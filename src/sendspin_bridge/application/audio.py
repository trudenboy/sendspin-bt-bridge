"""Local audio outputs: the inventory and who uses each sink."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import replace
from typing import Any, Literal

from sendspin_bridge.application import hardware as hardware_uc
from sendspin_bridge.application.errors import UseCaseError, require_audio_name
from sendspin_bridge.application.models.audio import AudioSinkOut, SinkStateOut
from sendspin_bridge.application.models.hardware import CardDetail, EndpointDetail
from sendspin_bridge.application.runtime import run_on_loop
from sendspin_bridge.config import load_config
from sendspin_bridge.services.audio.sink_inventory import LocalSink, list_local_sinks
from sendspin_bridge.services.hardware import capabilities
from sendspin_bridge.services.hardware.inventory import (
    HardwareUnavailable,
    InventoryRead,
    invalidate_cache,
    read_inventory,
)

logger = logging.getLogger(__name__)

_READ_TIMEOUT_S = 3.0


async def list_sinks() -> list[AudioSinkOut]:
    """The local sinks (one inventory read), each with the pulse_sink player using it."""
    sinks = await list_local_sinks()
    config = await asyncio.to_thread(load_config)
    users: dict[str, str] = {}
    for player in config.get("players") or []:
        backend = player.get("backend") if isinstance(player, dict) else None
        if isinstance(backend, dict) and backend.get("type") == "pulse_sink" and backend.get("sink"):
            users.setdefault(backend["sink"], player.get("id"))
    return [
        AudioSinkOut(
            name=s.name,
            description=s.description,
            state=s.state,
            sample_spec=s.sample_spec,
            muted=s.muted,
            volume=s.volume,
            active_port_available=s.active_port_available,
            identifiers=dict(s.identifiers),
            in_use_by=users.get(s.name),
        )
        for s in sinks
    ]


def read_local_sinks() -> list[LocalSink]:
    """The sink inventory for a worker thread (a config save); empty when it cannot be read.

    Never call this while holding ``config_lock``: it waits on the bridge loop.
    """
    coro = list_local_sinks()
    try:
        return run_on_loop(coro, timeout=_READ_TIMEOUT_S, description="local sink inventory")
    except UseCaseError as exc:
        coro.close()  # never started when the bridge loop is not there
        if exc.code != "not_ready":
            logger.warning("Could not read the local sinks: %s", exc)
            return []
    try:
        fallback = list_local_sinks()
        try:
            return asyncio.run(fallback)
        finally:
            fallback.close()
    except Exception as exc:
        logger.warning("Could not read the local sinks: %s", exc)
        return []


_RECENT_MAX_AGE_S = 2.0
_recent_lock = threading.Lock()
_recent: tuple[float, list[LocalSink]] | None = None


def read_local_sinks_recent() -> list[LocalSink]:
    """The inventory for guidance builders, reused for a couple of seconds.

    A status build runs on every status change, which during playback can be
    several a second; one inventory read each would spawn a PulseAudio round
    trip per build.  Builders run in worker threads, never on the loop.
    """
    global _recent
    with _recent_lock:
        now = time.monotonic()
        if _recent is not None and now - _recent[0] < _RECENT_MAX_AGE_S:
            return list(_recent[1])
        sinks = read_local_sinks()
        _recent = (time.monotonic(), sinks)
        return list(sinks)


# ---------------------------------------------------------------------------
# Commands (safe audio writes)
# ---------------------------------------------------------------------------

_NO_SERVER = "No PulseAudio-compatible audio server answered; this host does not support audio commands."


def _refused(action: str, name: str) -> UseCaseError:
    return UseCaseError(
        409,
        "not_supported_for_backend",
        f"The audio server did not accept {action} for {name}.",
    )


async def _after_write() -> None:
    """Expire the inventory cache so the next read shows what the write changed.

    Without this the page refreshes within the two-second TTL and gets the state
    from before the command: the Default badge stays on the old sink right after
    the toast says it moved.  ``invalidate_cache`` takes the reader lock, so it runs
    in a worker thread: a request handler must never wait on it on the loop.
    """
    await asyncio.to_thread(invalidate_cache)


def _unread_list(read: InventoryRead, kind: Literal["cards", "sinks"]) -> UseCaseError | None:
    """The 503 for a list that could not be read, instead of a 404 that invents absence.

    An empty list says the server has nothing; a failed list says the server
    could not answer.  Only the first one makes a name unknown.
    """
    reason = hardware_uc.audio_list_failure(read.audio, kind)
    if reason is None:
        return None
    return UseCaseError(
        503,
        "hardware_unavailable",
        f"The audio server answered, but its {kind} list could not be read: {reason}.",
    )


async def _inventory_or_unsupported() -> InventoryRead:
    """The current read, after the backend check the command contract starts with.

    The order is deliberate: 409 when nothing answers, then 404 when a server
    answers but the thing is not there.  The reader is blocking, so it runs in a
    worker thread; every hardware consumer reads through the same one.
    """
    try:
        read = await asyncio.to_thread(read_inventory)
    except HardwareUnavailable as exc:
        # The Bluetooth half failed; the audio answer is still the first one the
        # contract asks for, so a dead server stays a 409 before the 503.
        server_name = await asyncio.to_thread(capabilities.probe_audio_server)
        if capabilities.classify_audio_server(server_name) == "none":
            raise UseCaseError(409, "not_supported_for_backend", _NO_SERVER) from exc
        raise UseCaseError(503, "hardware_unavailable", str(exc)) from exc
    if read.capabilities.audio_server == "none":
        raise UseCaseError(409, "not_supported_for_backend", _NO_SERVER)
    return read


async def _require_sink(wanted: str) -> LocalSink:
    """The named sink from the current inventory."""
    read = await _inventory_or_unsupported()
    sink = next((item for item in read.audio.sinks if item.name == wanted), None)
    if sink is None:
        failed = _unread_list(read, "sinks")
        if failed is not None:
            raise failed
        raise UseCaseError(404, "entity_not_found", f"Unknown audio sink: {wanted}")
    return LocalSink(
        name=sink.name,
        description=sink.description,
        state=sink.state,
        sample_spec=sink.sample_spec,
        muted=bool(sink.muted),
        volume=int(sink.volume or 0),
        active_port=sink.active_port,
        active_port_available=sink.active_port_available,
        identifiers=dict(sink.identifiers),
    )


async def set_sink_volume(name: str, volume: int) -> SinkStateOut:
    """Set one sink's level (0-100 inclusive)."""
    wanted = require_audio_name(name, field="sink name")
    if not 0 <= volume <= 100:
        raise UseCaseError(400, "invalid_volume", "volume must be an integer from 0 to 100")
    sink = await _require_sink(wanted)
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.set_sink_volume, wanted, volume):
        raise _refused("a volume change", wanted)
    await _after_write()
    return SinkStateOut(name=wanted, volume=volume, muted=sink.muted, state=sink.state)


