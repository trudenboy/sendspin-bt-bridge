"""Hardware use cases: the read model joined with the configuration.

Two things are added here and nowhere else: the player a bond backs and the
Home Assistant area an adapter is mapped to.  Both come from ``load_config()``
in a worker thread — the reader is blocking, and a request handler is not where
a config migration should run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from sendspin_bridge.application import source as source_uc
from sendspin_bridge.application.errors import not_found, require_audio_name, unavailable
from sendspin_bridge.application.models.hardware import (
    AddressForms,
    AreaRef,
    AudioSection,
    BtDeviceDetail,
    BtDeviceSummary,
    CapabilitySet,
    CardDetail,
    CardPort,
    CardProfile,
    CardSummary,
    ControllerDetail,
    ControllerSummary,
    DegradedSection,
    EndpointDetail,
    EndpointOwner,
    EndpointRef,
    EndpointSummary,
    HardwareSnapshot,
    RoleEvidence,
    RssiSummary,
    SourceCapture,
    SourceFeed,
    SourceMa,
)
from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.vendor_map import vendor_from_modalias
from sendspin_bridge.config import load_config
from sendspin_bridge.config.players import player_id_for_mac
from sendspin_bridge.services.hardware.inventory import (
    AudioFacts,
    ControllerFacts,
    DeviceFacts,
    EndpointFacts,
    EndpointOwnerFacts,
    HardwareUnavailable,
    InventoryRead,
    read_inventory,
)
from sendspin_bridge.services.hardware.roles import CAPTURE_NAME_RE, SOURCE_NAME_RE
from sendspin_bridge.services.source import manager as source_manager

if TYPE_CHECKING:
    from sendspin_bridge.services.audio.sink_inventory import CardPort as LocalCardPort
    from sendspin_bridge.services.audio.sink_inventory import LocalCard
    from sendspin_bridge.services.hardware.capabilities import Capabilities

_BLUETOOTH_BACKEND = "bluetooth_a2dp"
_PULSE_SINK_BACKEND = "pulse_sink"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def _inventory() -> InventoryRead:
    try:
        return await asyncio.to_thread(read_inventory)
    except HardwareUnavailable as exc:
        raise unavailable(str(exc), code="hardware_unavailable") from exc


async def _config() -> dict[str, Any]:
    return await asyncio.to_thread(load_config)


async def _read() -> tuple[InventoryRead, dict[str, Any]]:
    return await asyncio.gather(_inventory(), _config())


# ---------------------------------------------------------------------------
# The config join
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    address = DeviceAddress.parse(value)
    return address.colons if address is not None else ""


def _config_view(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, AreaRef], dict[str, str]]:
    """``{mac: player_id}``, ``{mac: area}`` and ``{sink: player_id}`` from one config scan."""
    players: dict[str, str] = {}
    sink_players: dict[str, str] = {}
    for player in config.get("players") or []:
        backend = player.get("backend") if isinstance(player, dict) else None
        if not isinstance(player, dict) or not isinstance(backend, dict):
            continue
        if backend.get("type") == _BLUETOOTH_BACKEND:
            mac = _canonical(backend.get("mac"))
            if not mac:
                continue
            players[mac] = str(player.get("id") or player_id_for_mac(mac))
        elif backend.get("type") == _PULSE_SINK_BACKEND:
            sink = backend.get("sink")
            player_id = str(player.get("id") or "").strip()
            if isinstance(sink, str) and sink and player_id:
                sink_players.setdefault(sink, player_id)

    areas: dict[str, AreaRef] = {}
    raw_areas = config.get("HA_ADAPTER_AREA_MAP")
    if isinstance(raw_areas, dict):
        for raw_mac, entry in raw_areas.items():
            mac = _canonical(raw_mac)
            if not mac or not isinstance(entry, dict):
                continue
            area_id = str(entry.get("area_id") or "").strip()
            if not area_id:
                continue
            raw_name = entry.get("area_name")
            areas[mac] = AreaRef(id=area_id, name=str(raw_name) if raw_name else None)
    return players, areas, sink_players


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def _capabilities(capabilities: Capabilities) -> CapabilitySet:
    return CapabilitySet(
        bluetooth=capabilities.bluetooth.available,
        dbus=capabilities.dbus.available,
        audio_server=capabilities.audio_server,
        capture=capabilities.capture.available,
        default_sink=capabilities.default_sink.available,
        reasons=capabilities.reasons,
    )


def _rssi(facts: DeviceFacts) -> RssiSummary | None:
    if facts.rssi_delta_db is None:
        return None
    return RssiSummary(delta_db=facts.rssi_delta_db, at=facts.rssi_at)


def _device_summary(facts: DeviceFacts, players: dict[str, str], source: SourceFeed | None = None) -> BtDeviceSummary:
    roles = [RoleEvidence(role=role.role, evidence=role.evidence) for role in facts.roles]
    source_phone = source is not None and source.phone_mac == facts.mac
    # The source feed is evidence in its own right, but the capture node (when
    # the inventory found one) is the more specific proof and already claimed
    # the role; only a device with no input evidence gets ``source_feed:<mac>``.
    if source_phone and not any(role.role == "input" for role in roles):
        roles.append(RoleEvidence(role="input", evidence=f"source_feed:{facts.mac}"))
    return BtDeviceSummary(
        mac=facts.mac,
        name=facts.name,
        alias=facts.alias,
        paired=facts.paired,
        bonded=facts.bonded,
        trusted=facts.trusted,
        connected=facts.connected,
        controller_macs=list(facts.controller_macs),
        battery_pct=facts.battery_pct,
        rssi=_rssi(facts),
        # The transport's codec and delay are E2's audio half; the role evidence
        # already carries the one transport fact this stage consumes.
        transport=None,
        roles=roles,
        player_id=players.get(facts.mac),
        source_phone=source_phone,
    )


def _area(facts: ControllerFacts, areas: dict[str, AreaRef]) -> AreaRef | None:
    return areas.get(facts.mac)


def _controller_summary(facts: ControllerFacts, areas: dict[str, AreaRef]) -> ControllerSummary:
    return ControllerSummary(
        id=facts.id,
        id_synthetic=facts.id_synthetic,
        mac=facts.mac,
        name=facts.name,
        alias=facts.alias,
        powered=facts.powered,
        present=facts.present,
        class_of_device=facts.class_of_device,
        area=_area(facts, areas),
        device_count=len(facts.device_macs),
    )


def _address_forms(mac: str) -> AddressForms:
    address = DeviceAddress.parse(mac)
    if address is None:  # the reader only produces canonical addresses
        return AddressForms(colons=mac, underscores=mac, bare=mac)
    return AddressForms(colons=address.colons, underscores=address.underscores, bare=address.bare)


def _degraded(read: InventoryRead) -> list[DegradedSection]:
    return [
        DegradedSection(section=section.section, code=section.code, detail=section.detail) for section in read.degraded
    ]


# ---------------------------------------------------------------------------
# The source feed
# ---------------------------------------------------------------------------


def _phone_mac(node: str | None, known_macs: Collection[str]) -> str | None:
    """The MAC inside a capture node's name, when it is a device this bridge knows.

    A node name is the audio server's spelling, not proof that the peer is one
    of our devices: only a MAC that is in the inventory (a paired/bonded
    device) may claim the phone role.
    """
    if not node:
        return None
    for pattern in (CAPTURE_NAME_RE, SOURCE_NAME_RE):
        match = pattern.match(node.strip())
        if match is not None:
            address = DeviceAddress.parse(match.group("address"))
            if address is not None and address.colons in known_macs:
                return address.colons
            return None
    return None


def _source_state(view: source_uc.SourceStatus) -> Literal["off", "pairing", "waiting", "streaming", "error"]:
    """The source's own state name; the model keeps the same literals."""
    return view.state  # type: ignore[return-value]


