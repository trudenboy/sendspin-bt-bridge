"""The one hardware reader: controllers, bonds and what the runtime knows.

Every consumer — the aggregate endpoint, the detail endpoints, guidance, the
future CLI — reads through :func:`read_inventory`.  One process-wide cache with
a short TTL keeps a page load from meaning a dozen blocking round trips, and one
lock keeps two callers from making the same trip at the same time.

Two rules decide where the data comes from:

* **D-Bus first, without the adapter lease.**  A read cannot corrupt another
  operation, so it must not queue behind a pairing.
* **``bluetoothctl`` only as the fallback, and only under the lease.**  It is a
  blocking command on the same single adapter agent, so a read that needs it
  waits for the lease rather than colliding with a scan.

When the last refresh failed, the previous read is still returned — with
``stale`` set and a degraded entry — instead of an empty page.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sendspin_bridge.bluetooth.adapter_map import hci_for
from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
from sendspin_bridge.bluetooth.bluez import Adapter, get_bluez
from sendspin_bridge.services.bluetooth import build_hci_map
from sendspin_bridge.services.bluetooth.bt_class_of_device import read_device_class
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.hardware.capabilities import (
    Capabilities,
    build_capabilities,
    classify_audio_server,
    probe_audio_server,
    probe_capture,
)
from sendspin_bridge.services.hardware.roles import DeviceLink, LinkEndpoint, RoleEvidence, infer_roles

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from sendspin_bridge.bluetooth.bluez import DeviceInfo
    from sendspin_bridge.services.audio.sink_inventory import LocalCard, LocalSink, LocalSource

logger = logging.getLogger(__name__)

__all__ = [
    "AudioFacts",
    "CaptureFacts",
    "ControllerFacts",
    "Degraded",
    "DeviceFacts",
    "EndpointFacts",
    "EndpointOwnerFacts",
    "HardwareUnavailable",
    "InventoryRead",
    "build_audio_facts",
    "classify_capture",
    "classify_owner",
    "clear_cache",
    "read_inventory",
]

_TTL_S = 2.0
_LEASE_WAIT_S = 5.0
_READ_LEASE_REASON = "hardware inventory"

_UUID_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
#: Any of the audio server's names for a Bluetooth endpoint; the address is the
#: same device in either spelling, so ``DeviceAddress`` folds it.
_BLUEZ_ENDPOINT_RE = re.compile(
    r"^bluez_(?:sink|output|source)\."
    r"(?P<address>[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}|_[0-9A-Fa-f]{2}){5})"
    r"(?:\..*)?$"
)
_BLUEZ_SOURCE_RE = re.compile(r"^bluez_source\.(?P<address>[0-9A-Fa-f_]{17})(?:\..*)?$")

#: One reader per process; the lock serialises refreshes and the cache is shared.
_reader_lock = threading.Lock()
_cached: InventoryRead | None = None
_cached_at = 0.0


class HardwareUnavailable(RuntimeError):
    """Neither D-Bus nor bluetoothctl could answer — the section has no truth to show."""


class _BusUnavailable(RuntimeError):
    """The reader could not answer at all — not the same as answering with nothing."""


@dataclass(frozen=True, slots=True)
class Degraded:
    """One section that could not be read in full, and the stable code that says why."""

    section: str
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ControllerFacts:
    """One physical Bluetooth adapter."""

    id: str
    id_synthetic: bool
    mac: str
    name: str
    alias: str
    powered: bool
    present: bool
    class_of_device: str | None
    device_macs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeviceFacts:
    """One paired or known Bluetooth device, merged across the adapters holding the bond."""

    mac: str
    name: str
    alias: str
    paired: bool | None
    bonded: bool | None
    trusted: bool | None
    blocked: bool | None
    connected: bool
    controller_macs: tuple[str, ...]
    uuids: tuple[str, ...]
    modalias: str | None
    services_resolved: bool | None
    battery_pct: int | None
    rssi_delta_db: int | None
    rssi_at: str | None
    transport_state: str | None
    roles: tuple[RoleEvidence, ...]


@dataclass(frozen=True, slots=True)
class EndpointOwnerFacts:
    """Which object owns an endpoint: a card, a Bluetooth link, or the bridge itself."""

    kind: Literal["card", "bluetooth", "internal"]
    card: str | None = None
    bluetooth: str | None = None
    internal: bool = False


@dataclass(frozen=True, slots=True)
class EndpointFacts:
    """One sink or source, already classified and ready for the read model."""

    kind: Literal["sink", "source"]
    name: str
    description: str
    state: str
    sample_spec: str
    muted: bool | None
    volume: int | None
    active_port: str | None
    active_port_available: str | None
    identifiers: dict[str, str]
    monitor_of: str | None
    owner: EndpointOwnerFacts | None


@dataclass(frozen=True, slots=True)
class CaptureFacts:
    """The phone's A2DP capture node, or the reason there is none."""

    kind: Literal["pipewire_node", "pulse_source", "unavailable"] = "unavailable"
    node: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AudioFacts:
    """The audio half of one refresh: cards, endpoints and the capture node.

    ``unavailable`` says the server answered but a list could not be read; an
    empty list with no entry here is a truthful "nothing is there".
    """

    cards: tuple[LocalCard, ...] = ()
    sinks: tuple[EndpointFacts, ...] = ()
    sources: tuple[EndpointFacts, ...] = ()
    capture: CaptureFacts = field(default_factory=CaptureFacts)
    #: The audio server's current default sink; ``None`` when it cannot say.
    default_sink: str | None = None
    unavailable: tuple[str, ...] = ()


