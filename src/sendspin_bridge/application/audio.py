"""Local audio outputs: the inventory and who uses each sink."""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from sendspin_bridge.application.errors import UseCaseError, require_audio_name
from sendspin_bridge.application.models.audio import AudioSinkOut, SinkStateOut
from sendspin_bridge.application.runtime import run_on_loop
from sendspin_bridge.config import load_config
from sendspin_bridge.services.audio.sink_inventory import LocalSink, list_local_sinks
from sendspin_bridge.services.hardware import capabilities
from sendspin_bridge.services.hardware.inventory import HardwareUnavailable, clear_cache, read_inventory

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


def _after_write() -> None:
    """Drop the inventory cache so the next read shows what the write changed.

    Without this the page refreshes within the two-second TTL and gets the state
    from before the command: the Default badge stays on the old sink right after
    the toast says it moved.
    """
    clear_cache()


async def _require_sink(wanted: str) -> LocalSink:
    """The named sink from the current inventory, after the backend check.

    The order is the contract: 409 when nothing answers, 404 when a server
    answers but the sink is not there.  The reader is blocking, so it runs in a
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
    sink = next((item for item in read.audio.sinks if item.name == wanted), None)
    if sink is None:
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
    _after_write()
    return SinkStateOut(name=wanted, volume=volume, muted=sink.muted, state=sink.state)


async def set_sink_mute(name: str, muted: bool) -> SinkStateOut:
    """Mute or unmute one sink."""
    wanted = require_audio_name(name, field="sink name")
    sink = await _require_sink(wanted)
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.set_sink_mute, wanted, muted):
        raise _refused("a mute change", wanted)
    _after_write()
    return SinkStateOut(name=wanted, volume=sink.volume, muted=muted, state=sink.state)


async def set_default_sink(name: str) -> SinkStateOut:
    """Make one sink the audio server's default."""
    wanted = require_audio_name(name, field="sink name")
    sink = await _require_sink(wanted)
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.set_default_sink, wanted):
        raise _refused("a default-sink change", wanted)
    _after_write()
    return SinkStateOut(name=wanted, volume=sink.volume, muted=sink.muted, state=sink.state)


async def _set_suspended(name: str, suspend: bool) -> SinkStateOut:
    wanted = require_audio_name(name, field="sink name")
    sink = await _require_sink(wanted)
    from sendspin_bridge.services.audio import pulse

    if not await asyncio.to_thread(pulse.suspend_sink, wanted, suspend):
        raise _refused("a suspend change", wanted)
    _after_write()
    state = "suspended" if suspend else sink.state
    return SinkStateOut(name=wanted, volume=sink.volume, muted=sink.muted, state=state)


async def suspend_sink(name: str) -> SinkStateOut:
    """Suspend one sink (it keeps existing, but no longer plays)."""
    return await _set_suspended(name, True)


async def resume_sink(name: str) -> SinkStateOut:
    """Resume one suspended sink."""
    return await _set_suspended(name, False)