async def set_sink_mute(name: str, muted: bool) -> SinkStateOut:
    """Mute or unmute one sink."""
    wanted = require_audio_name(name, field="sink name")
    sink = await _require_sink(wanted)
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.set_sink_mute, wanted, muted):
        raise _refused("a mute change", wanted)
    await _after_write()
    return SinkStateOut(name=wanted, volume=sink.volume, muted=muted, state=sink.state)


async def set_default_sink(name: str) -> SinkStateOut:
    """Make one sink the audio server's default."""
    wanted = require_audio_name(name, field="sink name")
    sink = await _require_sink(wanted)
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.set_default_sink, wanted):
        raise _refused("a default-sink change", wanted)
    await _after_write()
    return SinkStateOut(name=wanted, volume=sink.volume, muted=sink.muted, state=sink.state)


async def _set_suspended(name: str, suspend: bool) -> SinkStateOut:
    wanted = require_audio_name(name, field="sink name")
    sink = await _require_sink(wanted)
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.suspend_sink, wanted, suspend):
        raise _refused("a suspend change", wanted)
    await _after_write()
    state = "suspended" if suspend else sink.state
    return SinkStateOut(name=wanted, volume=sink.volume, muted=sink.muted, state=state)


async def suspend_sink(name: str) -> SinkStateOut:
    """Suspend one sink (it keeps existing, but no longer plays)."""
    return await _set_suspended(name, True)


async def resume_sink(name: str) -> SinkStateOut:
    """Resume one suspended sink."""
    return await _set_suspended(name, False)