def classify_owner(
    name: str,
    kind: Literal["sink", "source"],
    cards: Iterable[LocalCard],
    *,
    owner_module: int | None = None,
    device_name: str | None = None,
    monitor_owner: EndpointOwnerFacts | None = None,
) -> EndpointOwnerFacts | None:
    """Classify an endpoint's owner from its name, membership, then its properties.

    Bluetooth and bridge-internal names are self-describing.  A local endpoint
    belongs to the card whose endpoint list names it.  When the server omits
    that list (PipeWire never prints ``Sinks:``/``Sources:`` and reports no
    module), the ``device.name`` property is the card's ``Name:``, so that join
    is used next; a monitor source is resolved through the sink it monitors.
    The one-card-per-module fallback runs when neither property is present.

    Two property matches are ambiguous and stay ``None`` rather than guessing:
    an unknown owner is not the same as an internal or a chosen one.
    """
    from sendspin_bridge.bluetooth.address import DeviceAddress

    match = _BLUEZ_ENDPOINT_RE.match(name)
    if match is not None:
        address = DeviceAddress.parse(match.group("address"))
        if address is not None:
            return EndpointOwnerFacts(kind="bluetooth", bluetooth=address.colons)
    if name.startswith("sendspin_"):
        return EndpointOwnerFacts(kind="internal", internal=True)
    known_cards = list(cards)
    for card in known_cards:
        if name in (card.sink_names if kind == "sink" else card.source_names):
            return EndpointOwnerFacts(kind="card", card=card.name)
    if device_name:
        by_property = [card for card in known_cards if card.name == device_name]
        if len(by_property) == 1:
            return EndpointOwnerFacts(kind="card", card=by_property[0].name)
        if len(by_property) > 1:
            return None
    if owner_module is not None:
        by_module = [card for card in known_cards if card.owner_module == owner_module]
        if len(by_module) == 1:
            return EndpointOwnerFacts(kind="card", card=by_module[0].name)
    if kind == "source" and monitor_owner is not None:
        return monitor_owner
    return None


def _endpoint_facts(
    kind: Literal["sink", "source"],
    endpoint: LocalSink | LocalSource,
    cards: Iterable[LocalCard],
    *,
    monitor_owner: EndpointOwnerFacts | None = None,
) -> EndpointFacts:
    is_sink = kind == "sink"
    return EndpointFacts(
        kind=kind,
        name=endpoint.name,
        description=endpoint.description,
        state=endpoint.state,
        sample_spec=endpoint.sample_spec,
        muted=endpoint.muted if is_sink else None,
        volume=endpoint.volume if is_sink else None,
        active_port=getattr(endpoint, "active_port", None),
        active_port_available=getattr(endpoint, "active_port_available", None),
        identifiers=dict(endpoint.identifiers),
        monitor_of=None if is_sink else getattr(endpoint, "monitor_of", None),
        owner=classify_owner(
            endpoint.name,
            kind,
            cards,
            owner_module=endpoint.owner_module,
            device_name=getattr(endpoint, "device_name", None),
            monitor_owner=monitor_owner,
        ),
    )