def _source_feed(config: dict[str, Any], audio: AudioFacts, known_macs: Collection[str]) -> SourceFeed | None:
    """The source as the page shows it, or ``None`` when the feature is off.

    ``None`` is "this bridge does not report a source", not an empty feed: the
    SPA hides the strip instead of claiming a stopped phone that never existed.
    A source enabled through the ``SENDSPIN_SOURCE`` environment variable runs
    without the config key, so the running manager is evidence too.
    """
    if not config.get("SENDSPIN_SOURCE") and not source_manager.running():
        return None
    view = source_uc.status(config)
    return SourceFeed(
        enabled=view.enabled,
        state=_source_state(view),
        phone_mac=_phone_mac(view.phone, known_macs),
        paired_phone_mac=view.paired_phone_mac,
        capture=SourceCapture(kind=audio.capture.kind, node=audio.capture.node),
        ma=SourceMa(
            server=view.server,
            connected=view.ma_connected,
            paired=view.ma_paired,
            pairing_required=view.ma_pairing_required,
            pairing_code=view.ma_pairing_code,
            pairing_seconds_left=view.ma_pairing_seconds_left,
            error=view.ma_error,
        ),
        error=view.error,
    )


# ---------------------------------------------------------------------------
# The audio half
# ---------------------------------------------------------------------------