async def _read_after_write(fallback: InventoryRead) -> tuple[InventoryRead, dict[str, Any], bool]:
    """The state after a command, the config the detail joins, and whether it refreshed.

    The cache was just expired, so this is a fresh read (and the first one to see
    what the command changed).  When the refresh cannot answer at all the
    pre-write read is used instead: the command already succeeded, and a failed
    follow-up read must not turn an audible action into an error.  ``refreshed``
    is False exactly then, so the caller can say the detail is stale and must
    not claim the new state from a read taken before the write.
    """
    try:
        read = await asyncio.to_thread(read_inventory)
    except HardwareUnavailable:
        read, refreshed = fallback, False
    else:
        refreshed = not read.stale
    config = await asyncio.to_thread(load_config)
    return read, config, refreshed


async def set_card_profile(card_name: str, profile: str) -> CardDetail:
    """Switch one card to an explicit profile, without cycling through ``off``.

    ``profile`` must be one the card itself lists: an operator picking from the
    hardware page can only choose one of those, and anything else is a bad
    request rather than something the server should be asked to reject.  A card
    the bridge's Bluetooth link manages is refused: its profile follows the
    connection, and a second writer would only fight it (``off`` silences the
    speaker until the next connect).
    """
    wanted = require_audio_name(card_name, field="card name")
    read = await _inventory_or_unsupported()
    card = next((item for item in read.audio.cards if item.name == wanted), None)
    if card is None:
        failed = _unread_list(read, "cards")
        if failed is not None:
            raise failed
        raise UseCaseError(404, "entity_not_found", f"Unknown audio card: {card_name}")
    if hardware_uc.managed_by(card) == "bridge":
        raise UseCaseError(
            409,
            "not_supported_for_backend",
            f"The bridge manages this card's profile ({wanted}); the Bluetooth connection chooses it.",
        )
    if profile not in {item.name for item in card.profiles}:
        raise UseCaseError(
            400,
            "invalid_profile",
            f"The card {wanted} does not offer the profile {profile!r}.",
        )
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.set_card_profile, wanted, profile):
        raise _refused("a profile change", wanted)
    await _after_write()
    fresh, config, refreshed = await _read_after_write(read)
    current = next((item for item in fresh.audio.cards if item.name == wanted), None)
    confirmed = refreshed and current is not None
    return hardware_uc.build_card_detail(
        current if current is not None else card,
        fresh.audio,
        config,
        as_of=fresh.as_of,
        stale=fresh.stale or not confirmed,
        active_profile=profile if confirmed else None,
    )


async def set_sink_port(sink_name: str, port: str) -> EndpointDetail:
    """Switch one sink to one of its card's ports.

    The card is resolved through the read model's owner classification
    (``application.hardware.endpoint_card``), never by parsing the sink name:
    a Bluetooth sink is *owned* by the speaker link but its ports still belong
    to the ``bluez_card`` that lists it.
    """
    wanted = require_audio_name(sink_name, field="sink name")
    read = await _inventory_or_unsupported()
    facts = next((item for item in read.audio.sinks if item.name == wanted), None)
    if facts is None:
        failed = _unread_list(read, "sinks")
        if failed is not None:
            raise failed
        raise UseCaseError(404, "entity_not_found", f"Unknown audio sink: {sink_name}")
    card = hardware_uc.endpoint_card(facts, read.audio.cards)
    chosen = next((item for item in card.ports if item.name == port), None) if card is not None else None
    if chosen is None:
        raise UseCaseError(
            400,
            "invalid_port",
            f"The sink {wanted} has no port {port!r} on its card.",
        )
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.set_sink_port, wanted, port):
        raise _refused("a port change", wanted)
    await _after_write()
    fresh, config, refreshed = await _read_after_write(read)
    current = next((item for item in fresh.audio.sinks if item.name == wanted), None)
    confirmed = refreshed and current is not None
    shown = current if current is not None else facts
    availability = {True: "available", False: "not available", None: "availability unknown"}[chosen.available]
    return hardware_uc.build_endpoint_detail(
        replace(shown, active_port=port, active_port_available=availability) if confirmed else shown,
        fresh.audio,
        config,
        as_of=fresh.as_of,
        stale=fresh.stale or not confirmed,
        active_port=port if confirmed else None,
    )
