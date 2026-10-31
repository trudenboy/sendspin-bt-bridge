"""Use cases for the Sendspin source: the phone→bridge feed as one entity.

The routers used to inline this orchestration (start/stop, arming a speaker's
relay, the phone pairing window) and reach into ``services`` themselves.  The
routes are now adapters over these functions, and the source's runtime state is
read through the typed snapshot in :mod:`sendspin_bridge.services.source.state`.

Nothing here touches HTTP: a failure is a :class:`UseCaseError`, mapped by the
API adapter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sendspin_bridge.application.errors import UseCaseError, conflict
from sendspin_bridge.services.source import manager, pairing, relay
from sendspin_bridge.services.source import state as source_state

__all__ = [
    "SourceStatus",
    "close_pairing_window",
    "open_pairing_window",
    "set_players",
    "start",
    "status",
    "stop",
]

DEFAULT_PAIRING_WINDOW_S = 120


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """What the source is doing, joined with the players it is armed to feed."""

    enabled: bool
    port: int | None
    phone: str | None
    players: int
    streaming: bool
    error: str | None
    pairing_error: str | None
    pairing_seconds_left: int
    pairing_adapters: tuple[str, ...]
    attached_devices: tuple[str, ...]

    def as_status_dict(self) -> dict[str, Any]:
        """The bridge-status shape (the keys ``SendspinSourceStatus`` reads)."""
        return {
            "enabled": self.enabled,
            "port": self.port,
            "phone": self.phone,
            "players": self.players,
            "streaming": self.streaming,
            "error": self.error,
            "pairing_seconds_left": self.pairing_seconds_left,
            "pairing_adapters": list(self.pairing_adapters),
            "attached_devices": list(self.attached_devices),
        }


def _configured_port(config: dict[str, Any]) -> int | None:
    raw = config.get("SENDSPIN_SOURCE_PORT")
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def status(config: dict[str, Any]) -> SourceStatus:
    """The source's state, for the bridge status and the hardware read model.

    Never leaves the process: the snapshot and the armed-relay list are in
    memory, so this is safe on the read path without a thread hop.
    """
    current = source_state.snapshot()
    return SourceStatus(
        enabled=current.enabled,
        port=current.port if current.port is not None else _configured_port(config),
        phone=current.phone,
        players=current.players,
        streaming=current.streaming,
        error=current.error,
        pairing_error=current.pairing_error,
        pairing_seconds_left=current.pairing_seconds_left(),
        pairing_adapters=tuple(current.pairing_adapters),
        attached_devices=tuple(relay.armed_devices()),
    )


async def _capture_available() -> bool:
    """Whether this host can capture a phone's Bluetooth audio at all.

    The fact comes from the hardware inventory's capture capability (PipeWire's
    tools are present), never from whether a phone is streaming right now: a
    paired phone that is silent is a capture-capable host.
    """
    from sendspin_bridge.services.hardware import inventory

    try:
        read = await asyncio.to_thread(inventory.read_inventory)
    except inventory.HardwareUnavailable:
        return False
    return read.capabilities.capture.available


async def start() -> dict[str, Any]:
    """Start the source from the bridge config; a second call is a no-op.

    A host without the capture capability would answer 200 and then never
    stream, so it is refused with the documented 422 instead of starting a
    source that cannot work.
    """
    from sendspin_bridge.bridge.orchestrator import _resolve_config_dir
    from sendspin_bridge.config import load_config

    if not manager.running():
        available = await _capture_available()
        if not available and not manager.running():
            raise UseCaseError(
                422,
                "capture_unavailable",
                "This host cannot capture Bluetooth audio: PipeWire's wpctl is not available.",
            )
    config = await asyncio.to_thread(load_config)
    return await manager.start(config, _resolve_config_dir())


async def stop() -> dict[str, Any]:
    """Stop the source, its relays and its pairing agent; a second call is a no-op."""
    return await manager.stop()


async def set_players(device_id: str, enabled: bool) -> dict[str, Any]:
    """Arm a speaker to play the phone's capture, or send it back to Music Assistant.

    The client is resolved first even when disarming, so an unknown device is a
    404 rather than a silent no-op (the route behaved this way before E3).
    """
    from sendspin_bridge.application.status import find_client

    client = find_client(device_id)
    if not enabled:
        await asyncio.to_thread(relay.disarm, device_id)
        return {"device_id": device_id, "playing_source": False}
    # The speaker the bridge drives itself: relay the phone's capture straight
    # to its sink.  The device stays armed, so a phone that reconnects (or a
    # bridge restart) resumes it.
    sink = getattr(client, "bluetooth_sink_name", None) or getattr(client, "sink_name", None)
    if not sink:
        raise conflict("That speaker has no audio output", code="source_sink_missing")
    await asyncio.to_thread(relay.arm, device_id, str(sink))
    return {"device_id": device_id, "playing_source": True}


async def open_pairing_window(seconds: int = DEFAULT_PAIRING_WINDOW_S) -> dict[str, Any]:
    """Make the adapters discoverable and pairable for a couple of minutes."""
    return await pairing.open_window(seconds)


async def close_pairing_window() -> None:
    """Close the phone pairing window and stop being discoverable."""
    await pairing.close_window()