def build_audio_facts(
    cards: Collection[LocalCard],
    sinks: Collection[LocalSink],
    sources: Collection[LocalSource],
    capture: CaptureFacts,
    *,
    default_sink: str | None = None,
    unavailable: tuple[str, ...] = (),
) -> AudioFacts:
    """Assemble the audio half from one read of each list (pure)."""
    sink_facts = tuple(_endpoint_facts("sink", sink, cards) for sink in sinks)
    by_sink_name = {facts.name: facts for facts in sink_facts}

    def _monitor_owner(source: LocalSource) -> EndpointOwnerFacts | None:
        sink = by_sink_name.get(source.monitor_of or "")
        if sink is None or sink.owner is None or sink.owner.kind != "card":
            return None
        return sink.owner

    source_facts = tuple(
        _endpoint_facts("source", source, cards, monitor_owner=_monitor_owner(source)) for source in sources
    )
    return AudioFacts(
        cards=tuple(cards),
        sinks=sink_facts,
        sources=source_facts,
        capture=capture,
        default_sink=default_sink,
        unavailable=unavailable,
    )


def classify_capture(
    wpctl_status: str | None,
    sources: Iterable[LocalSource],
    *,
    wpctl_available: bool,
    audio_server: str = "pipewire",
) -> CaptureFacts:
    """Find the phone's capture node, preferring the PipeWire node over a PA source.

    Neither existing is a truthful "unavailable" with a reason, not an error:
    a phone that is not streaming is not a fault.  The reason names the backend the
    host actually runs — a PulseAudio host has no ``wpctl`` by design, so blaming that
    would describe every quiet HAOS host as broken.
    """
    from sendspin_bridge.services.source.capture.pipewire import find_bluez_input

    node = find_bluez_input(wpctl_status or "")
    if node:
        return CaptureFacts(kind="pipewire_node", node=node)
    for source in sources:
        if _BLUEZ_SOURCE_RE.match(source.name):
            return CaptureFacts(kind="pulse_source", node=source.name)
    if audio_server == "pulseaudio":
        reason = "No bluez_source is present; the phone is not streaming into the bridge yet."
    elif not wpctl_available:
        reason = "wpctl is not installed, so no bluez_input node can be read; no bluez_source is present either."
    elif wpctl_status is None:
        reason = (
            "wpctl status could not be queried (it failed or timed out), so the phone's "
            "streaming state is unknown; no bluez_source is present either."
        )
    else:
        reason = "No bluez_input.<MAC>.<n> capture node and no bluez_source is present; the phone is not streaming."
    return CaptureFacts(kind="unavailable", node=None, reason=reason)


@dataclass(frozen=True, slots=True)
class InventoryRead:
    """One answer from the reader, cached for :data:`_TTL_S` seconds."""

    as_of: str
    stale: bool
    stale_after_s: int
    capabilities: Capabilities
    controllers: tuple[ControllerFacts, ...]
    devices: tuple[DeviceFacts, ...]
    degraded: tuple[Degraded, ...]
    audio: AudioFacts = field(default_factory=AudioFacts)


def _monotonic() -> float:
    return time.monotonic()


def _take_read_lease():
    """The adapter lease a ``bluetoothctl`` read must hold (it waits, never bypasses)."""
    return AdapterHandle().lease(_READ_LEASE_REASON, timeout=_LEASE_WAIT_S)


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=UTC).isoformat()


def _as_of() -> str:
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# What the runtime already knows
# ---------------------------------------------------------------------------


def _status_value(status: Any, key: str) -> Any:
    getter = getattr(status, "get", None)
    if callable(getter):
        return getter(key)
    return getattr(status, key, None)


