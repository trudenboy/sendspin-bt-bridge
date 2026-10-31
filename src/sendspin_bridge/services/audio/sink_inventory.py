"""Inventory of local (non-Bluetooth) PulseAudio/PipeWire sinks.

Lists sinks together with the stable hardware identifiers a ``pulse_sink``
player stores in its config, and matches those identifiers back to the sink
that is currently present.  Sink *names* can change across replugs and
reboots; the identifiers are tried first and the stored name is the last resort.

Source split follows ``pulse.py``: ``pulsectl_asyncio`` first, ``pactl list
sinks`` subprocess as fallback.  ``parse_pactl_sinks`` is pure.  Cards and
sources are read through ``pactl`` because only its text form carries the
card's endpoint index lists; the parsers are pure and fixture-testable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Hashable, Mapping

from sendspin_bridge.services.audio import pulse
from sendspin_bridge.services.audio.pulse import STANDBY_SINK_NAME

logger = logging.getLogger(__name__)

__all__ = [
    "CardPort",
    "CardProfile",
    "LocalCard",
    "LocalSink",
    "LocalSource",
    "SinkInventoryUnavailable",
    "SinkMatch",
    "list_all_sinks",
    "list_local_cards",
    "list_local_sinks",
    "list_local_sources",
    "match_players",
    "match_sink",
    "parse_pactl_cards",
    "parse_pactl_sinks",
    "parse_pactl_sources",
]

_EXCLUDED_PREFIXES = ("bluez_", "sendspin_")
_STANDBY_SINK_NAMES = frozenset({STANDBY_SINK_NAME, "sendspin_standby"})

# PulseAudio property -> identifier key stored in the player config.
_PROPERTY_TO_IDENTIFIER = {
    "device.serial": "serial",
    "device.bus_path": "bus_path",
    "device.vendor.id": "vendor_id",
    "device.product.id": "product_id",
    "alsa.long_card_name": "alsa_long_card_name",
}

# pa_sample_format_t values as pulsectl exposes them (plain ints).
_SAMPLE_FORMATS = {
    0: "u8",
    1: "aLaw",
    2: "uLaw",
    3: "s16le",
    4: "s16be",
    5: "float32le",
    6: "float32be",
    7: "s32le",
    8: "s32be",
    9: "s24le",
    10: "s24be",
    11: "s24-32le",
    12: "s24-32be",
}
_PORT_AVAILABILITY = ("not available", "availability unknown", "available")
_PROP_RE = re.compile(r'^\s*([\w.\-]+) = "(.*)"\s*$')
_PERCENT_RE = re.compile(r"(\d+)%")


class SinkInventoryUnavailable(RuntimeError):
    """Neither pulsectl nor ``pactl`` could list the sinks — the list is unknown, not empty."""


@dataclass(frozen=True)
class LocalSink:
    name: str
    description: str
    state: str  # running | idle | suspended | unknown
    sample_spec: str  # e.g. "s16le 2ch 48000Hz"
    muted: bool
    volume: int  # 0-100 (loudest channel)
    active_port: str | None
    active_port_available: str | None  # "available" | "not available" | "availability unknown" | None
    identifiers: dict[str, str] = field(default_factory=dict)
    index: int | None = None  # the ``Sink #N`` index, for the card's endpoint lists
    owner_module: int | None = None  # the module that loaded the card, when it has one
    #: The ``device.name`` property: under PipeWire this is the card's ``Name:``.
    device_name: str | None = None


@dataclass(frozen=True)
class CardProfile:
    """One profile a card can take (``a2dp_sink``, ``output:analog-stereo``…)."""

    name: str
    description: str
    available: bool | None


@dataclass(frozen=True)
class CardPort:
    """One port a card exposes; ``available=None`` means the server said "unknown".

    The tri-state matters: an unplugged port (``False``) explains silence, an
    unknown one does not, and collapsing the two would invent a fault.
    """

    name: str
    description: str
    available: bool | None
    active: bool
    profiles: tuple[str, ...] = ()


@dataclass(frozen=True)
class LocalCard:
    """An audio server card: its profiles, its ports and the endpoints it owns."""

    name: str
    driver: str
    active_profile: str | None
    profiles: tuple[CardProfile, ...]
    ports: tuple[CardPort, ...]
    sink_names: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()
    index: int | None = None
    owner_module: int | None = None


@dataclass(frozen=True)
class LocalSource:
    """A capture endpoint: a microphone, a monitor, or a phone's A2DP source."""

    name: str
    description: str
    state: str  # running | idle | suspended | unknown
    sample_spec: str
    muted: bool
    volume: int  # 0-100 (loudest channel)
    identifiers: dict[str, str] = field(default_factory=dict)
    monitor_of: str | None = None  # the sink this source monitors, if any
    index: int | None = None
    owner_module: int | None = None
    active_port: str | None = None
    active_port_available: str | None = None  # "available" | "not available" | "availability unknown" | None
    #: The ``device.name`` property: under PipeWire this is the card's ``Name:``.
    device_name: str | None = None