def _owner(facts: EndpointOwnerFacts | None) -> EndpointOwner | None:
    if facts is None:
        return None
    if facts.kind == "card":
        return EndpointOwner(card=facts.card)
    if facts.kind == "bluetooth":
        return EndpointOwner(bluetooth=facts.bluetooth)
    return EndpointOwner(internal=facts.internal)


def _endpoint_summary(
    facts: EndpointFacts, sink_players: dict[str, str], default_sink: str | None = None
) -> EndpointSummary:
    return EndpointSummary(
        kind=facts.kind,
        name=facts.name,
        description=facts.description,
        owner=_owner(facts.owner),
        state=facts.state,
        sample_spec=facts.sample_spec,
        muted=facts.muted,
        volume=facts.volume,
        in_use_by=sink_players.get(facts.name) if facts.kind == "sink" else None,
        active_port=facts.active_port,
        active_port_available=facts.active_port_available,
        is_default=facts.kind == "sink" and default_sink is not None and facts.name == default_sink,
    )


def _audio_endpoints(audio: AudioFacts) -> list[EndpointFacts]:
    return [*audio.sinks, *audio.sources]


def _owned_by_card(facts: EndpointFacts, card: LocalCard) -> bool:
    owner = facts.owner
    return owner is not None and owner.kind == "card" and owner.card == card.name


def _card_endpoints(card: LocalCard, endpoints: list[EndpointFacts]) -> list[EndpointFacts]:
    """The endpoints belonging to *card*: its own lists first, then classified members.

    Branch membership and endpoint ownership are different facts: a Bluetooth
    sink belongs to its ``bluez_card`` (the card lists it) while its owner is
    the speaker link.  The classified fallback covers hosts whose ``pactl``
    omits the card's endpoint lists.
    """
    by_name = {facts.name: facts for facts in endpoints}
    members: list[EndpointFacts] = []
    seen: set[str] = set()
    for kind, names in (("sink", card.sink_names), ("source", card.source_names)):
        for name in names:
            facts = by_name.get(name)
            if facts is not None and facts.kind == kind and facts.name not in seen:
                members.append(facts)
                seen.add(facts.name)
    for facts in endpoints:
        if facts.name not in seen and _owned_by_card(facts, card):
            members.append(facts)
            seen.add(facts.name)
    return members


def _card_summary(card: LocalCard, endpoints: list[EndpointFacts]) -> CardSummary:
    return CardSummary(
        name=card.name,
        driver=card.driver,
        active_profile=card.active_profile,
        profile_count=len(card.profiles),
        port_count=len(card.ports),
        endpoint_refs=[EndpointRef(kind=facts.kind, name=facts.name) for facts in _card_endpoints(card, endpoints)],
        managed_by=managed_by(card),
    )


def managed_by(card: LocalCard) -> Literal["bridge"] | None:
    """Who chooses this card's profile: the bridge's Bluetooth link, or the operator.

    This is a fact about the card itself, never about its endpoints: PipeWire
    does not print a card's ``Sinks:``/``Sources:`` lists, so a ``bluez_card``
    there has no endpoints to classify and the sink's owner is the speaker
    link.  BlueZ names its cards ``bluez_card.<MAC>`` on both servers; its
    classic PulseAudio driver (``module-bluez5-device.c``) is the second tell.
    """
    if card.name.startswith("bluez_card."):
        return "bridge"
    if "bluez" in (card.driver or "").lower():
        return "bridge"
    return None


_AUDIO_LIST_KINDS = frozenset({"cards", "sinks", "sources"})