def _runtime_status() -> dict[str, dict[str, Any]]:
    """Battery, RSSI and streaming state from the running clients, keyed by MAC.

    The status bag is where the RSSI refresh loop and the battery poll already
    write, so this costs no extra bus round trip.  An absent value stays
    ``None`` — devices are listed even when the runtime has nothing to add.
    """
    out: dict[str, dict[str, Any]] = {}
    snapshot = get_device_registry_snapshot()
    for client in snapshot.active_clients:
        manager = getattr(client, "bt_manager", None)
        raw_mac = getattr(manager, "mac_address", None)
        address = _canonical(raw_mac)
        if not address:
            continue
        status = getattr(client, "status", None) or {}
        battery = _status_value(status, "battery_level")
        rssi = _status_value(status, "rssi_dbm")
        rssi_at = _status_value(status, "rssi_at_ts")
        streaming = bool(_status_value(status, "audio_streaming"))
        out[address] = {
            "battery_pct": int(battery) if isinstance(battery, int) else None,
            "rssi_delta_db": rssi if isinstance(rssi, int) else None,
            "rssi_at": _iso(float(rssi_at)) if isinstance(rssi_at, int | float) else None,
            # An active transport is what BlueZ would report while audio flows;
            # the daemon's own streaming flag is the same moment, read for free.
            "transport_state": "active" if streaming else None,
        }
    return out


def _canonical(value: Any) -> str:
    from sendspin_bridge.bluetooth.address import DeviceAddress

    address = DeviceAddress.parse(value)
    return address.colons if address is not None else ""


# ---------------------------------------------------------------------------
# Reading through either transport
# ---------------------------------------------------------------------------


def _uuids(info: DeviceInfo | None) -> tuple[str, ...]:
    if info is None:
        return ()
    found: list[str] = []
    for line in info.raw:
        if not line.startswith("UUID:"):
            continue
        match = _UUID_RE.search(line)
        if match and match.group(1).lower() not in found:
            found.append(match.group(1).lower())
    return tuple(found)


def _modalias(info: DeviceInfo | None) -> str | None:
    if info is None:
        return None
    for line in info.raw:
        if line.startswith("Modalias:"):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def _controller_facts(ref: Any, info: Any, hci_by_mac: dict[str, str]) -> ControllerFacts | None:
    """One controller's facts, before its bonds are attached and its id is settled."""
    mac = _canonical(ref.mac)
    if not mac:
        return None
    hci = hci_for(hci_by_mac, mac) or ""
    name = (getattr(info, "name", "") or ref.name or hci or mac) if info is not None else (ref.name or mac)
    alias = (getattr(info, "alias", "") or name) if info is not None else name
    powered = bool(getattr(info, "powered", False)) if info is not None else False
    present = bool(getattr(info, "present", False)) if info is not None else True
    class_of_device = None
    if hci.startswith("hci") and hci[3:].isdigit():
        raw_class = read_device_class(int(hci[3:]))
        if raw_class is not None:
            class_of_device = f"0x{raw_class:06x}"
    return ControllerFacts(
        id=hci,
        id_synthetic=not hci,
        mac=mac,
        name=name,
        alias=alias,
        powered=powered,
        present=present,
        class_of_device=class_of_device,
        device_macs=(),
    )


def _with_unique_ids(controllers: list[ControllerFacts]) -> list[ControllerFacts]:
    """Give every controller an id no other controller holds.

    A controller the sysfs map does not know has no ``hciN`` of its own.  The
    synthetic one is the first free kernel name, never an index — with a partial
    sysfs map an index would mint a name a real adapter already owns, and
    ``/hardware/controllers/hciN`` would then resolve the wrong adapter.
    """
    used = {facts.id for facts in controllers if facts.id}
    next_free = 0
    settled: list[ControllerFacts] = []
    for facts in controllers:
        if facts.id:
            settled.append(facts)
            continue
        while f"hci{next_free}" in used:
            next_free += 1
        identifier = f"hci{next_free}"
        used.add(identifier)
        next_free += 1
        settled.append(replace(facts, id=identifier, id_synthetic=True))
    return settled