@dataclass(frozen=True)
class SinkMatch:
    sink: LocalSink | None
    method: str | None  # serial | bus_path | vendor_product | alsa_long_card_name | name
    status: str  # ok | sink_missing | sink_ambiguous | sink_in_use


# ---------------------------------------------------------------------------
# pactl parsing
# ---------------------------------------------------------------------------


def _normalise_state(raw: str) -> str:
    state = raw.strip().lower()
    return state if state in {"running", "idle", "suspended"} else "unknown"


def _parse_block(lines: list[str], index: int | None = None) -> LocalSink | None:
    fields: dict[str, str] = {}
    props: dict[str, str] = {}
    ports: dict[str, str] = {}
    section = ""
    for line in lines:
        if line.startswith("\t\t"):
            if section == "Properties":
                m = _PROP_RE.match(line)
                if m:
                    props[m.group(1)] = m.group(2)
            elif section == "Ports":
                port_name, sep, rest = line.strip().partition(": ")
                if sep:
                    ports[port_name] = rest
            continue
        if line.startswith("\t") and not line.startswith("\t "):
            key, sep, value = line.strip().partition(":")
            if not sep:
                continue
            section = key if key in {"Properties", "Ports"} else ""
            fields[key] = value.strip()
    name = fields.get("Name")
    if not name:
        return None
    percents = [int(p) for p in _PERCENT_RE.findall(fields.get("Volume", ""))]
    active_port = fields.get("Active Port") or None
    return LocalSink(
        name=name,
        description=fields.get("Description", ""),
        state=_normalise_state(fields.get("State", "")),
        sample_spec=fields.get("Sample Specification", ""),
        muted=fields.get("Mute", "").lower() == "yes",
        volume=max(percents) if percents else 0,
        active_port=active_port,
        active_port_available=_active_port_availability(active_port, ports),
        identifiers={ident: props[prop] for prop, ident in _PROPERTY_TO_IDENTIFIER.items() if props.get(prop)},
        index=index,
        owner_module=_owner_module(fields.get("Owner Module", "")),
        device_name=props.get("device.name") or None,
    )


def _owner_module(raw: str) -> int | None:
    """``Owner Module: 7`` as an int; ``n/a`` and PipeWire's ``4294967295`` as unknown."""
    value = raw.strip()
    if not value.isdigit():
        return None
    module = int(value)
    return None if module == 4294967295 else module


def _active_port_availability(active_port: str | None, ports: dict[str, str]) -> str | None:
    """The availability phrase of *active_port*, from the port line that describes it."""
    if not active_port or active_port not in ports:
        return None
    text = ports[active_port].rstrip(")")
    return next((availability for availability in _PORT_AVAILABILITY if text.endswith(availability)), None)


def _block_index(header: str) -> int | None:
    """The ``N`` of ``Card #N`` / ``Source #N``, or ``None`` when it is not a number."""
    _, _, raw = header.partition("#")
    return int(raw) if raw.strip().isdigit() else None