def audio_list_failure(audio: AudioFacts, kind: Literal["cards", "sinks", "sources"]) -> str | None:
    """Why *kind*'s list is unknown, or ``None`` when it was read (even if empty).

    The reader reports a list it could not read in ``AudioFacts.unavailable``;
    an empty list with no entry there is a truthful "nothing is there".  A 404
    for a name that was never read would invent an answer the server did not
    give.  An entry that names no list at all (the whole audio read failed) is
    evidence against every list.
    """
    prefix = f"{kind}:"
    reasons: list[str] = []
    for entry in audio.unavailable:
        if entry.startswith(prefix):
            reasons.append(entry[len(prefix) :].strip())
        elif entry.split(":", 1)[0] not in _AUDIO_LIST_KINDS:
            reasons.append(entry.strip())
    return "; ".join(reason for reason in reasons if reason) or None


def _card_profile(profile: Any) -> CardProfile:
    return CardProfile(name=profile.name, description=profile.description, available=profile.available)


def _card_port(port: LocalCardPort) -> CardPort:
    return CardPort(
        name=port.name,
        description=port.description,
        available=port.available,
        active=port.active,
        profiles=list(port.profiles),
    )


def _audio_section(audio: AudioFacts, sink_players: dict[str, str]) -> AudioSection:
    endpoints = _audio_endpoints(audio)
    return AudioSection(
        cards=[_card_summary(card, endpoints) for card in audio.cards],
        sinks=[_endpoint_summary(facts, sink_players, audio.default_sink) for facts in audio.sinks],
        sources=[_endpoint_summary(facts, sink_players, audio.default_sink) for facts in audio.sources],
    )


def endpoint_card(facts: EndpointFacts, cards: tuple[LocalCard, ...]) -> LocalCard | None:
    """The card whose ports describe this endpoint, even when its owner is a link.

    This is the read model's owner classification: ``owner.card`` first, then
    the card's own endpoint list.  A Bluetooth sink is owned by the speaker
    link but still appears in its ``bluez_card``'s ``Sinks:`` list, and that
    card is what carries the ports an operator can switch.
    """
    if facts.owner is not None and facts.owner.kind == "card":
        owned = next((card for card in cards if card.name == facts.owner.card), None)
        if owned is not None:
            return owned
    for card in cards:
        names = card.sink_names if facts.kind == "sink" else card.source_names
        if facts.name in names:
            return card
    return None


def build_card_detail(
    card: LocalCard,
    audio: AudioFacts,
    config: dict[str, Any],
    *,
    as_of: str,
    stale: bool,
    active_profile: str | None = None,
) -> CardDetail:
    """A card's detail from one read, joined with the config (pure).

    Shared by the read route and by the profile command, so a command's
    response is the same shape a second read would have produced.  The command
    passes the profile it just set; the read passes nothing, so the card's own
    ``active_profile`` stands.
    """
    if active_profile is not None:
        card = replace(card, active_profile=active_profile)
    _players, _areas, sink_players = _config_view(config)
    endpoints = _audio_endpoints(audio)
    return CardDetail(
        **_card_summary(card, endpoints).model_dump(),
        profiles=[_card_profile(profile) for profile in card.profiles],
        ports=[_card_port(port) for port in card.ports],
        # The same membership the summary uses: a bluez_card lists its Bluetooth
        # sink and source even though their *owner* is the speaker link.
        endpoints=[
            _endpoint_summary(facts, sink_players, audio.default_sink) for facts in _card_endpoints(card, endpoints)
        ],
        as_of=as_of,
        stale=stale,
    )


def build_endpoint_detail(
    facts: EndpointFacts,
    audio: AudioFacts,
    config: dict[str, Any],
    *,
    as_of: str,
    stale: bool,
    active_port: str | None = None,
) -> EndpointDetail:
    """A sink's or source's detail from one read, joined with the config (pure).

    A port command passes the port it just set: the read behind the answer can
    still mark the old port active (or be the read from before the write), so
    the marker is rebuilt from the choice rather than copied from that read.
    """
    _players, _areas, sink_players = _config_view(config)
    card = endpoint_card(facts, audio.cards)
    ports = card.ports if card is not None else ()
    if active_port is not None:
        ports = tuple(replace(port, active=port.name == active_port) for port in ports)
    return EndpointDetail(
        **_endpoint_summary(facts, sink_players, audio.default_sink).model_dump(),
        identifiers=dict(facts.identifiers),
        ports=[_card_port(port) for port in ports],
        monitor_of=facts.monitor_of,
        as_of=as_of,
        stale=stale,
    )


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