def _device_facts(
    mac: str,
    record: dict[str, Any],
    info: DeviceInfo | None,
    runtime: dict[str, dict[str, Any]],
    endpoints: Iterable[LinkEndpoint] = (),
) -> DeviceFacts:
    """One bond, merged across the controllers that hold it."""
    entry = record["entry"]
    name = (getattr(info, "name", "") or getattr(entry, "name", "") or mac) if info else (entry.name or mac)
    alias = (getattr(info, "alias", "") or name) if info else name
    status = runtime.get(mac, {})
    transport_state = status.get("transport_state")
    roles = infer_roles(DeviceLink(mac, transport_state=transport_state), endpoints, None)
    return DeviceFacts(
        mac=mac,
        name=name,
        alias=alias,
        paired=getattr(info, "paired", None),
        bonded=getattr(info, "bonded", None),
        trusted=getattr(info, "trusted", None),
        blocked=getattr(info, "blocked", None),
        connected=bool(getattr(info, "connected", False)),
        controller_macs=tuple(record["controller_macs"]),
        uuids=_uuids(info),
        modalias=_modalias(info),
        services_resolved=None,
        battery_pct=status.get("battery_pct"),
        rssi_delta_db=status.get("rssi_delta_db"),
        rssi_at=status.get("rssi_at"),
        transport_state=transport_state,
        roles=tuple(roles),
    )


def _read_from(
    reader: Any, hci_by_mac: dict[str, str], endpoints: Iterable[LinkEndpoint] = ()
) -> tuple[list[ControllerFacts], list[DeviceFacts]]:
    """One full read through *reader* (a D-Bus backend or the bluetoothctl transport).

    A batched ``inventory()`` answer is consumed in one go; duck-typed readers
    that only answer the individual questions still work.  ``inventory()``
    returning ``None`` — or a question answering ``None`` — raises
    :class:`_BusUnavailable`; an empty list is a truthful "no controllers".
    """
    batched = getattr(reader, "inventory", None)
    if batched is not None:
        readings = batched()
        if readings is None:
            raise _BusUnavailable("the reader could not answer")
        return _from_readings(readings, hci_by_mac, endpoints)
    return _from_questions(reader, hci_by_mac, endpoints)


def _from_readings(
    readings: list[Any], hci_by_mac: dict[str, str], endpoints: Iterable[LinkEndpoint] = ()
) -> tuple[list[ControllerFacts], list[DeviceFacts]]:
    runtime = _runtime_status()
    controllers: list[ControllerFacts] = []
    merged: dict[str, dict[str, Any]] = {}
    for reading in readings:
        facts = _controller_facts(reading.ref, reading.info, hci_by_mac)
        if facts is None:
            continue
        device_macs: list[str] = []
        for entry in reading.devices:
            device_mac = _canonical(entry.mac)
            if not device_mac:
                continue
            device_macs.append(device_mac)
            record = merged.setdefault(device_mac, {"entry": entry, "controller_macs": [], "info": None})
            if facts.mac not in record["controller_macs"]:
                record["controller_macs"].append(facts.mac)
        for info in reading.device_infos:
            device_mac = _canonical(info.mac)
            target = merged.get(device_mac)
            if target is not None and target["info"] is None:
                target["info"] = info
        controllers.append(replace(facts, device_macs=tuple(device_macs)))
    devices = [_device_facts(mac, record, record.get("info"), runtime, endpoints) for mac, record in merged.items()]
    return _with_unique_ids(controllers), devices


def _from_questions(
    reader: Any, hci_by_mac: dict[str, str], endpoints: Iterable[LinkEndpoint] = ()
) -> tuple[list[ControllerFacts], list[DeviceFacts]]:
    """The legacy per-question read, for a reader without a batched answer."""
    adapters = reader.list_adapters()
    if adapters is None:
        raise _BusUnavailable("the bus could not answer")
    runtime = _runtime_status()
    controllers: list[ControllerFacts] = []
    merged: dict[str, dict[str, Any]] = {}
    for ref in adapters:
        info = reader.show(Adapter.addressed(ref.mac))
        facts = _controller_facts(ref, info, hci_by_mac)
        if facts is None:
            continue
        device_macs: list[str] = []
        for entry in reader.list_devices(Adapter.select(facts.mac), filter="Paired") or []:
            device_mac = _canonical(entry.mac)
            if not device_mac:
                continue
            device_macs.append(device_mac)
            record = merged.setdefault(device_mac, {"entry": entry, "controller_macs": [], "info": None})
            if facts.mac not in record["controller_macs"]:
                record["controller_macs"].append(facts.mac)
        controllers.append(replace(facts, device_macs=tuple(device_macs)))
    for mac, record in merged.items():
        owner = record["controller_macs"][0]
        record["info"] = reader.device_info(mac, Adapter.select(owner))
    devices = [_device_facts(mac, record, record.get("info"), runtime, endpoints) for mac, record in merged.items()]
    return _with_unique_ids(controllers), devices