def parse_pactl_sinks(text: str) -> list[LocalSink]:
    """Parse ``pactl list sinks`` output into :class:`LocalSink` entries."""
    blocks: list[tuple[int | None, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("Sink #"):
            blocks.append((_block_index(line), []))
        elif blocks:
            blocks[-1][1].append(line)
    return [s for s in (_parse_block(b, i) for i, b in blocks) if s is not None]


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

_PROFILE_META_RE = re.compile(
    r"\s*\(sinks: \d+, sources: \d+, priority: \d+, available: (yes|no|unknown|availability unknown)\)\s*$"
)


def _availability(text: str) -> bool | None:
    """Port availability: True, False, or None when the server said "unknown"."""
    tail = text.strip().rstrip(")").strip()
    if tail.endswith("not available"):
        return False
    if tail.endswith("availability unknown"):
        return None
    return True if tail.endswith("available") else None


def _card_profile(line: str) -> CardProfile | None:
    name, sep, rest = line.partition(": ")
    if not sep:
        return None
    meta = _PROFILE_META_RE.search(rest)
    availability: bool | None = None
    if meta is not None:
        availability = {"yes": True, "no": False}.get(meta.group(1))
    return CardProfile(
        name=name.strip(),
        description=_PROFILE_META_RE.sub("", rest).strip(),
        available=availability,
    )


def _card_port(line: str, active_port: str | None, profiles: tuple[str, ...] = ()) -> CardPort | None:
    name, sep, rest = line.partition(": ")
    if not sep:
        return None
    description, port_type, tail = rest.partition(" (type:")
    return CardPort(
        name=name.strip(),
        description=description.strip(),
        available=_availability(tail) if port_type else None,
        active=name.strip() == active_port,
        profiles=profiles,
    )


def _card_endpoint_names(entries: list[str], endpoints: Any) -> tuple[str, ...]:
    """Resolve a card's ``Sinks:``/``Sources:`` entries to names that exist.

    Entries may be endpoint indexes or endpoint names; both are looked up in the
    list that was actually read, so a stale index becomes nothing rather than a
    guessed name.
    """
    by_index = {item.index: item.name for item in endpoints if getattr(item, "index", None) is not None}
    known = {item.name for item in endpoints}
    resolved: list[str] = []
    for entry in entries:
        token = entry.split(": ", 1)[0].strip()
        name = by_index.get(int(token)) if token.isdigit() else (token if token in known else None)
        if name and name not in resolved:
            resolved.append(name)
    return tuple(resolved)


def _parse_card(lines: list[str], index: int | None, sinks: Any, sources: Any) -> LocalCard | None:
    fields: dict[str, str] = {}
    profile_lines: list[str] = []
    port_lines: list[str] = []
    port_profiles: list[list[str]] = []
    sink_entries: list[str] = []
    source_entries: list[str] = []
    section = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Part of profile(s):") and section == "Ports" and port_profiles:
            # The line under a port names the profiles that can use it.
            port_profiles[-1].extend(part.strip() for part in stripped.split(":", 1)[1].split(",") if part.strip())
            continue
        if line.startswith("\t\t\t"):
            continue  # port properties belong to the port line above
        if line.startswith("\t\t"):
            entry = stripped
            if section == "Profiles":
                profile_lines.append(entry)
            elif section == "Ports":
                port_lines.append(entry)
                port_profiles.append([])
            elif section == "Sinks":
                sink_entries.append(entry)
            elif section == "Sources":
                source_entries.append(entry)
            continue
        if line.startswith("\t") and not line.startswith("\t "):
            key, sep, value = line.strip().partition(":")
            if not sep:
                continue
            section = key if key in {"Profiles", "Ports", "Sinks", "Sources"} else ""
            fields[key] = value.strip()
    name = fields.get("Name")
    if not name:
        return None
    active_port = fields.get("Active Port") or None
    profiles = tuple(p for p in (_card_profile(line) for line in profile_lines) if p is not None)
    ports = tuple(
        port
        for port in (
            _card_port(line, active_port, tuple(port_profiles[index])) for index, line in enumerate(port_lines)
        )
        if port is not None
    )
    return LocalCard(
        name=name,
        driver=fields.get("Driver", ""),
        active_profile=fields.get("Active Profile") or None,
        profiles=profiles,
        ports=ports,
        sink_names=_card_endpoint_names(sink_entries, sinks),
        source_names=_card_endpoint_names(source_entries, sources),
        index=index,
        owner_module=_owner_module(fields.get("Owner Module", "")),
    )


def parse_pactl_cards(
    text: str,
    sinks: Collection[LocalSink] = (),
    sources: Collection[LocalSource] = (),
) -> list[LocalCard]:
    """Parse ``pactl list cards`` output into :class:`LocalCard` entries.

    *sinks* and *sources* are the endpoint lists the same read produced; the
    card's endpoint index lists are resolved through them and never parsed as
    names on their own.
    """
    blocks: list[tuple[int | None, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("Card #"):
            blocks.append((_block_index(line), []))
        elif blocks:
            blocks[-1][1].append(line)
    return [c for c in (_parse_card(b, i, sinks, sources) for i, b in blocks) if c is not None]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _parse_source_block(lines: list[str], index: int | None) -> LocalSource | None:
    fields: dict[str, str] = {}
    props: dict[str, str] = {}
    ports: dict[str, str] = {}
    section = ""
    for line in lines:
        if line.startswith("\t\t"):
            if section == "Properties":
                m = _PROP_RE.match(line)
                if m:
                    props[m.group(1)] = m.group(2)
            elif section == "Ports":
                port_name, sep, rest = line.strip().partition(": ")
                if sep:
                    ports[port_name] = rest
            continue
        if line.startswith("\t") and not line.startswith("\t "):
            key, sep, value = line.strip().partition(":")
            if not sep:
                continue
            section = key if key in {"Properties", "Ports"} else ""
            fields[key] = value.strip()
    name = fields.get("Name")
    if not name:
        return None
    percents = [int(p) for p in _PERCENT_RE.findall(fields.get("Volume", ""))]
    monitor_of = fields.get("Monitor of Sink", "").strip()
    active_port = fields.get("Active Port") or None
    return LocalSource(
        name=name,
        description=fields.get("Description", ""),
        state=_normalise_state(fields.get("State", "")),
        sample_spec=fields.get("Sample Specification", ""),
        muted=fields.get("Mute", "").lower() == "yes",
        volume=max(percents) if percents else 0,
        identifiers={ident: props[prop] for prop, ident in _PROPERTY_TO_IDENTIFIER.items() if props.get(prop)},
        monitor_of=None if monitor_of in ("", "n/a") else monitor_of,
        index=index,
        owner_module=_owner_module(fields.get("Owner Module", "")),
        active_port=active_port,
        active_port_available=_active_port_availability(active_port, ports),
        device_name=props.get("device.name") or None,
    )


def parse_pactl_sources(text: str) -> list[LocalSource]:
    """Parse ``pactl list sources`` output into :class:`LocalSource` entries."""
    blocks: list[tuple[int | None, list[str]]] = []
    for line in text.splitlines():
        if line.startswith("Source #"):
            blocks.append((_block_index(line), []))
        elif blocks:
            blocks[-1][1].append(line)
    return [s for s in (_parse_source_block(b, i) for i, b in blocks) if s is not None]


# ---------------------------------------------------------------------------
# pulsectl mapping
# ---------------------------------------------------------------------------


def _from_pulsectl(info: Any) -> LocalSink:
    spec = getattr(info, "sample_spec", None)
    sample_spec = ""
    if spec is not None:
        raw_fmt = getattr(spec, "format", None)
        fmt = _SAMPLE_FORMATS.get(raw_fmt, str(raw_fmt)) if isinstance(raw_fmt, int) else str(raw_fmt or "")
        sample_spec = f"{fmt} {getattr(spec, 'channels', '?')}ch {getattr(spec, 'rate', '?')}Hz"
    values = list(getattr(getattr(info, "volume", None), "values", None) or [])
    port = getattr(info, "port_active", None)
    availability: str | None = None
    if port is not None:
        raw = str(getattr(getattr(port, "available", None), "_value", getattr(port, "available", "")))
        availability = {"yes": "available", "no": "not available", "unknown": "availability unknown"}.get(raw)
    proplist = dict(getattr(info, "proplist", None) or {})
    state = getattr(info, "state", None)
    return LocalSink(
        name=info.name,
        description=getattr(info, "description", "") or "",
        state=_normalise_state(str(getattr(state, "_value", state) or "")),
        sample_spec=sample_spec,
        muted=bool(getattr(info, "mute", 0)),
        volume=round(max(values) * 100) if values else 0,
        active_port=getattr(port, "name", None) if port is not None else None,
        active_port_available=availability,
        identifiers={
            ident: str(proplist[prop]) for prop, ident in _PROPERTY_TO_IDENTIFIER.items() if proplist.get(prop)
        },
        index=getattr(info, "index", None),
        owner_module=getattr(info, "owner_module", None),
    )


async def _pactl_list_text(what: str) -> str | None:
    """``pactl list <what>`` output, or ``None`` when it could not be run."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "pactl",
            "list",
            what,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "LC_ALL": "C"},
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=pulse._TIMEOUT)
    except (OSError, TimeoutError) as exc:
        logger.debug("pactl list %s failed: %s", what, exc)
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return None
    if proc.returncode not in (0, None):
        logger.debug("pactl list %s exited with %s", what, proc.returncode)
        return None
    return out.decode(errors="replace")


async def _pactl_list_sinks_text() -> str | None:
    """``pactl list sinks`` output, or ``None`` when it could not be run."""
    return await _pactl_list_text("sinks")


async def _pactl_list_sources_text() -> str | None:
    """``pactl list sources`` output, or ``None`` when it could not be run."""
    return await _pactl_list_text("sources")


async def _pactl_list_cards_text() -> str | None:
    """``pactl list cards`` output, or ``None`` when it could not be run."""
    return await _pactl_list_text("cards")


async def _list_all_sinks_pactl() -> list[LocalSink]:
    text = await _pactl_list_sinks_text()
    return parse_pactl_sinks(text) if text is not None else []


async def _list_all_sinks_pulsectl() -> list[LocalSink] | None:
    """The sinks through pulsectl, or ``None`` when it is missing or failed."""
    if not pulse._PULSECTL_AVAILABLE:
        return None
    try:
        async with asyncio.timeout(pulse._TIMEOUT):
            async with pulse.pulsectl_asyncio.PulseAsync(pulse._CLIENT_NAME) as client:
                return [_from_pulsectl(s) for s in await client.sink_list()]
    except Exception as exc:
        logger.debug("list_local_sinks pulsectl error: %s — falling back to pactl", exc)
        return None


def _mock_sinks_text() -> str | None:
    """Dev-only: ``SENDSPIN_MOCK_SINKS=<file in pactl format>`` replaces the real sink source.

    ``None`` when the variable is unset; an unreadable file raises :class:`OSError`.
    """
    path = os.environ.get("SENDSPIN_MOCK_SINKS")
    if not path:
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


async def _list_all_sinks_strict() -> list[LocalSink]:
    """Like :func:`_list_all_sinks`, but a failure to read raises instead of reading as no sinks."""
    if os.environ.get("SENDSPIN_MOCK_SINKS"):
        try:
            return parse_pactl_sinks(_mock_sinks_text() or "")
        except OSError as exc:
            raise SinkInventoryUnavailable(f"could not read SENDSPIN_MOCK_SINKS: {exc}") from exc
    sinks = await _list_all_sinks_pulsectl()
    if sinks is not None:
        return sinks
    text = await _pactl_list_sinks_text()
    if text is None:
        raise SinkInventoryUnavailable("could not list PulseAudio sinks")
    return parse_pactl_sinks(text)


async def _list_all_sinks() -> list[LocalSink]:
    if os.environ.get("SENDSPIN_MOCK_SINKS"):
        try:
            return parse_pactl_sinks(_mock_sinks_text() or "")
        except OSError as exc:
            logger.debug("SENDSPIN_MOCK_SINKS unreadable: %s", exc)
            return []
    sinks = await _list_all_sinks_pulsectl()
    return sinks if sinks is not None else await _list_all_sinks_pactl()


async def list_all_sinks(*, strict: bool = False) -> list[LocalSink]:
    """Every sink the audio server reports, Bluetooth and bridge-internal ones included.

    :func:`list_local_sinks` is the filtered view for player matching; the
    hardware inventory needs the whole set to classify each endpoint's owner.
    *strict* turns an unreadable list into :class:`SinkInventoryUnavailable`.
    """
    return await (_list_all_sinks_strict() if strict else _list_all_sinks())


async def list_local_sources(*, strict: bool = False) -> list[LocalSource]:
    """Every source the audio server reports, monitors included.

    An unreadable source list reads as empty unless *strict*: then it raises
    :class:`SinkInventoryUnavailable`, so a caller can tell "none" from
    "cannot read" instead of rendering both as an empty section.
    """
    text = await _pactl_list_sources_text()
    if text is None:
        if strict:
            raise SinkInventoryUnavailable("could not list PulseAudio sources")
        return []
    return parse_pactl_sources(text)


async def list_local_cards(
    *,
    sinks: Collection[LocalSink] | None = None,
    sources: Collection[LocalSource] | None = None,
    strict: bool = False,
) -> list[LocalCard]:
    """Every card, with its endpoint index lists resolved through the endpoint lists.

    *sinks* and *sources* are reused when the caller already read them (the
    hardware reader does), so one refresh still costs one read per kind.
    *strict* turns an unreadable list into :class:`SinkInventoryUnavailable`.
    """
    if sinks is None:
        sinks = await (list_all_sinks(strict=True) if strict else list_all_sinks())
    if sources is None:
        sources = await (list_local_sources(strict=True) if strict else list_local_sources())
    text = await _pactl_list_cards_text()
    if text is None:
        if strict:
            raise SinkInventoryUnavailable("could not list PulseAudio cards")
        return []
    return parse_pactl_cards(text, sinks, sources)


async def list_local_sinks(*, strict: bool = False) -> list[LocalSink]:
    """Local playback sinks, without Bluetooth and bridge-internal sinks.

    An unreadable sink list reads as empty, unless *strict*: then it raises
    :class:`SinkInventoryUnavailable`, so a caller that acts on absence (an
    unplugged output) never mistakes a PulseAudio hiccup for an unplug.
    """
    sinks = await (_list_all_sinks_strict() if strict else _list_all_sinks())
    return [s for s in sinks if not s.name.startswith(_EXCLUDED_PREFIXES) and s.name not in _STANDBY_SINK_NAMES]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _resolve(
    candidates: list[LocalSink], method: str, match: dict, stored_name: str | None
) -> tuple[LocalSink | None, str | None, bool]:
    """Pick one of *candidates*; returns (sink, method, ambiguous)."""
    if len(candidates) == 1:
        return candidates[0], method, False
    if not candidates:
        return None, None, False
    wanted = match.get("alsa_long_card_name")
    if wanted:
        narrowed = [s for s in candidates if s.identifiers.get("alsa_long_card_name") == wanted]
        if len(narrowed) == 1:
            return narrowed[0], "alsa_long_card_name", False
        if narrowed:
            candidates = narrowed
    # Several sinks of one card share its identifiers; the stored name tells them apart.
    named = [s for s in candidates if stored_name and s.name == stored_name]
    if len(named) == 1:
        return named[0], method, False
    return None, None, True


def _same_port(match: dict, sink: LocalSink) -> bool:
    """A different ``bus_path`` is a different physical device, even if identical hardware."""
    stored, current = match.get("bus_path"), sink.identifiers.get("bus_path")
    return not stored or not current or stored == current


def _match_identity(match: dict, stored_name: str | None, sinks: list[LocalSink]) -> tuple[SinkMatch | None, bool]:
    """The identity tiers only: (match, ambiguous); the stored name is not tried."""
    ambiguous = False
    tiers: list[tuple[str, list[LocalSink]]] = []
    if match.get("serial"):
        tiers.append(("serial", [s for s in sinks if s.identifiers.get("serial") == match["serial"]]))
    if match.get("bus_path"):
        tiers.append(("bus_path", [s for s in sinks if s.identifiers.get("bus_path") == match["bus_path"]]))
    if match.get("vendor_id") and match.get("product_id"):
        tiers.append(
            (
                "vendor_product",
                [
                    s
                    for s in sinks
                    if s.identifiers.get("vendor_id") == match["vendor_id"]
                    and s.identifiers.get("product_id") == match["product_id"]
                    and _same_port(match, s)
                ],
            )
        )
    for method, candidates in tiers:
        sink, how, tier_ambiguous = _resolve(candidates, method, match, stored_name)
        if sink is not None and how is not None:
            return SinkMatch(sink, how, "ok"), False
        ambiguous = ambiguous or tier_ambiguous
    return None, ambiguous


def match_players[K: Hashable](
    players: Mapping[K, tuple[dict, str | None]],
    sinks: list[LocalSink],
    *,
    claimed: Collection[str] = frozenset(),
) -> dict[K, SinkMatch]:
    """Find the current sink of several players at once.

    *players* maps a key to its stored ``(match, sink name)``.  Every player's
    identifiers (``serial`` -> ``bus_path`` -> ``vendor_id``+``product_id``)
    claim their sinks first; only then does a player still without one try its
    stored name, and never on a sink another player's identifiers claimed.
    PulseAudio names identical DACs by enumeration order, so after a reboot
    with one unplugged its twin can carry the stored name of the missing one.

    A tier that finds several sinks is narrowed by ``alsa_long_card_name`` and
    then by the stored name; if that does not leave exactly one the result is
    ``sink_ambiguous``.  A sink in *claimed* (another running player) or taken
    by an earlier player here yields ``sink_in_use``.
    """
    results: dict[K, SinkMatch] = {}
    by_identity: set[str] = set()
    pending: list[K] = []
    for key, (match, stored_name) in players.items():
        found, ambiguous = _match_identity(match or {}, stored_name, sinks)
        if found is not None and found.sink is not None:
            name = found.sink.name
            in_use = name in claimed or name in by_identity
            results[key] = SinkMatch(found.sink, found.method, "sink_in_use" if in_use else "ok")
            by_identity.add(name)
        elif ambiguous:
            results[key] = SinkMatch(None, None, "sink_ambiguous")
        else:
            pending.append(key)

    by_name: set[str] = set()
    for key in pending:
        stored_name = players[key][1]
        sink = next((s for s in sinks if stored_name and s.name == stored_name), None)
        if sink is None or sink.name in by_identity:
            # The name now belongs to a device another player's identifiers found.
            results[key] = SinkMatch(None, None, "sink_missing")
            continue
        in_use = sink.name in claimed or sink.name in by_name
        results[key] = SinkMatch(sink, "name", "sink_in_use" if in_use else "ok")
        by_name.add(sink.name)
    return {key: results[key] for key in players}


def match_sink(
    match: dict,
    stored_name: str | None,
    sinks: list[LocalSink],
    *,
    claimed: Collection[str] = frozenset(),
) -> SinkMatch:
    """Find the current sink for one player's stored identifiers.

    One player alone; see :func:`match_players` for the order of the tiers.
    Players matched together must go through :func:`match_players`, so one
    player's stored name cannot take a sink another player's identifiers own.
    """
    return match_players({0: (match, stored_name)}, sinks, claimed=claimed)[0]