async def snapshot() -> HardwareSnapshot:
    """One aggregate read of the physical layer."""
    read, config = await _read()
    players, areas, sink_players = _config_view(config)
    source = _source_feed(config, read.audio, {device.mac for device in read.devices})
    return HardwareSnapshot(
        as_of=read.as_of,
        stale_after_s=read.stale_after_s,
        stale=read.stale,
        capabilities=_capabilities(read.capabilities),
        controllers=[_controller_summary(facts, areas) for facts in read.controllers],
        bluetooth_devices=[_device_summary(facts, players, source) for facts in read.devices],
        audio=_audio_section(read.audio, sink_players),
        source=source,
        degraded=_degraded(read),
    )


def _find_controller(controllers: tuple[ControllerFacts, ...], controller_id: str) -> ControllerFacts | None:
    wanted = (controller_id or "").strip()
    wanted_mac = _canonical(wanted)
    for facts in controllers:
        if facts.id.lower() == wanted.lower() or (wanted_mac and facts.mac == wanted_mac):
            return facts
    return None


async def controller(controller_id: str) -> ControllerDetail:
    """One adapter with its bonds, by ``hciN`` or by controller MAC."""
    read, config = await _read()
    facts = _find_controller(read.controllers, controller_id)
    if facts is None:
        raise not_found(f"Unknown controller: {controller_id}", code="entity_not_found")
    players, areas, _sink_players = _config_view(config)
    by_mac = {device.mac: device for device in read.devices}
    source = _source_feed(config, read.audio, set(by_mac))
    return ControllerDetail(
        **_controller_summary(facts, areas).model_dump(),
        devices=[_device_summary(by_mac[mac], players, source) for mac in facts.device_macs if mac in by_mac],
        address_forms=_address_forms(facts.mac),
    )


async def bluetooth_device(mac: str) -> BtDeviceDetail:
    """One Bluetooth device with the detail-only fields."""
    address = DeviceAddress.parse(mac)
    if address is None:
        raise not_found(f"Unknown Bluetooth device: {mac}", code="entity_not_found")
    read, config = await _read()
    facts = next((device for device in read.devices if device.mac == address.colons), None)
    if facts is None:
        raise not_found(f"Unknown Bluetooth device: {mac}", code="entity_not_found")
    players, _areas, _sink_players = _config_view(config)
    summary = _device_summary(facts, players, _source_feed(config, read.audio, {device.mac for device in read.devices}))
    return BtDeviceDetail(
        **summary.model_dump(),
        uuids=list(facts.uuids),
        services_resolved=facts.services_resolved,
        modalias=facts.modalias,
        manufacturer=vendor_from_modalias(facts.modalias),
        bonds=list(facts.controller_macs),
    )


async def audio_card(name: str) -> CardDetail:
    """One card with every profile, every port and its endpoints."""
    wanted = require_audio_name(name, field="card name")
    read, config = await _read()
    card = next((item for item in read.audio.cards if item.name == wanted), None)
    if card is None:
        reason = audio_list_failure(read.audio, "cards")
        if reason is not None:
            raise unavailable(f"The card list could not be read: {reason}", code="hardware_unavailable")
        raise not_found(f"Unknown audio card: {name}", code="entity_not_found")
    return build_card_detail(card, read.audio, config, as_of=read.as_of, stale=read.stale)


async def _audio_endpoint(kind: Literal["sink", "source"], name: str) -> EndpointDetail:
    wanted = require_audio_name(name, field=f"{kind} name")
    read, config = await _read()
    pool = read.audio.sinks if kind == "sink" else read.audio.sources
    facts = next((item for item in pool if item.name == wanted), None)
    if facts is None:
        list_kind: Literal["sinks", "sources"] = "sinks" if kind == "sink" else "sources"
        reason = audio_list_failure(read.audio, list_kind)
        if reason is not None:
            raise unavailable(f"The {kind} list could not be read: {reason}", code="hardware_unavailable")
        raise not_found(f"Unknown audio {kind}: {name}", code="entity_not_found")
    return build_endpoint_detail(facts, read.audio, config, as_of=read.as_of, stale=read.stale)


async def audio_sink(name: str) -> EndpointDetail:
    """One sink with its stable identifiers and the owning card's ports."""
    return await _audio_endpoint("sink", name)


async def audio_source(name: str) -> EndpointDetail:
    """One source, including the sink it monitors when it is a monitor source."""
    return await _audio_endpoint("source", name)