# ---------------------------------------------------------------------------
# The audio half
# ---------------------------------------------------------------------------


async def _wpctl_status() -> str | None:
    """``wpctl status`` output, or ``None`` when it could not be run."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "wpctl",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (OSError, TimeoutError) as exc:
        logger.debug("wpctl status failed: %s", exc)
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return None
    if proc.returncode not in (0, None):
        return None
    return out.decode(errors="replace")


def _wpctl_available() -> bool:
    """Whether the PipeWire tools are installed; one seam for tests and the reason code."""
    import shutil

    return shutil.which("wpctl") is not None


async def _capture_facts(sources: Collection[LocalSource], server_name: str) -> CaptureFacts:
    # ``wpctl`` presence is a PipeWire fact; ``probe_capture`` is the host's overall answer
    # (PulseAudio needs pactl/parecord instead).  Keep them apart: the classifier reasons
    # about the PipeWire node even on a PulseAudio host.
    wpctl_available = _wpctl_available()
    status = await _wpctl_status() if wpctl_available else None
    return classify_capture(
        status,
        sources,
        wpctl_available=wpctl_available,
        audio_server=classify_audio_server(server_name),
    )


async def _read_audio_async(server_name: str) -> AudioFacts:
    """One read of each audio list, then one assembly (pure).

    Each list is read strictly so a failed ``pactl list`` is remembered as
    unavailable instead of becoming an empty list; the lists that did answer
    are still returned, so one broken read does not hide the others.
    """
    from sendspin_bridge.services.audio import pulse, sink_inventory
    from sendspin_bridge.services.audio.sink_inventory import SinkInventoryUnavailable

    unavailable: list[str] = []
    sinks: list[LocalSink] = []
    sources: list[LocalSource] = []
    cards: list[LocalCard] = []
    try:
        sinks = await sink_inventory.list_all_sinks(strict=True)
    except SinkInventoryUnavailable as exc:
        unavailable.append(f"sinks: {exc}")
    try:
        sources = await sink_inventory.list_local_sources(strict=True)
    except SinkInventoryUnavailable as exc:
        unavailable.append(f"sources: {exc}")
    try:
        cards = await sink_inventory.list_local_cards(sinks=sinks, sources=sources, strict=True)
    except SinkInventoryUnavailable as exc:
        unavailable.append(f"cards: {exc}")
    capture = await _capture_facts(sources, server_name)
    default_sink = await pulse.aget_default_sink_name()
    return build_audio_facts(cards, sinks, sources, capture, default_sink=default_sink, unavailable=tuple(unavailable))


def _read_audio(server_name: str) -> AudioFacts:
    """The audio half for a worker thread; empty when no server answers.

    The async readers use ``asyncio`` subprocesses and are bridged with
    ``asyncio.run`` because this reader is synchronous by contract and always
    runs in a worker thread.  A server that does not answer reads as empty;
    a server that answered but whose lists failed keeps the failure in
    ``unavailable`` so the API can say why instead of "no audio".
    """
    if classify_audio_server(server_name) == "none":
        return AudioFacts()
    try:
        return asyncio.run(_read_audio_async(server_name))
    except Exception as exc:
        logger.debug("Hardware inventory: audio read failed (%s)", exc)
        return AudioFacts(unavailable=(f"the audio read failed: {exc}",))


def _role_endpoints(audio: AudioFacts) -> tuple[LinkEndpoint, ...]:
    """The endpoints and capture node role inference reads, in one tuple."""
    endpoints = [LinkEndpoint("sink", sink.name, sink.state) for sink in audio.sinks]
    endpoints.extend(LinkEndpoint("source", source.name, source.state) for source in audio.sources)
    if audio.capture.node:
        endpoints.append(LinkEndpoint("capture", audio.capture.node, ""))
    return tuple(endpoints)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _assemble(
    controllers: list[ControllerFacts],
    devices: list[DeviceFacts],
    *,
    dbus: bool,
    bluetooth: bool,
    degraded: list[Degraded],
    audio: AudioFacts | None = None,
    server_name: str | None = None,
) -> InventoryRead:
    resolved_name = probe_audio_server() if server_name is None else server_name
    audio_facts = audio if audio is not None else AudioFacts()
    audio_server = classify_audio_server(resolved_name)
    capabilities = build_capabilities(
        bluetooth=bluetooth,
        dbus=dbus,
        server_name=resolved_name,
        capture=probe_capture(resolved_name),
        default_sink=audio_facts.default_sink is not None,
    )
    entries = list(degraded)
    if audio_server == "none":
        entries.append(
            Degraded(
                "audio",
                "pulse_unavailable",
                "No PulseAudio-compatible server answered; cards, sinks and sources cannot be read.",
            )
        )
    elif audio_facts.unavailable:
        entries.append(
            Degraded(
                "audio",
                "pulse_unavailable",
                "The audio server answered, but its inventory could not be fully read: "
                + "; ".join(audio_facts.unavailable),
            )
        )
    elif audio_facts.capture.kind == "unavailable" and not capabilities.capture.available:
        # A phone that is not streaming is a quiet host, not a broken one: the source strip
        # says "waiting for the phone".  Only a host that cannot capture at all degrades.
        entries.append(
            Degraded(
                "audio",
                "capture_unavailable",
                audio_facts.capture.reason or "No Bluetooth capture node or source is present.",
            )
        )
    return InventoryRead(
        as_of=_as_of(),
        stale=False,
        stale_after_s=int(_TTL_S),
        capabilities=capabilities,
        controllers=tuple(controllers),
        devices=tuple(devices),
        degraded=tuple(entries),
        audio=audio_facts,
    )


def _refresh() -> InventoryRead:
    server_name = probe_audio_server()
    audio = _read_audio(server_name)
    endpoints = _role_endpoints(audio)
    bluez = get_bluez()
    hci_by_mac = build_hci_map()
    backend = getattr(bluez, "query_backend", None)
    if backend is not None:
        try:
            controllers, devices = _read_from(backend, hci_by_mac, endpoints)
        except _BusUnavailable as exc:
            logger.debug("Hardware inventory: D-Bus could not answer (%s); asking bluetoothctl", exc)
        except Exception as exc:  # the bus could not be asked — the transport decides
            logger.debug("Hardware inventory: D-Bus read failed (%s); asking bluetoothctl", exc)
        else:
            return _assemble(
                controllers, devices, dbus=True, bluetooth=True, degraded=[], audio=audio, server_name=server_name
            )

    # The bus already said no — ask bluetoothctl without letting every verb
    # spend the same bus timeout before it answers.
    reader = bluez.without_query_backend() if backend is not None else bluez
    with _take_read_lease():
        try:
            controllers, devices = _read_from(reader, hci_by_mac, endpoints)
        except _BusUnavailable as exc:
            raise HardwareUnavailable(f"Neither D-Bus nor bluetoothctl could answer: {exc}") from exc
    return _assemble(
        controllers,
        devices,
        dbus=False,
        bluetooth=True,
        degraded=[
            Degraded(
                "controllers",
                "dbus_unavailable",
                "D-Bus did not answer; the inventory was read through bluetoothctl under the adapter lease.",
            )
        ],
        audio=audio,
        server_name=server_name,
    )


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


def read_inventory() -> InventoryRead:
    """The current hardware inventory, read at most once per :data:`_TTL_S` seconds."""
    global _cached, _cached_at
    with _reader_lock:
        now = _monotonic()
        if _cached is not None and now - _cached_at < _TTL_S:
            return _cached
        try:
            fresh = _refresh()
        except Exception as exc:
            if _cached is None:
                if isinstance(exc, HardwareUnavailable):
                    raise
                raise HardwareUnavailable(str(exc)) from exc
            logger.warning("Hardware inventory refresh failed; serving the last read: %s", exc)
            return replace(
                _cached,
                stale=True,
                degraded=(
                    *_cached.degraded,
                    Degraded("hardware", "refresh_failed", f"The last hardware refresh failed: {exc}"),
                ),
            )
        _cached = fresh
        _cached_at = _monotonic()
        return fresh


def clear_cache() -> None:
    """Forget the cached read (a manual refresh, and every test that needs a clean slate)."""
    global _cached, _cached_at
    with _reader_lock:
        _cached = None
        _cached_at = 0.0
