"""Use cases for the Sendspin source: the phone→Music Assistant feed as one entity.

The routes are adapters over these functions; the source's runtime state is read through
the typed snapshot in :mod:`sendspin_bridge.services.source.state`.  Nothing here touches
HTTP: a failure is a :class:`UseCaseError`, mapped by the API adapter.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.services.source import devices as source_devices
from sendspin_bridge.services.source import manager, pairing
from sendspin_bridge.services.source import state as source_state

__all__ = [
    "SourceStatus",
    "cancel_ma_pairing",
    "close_pairing_window",
    "open_ma_pairing",
    "open_pairing_window",
    "resolve_server_url",
    "start",
    "status",
    "stop",
]

logger = logging.getLogger(__name__)

DEFAULT_PAIRING_WINDOW_S = 120
DEFAULT_SENDSPIN_PORT = 8927


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """The source's state, for the bridge status and the hardware read model."""

    enabled: bool
    state: str
    phone: str | None
    phone_mac: str | None
    paired_phone_mac: str | None
    streaming: bool
    server: str | None
    ma_connected: bool
    ma_paired: bool
    ma_pairing_required: bool
    ma_pairing_code: str | None
    ma_pairing_seconds_left: int
    ma_error: str | None
    pairing_seconds_left: int
    pairing_adapters: tuple[str, ...]
    devices: tuple[source_state.SourceDevice, ...]
    error: str | None

    def as_status_dict(self) -> dict[str, Any]:
        """The bridge-status shape (the keys ``SendspinSourceStatus`` reads)."""
        return {
            "enabled": self.enabled,
            "state": self.state,
            "phone": self.phone,
            "phone_mac": self.phone_mac,
            "paired_phone_mac": self.paired_phone_mac,
            "streaming": self.streaming,
            "server": self.server,
            "ma_connected": self.ma_connected,
            "ma_paired": self.ma_paired,
            "ma_pairing_required": self.ma_pairing_required,
            "ma_pairing_code": self.ma_pairing_code,
            "ma_pairing_seconds_left": self.ma_pairing_seconds_left,
            "ma_error": self.ma_error,
            "pairing_seconds_left": self.pairing_seconds_left,
            "pairing_adapters": list(self.pairing_adapters),
            "devices": [device.as_dict() for device in self.devices],
            "error": self.error,
        }


def _configured_player_macs(config: dict[str, Any]) -> set[str]:
    """MACs the bridge already plays to; they are not source devices."""
    macs: set[str] = set()
    for player in config.get("players") or ():
        if not isinstance(player, dict):
            continue
        backend = player.get("backend")
        if isinstance(backend, dict) and backend.get("type") == "bluetooth_a2dp":
            mac = str(backend.get("mac") or "").strip().upper()
            if mac:
                macs.add(mac)
    return macs


def _source_devices(
    config: dict[str, Any], phone_mac: str | None, paired_mac: str | None
) -> tuple[source_state.SourceDevice, ...]:
    devices = source_devices.list_source_devices(active_mac=phone_mac, excluded_macs=_configured_player_macs(config))
    if paired_mac and not any(device.mac == paired_mac.upper() for device in devices):
        devices = (*devices, source_state.SourceDevice(mac=paired_mac.upper(), name=paired_mac.upper(), active=False))
    return devices


def status(config: dict[str, Any]) -> SourceStatus:
    """The source's state, for the bridge status and the hardware read model."""
    current = source_state.snapshot()
    return SourceStatus(
        enabled=current.enabled,
        state=current.state,
        phone=current.phone,
        phone_mac=current.phone_mac,
        paired_phone_mac=current.paired_phone_mac,
        streaming=current.streaming,
        server=current.server,
        ma_connected=current.ma_connected,
        ma_paired=current.ma_paired,
        ma_pairing_required=current.enabled and not current.ma_paired,
        ma_pairing_code=current.ma_pairing_code,
        ma_pairing_seconds_left=current.ma_pairing_seconds_left(),
        ma_error=current.ma_error,
        pairing_seconds_left=current.pairing_seconds_left(),
        pairing_adapters=tuple(current.pairing_adapters),
        devices=_source_devices(config, current.phone_mac, current.paired_phone_mac) if current.enabled else (),
        error=current.error,
    )


async def resolve_server_url(config: dict[str, Any]) -> str | None:
    """The Sendspin server URL, reusing the value a connected player already resolved.

    ``SENDSPIN_SERVER=auto`` is common on HAOS: the players discover MA over mDNS and record
    the URL they dialled.  Before any player connects, discovery is the fallback.
    """
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot

    for client in get_device_registry_snapshot().active_clients:
        url = str(getattr(client, "connected_server_url", "") or "").strip()
        if url:
            return url

    host = str(config.get("SENDSPIN_SERVER") or "").strip()
    port = _port(config)
    if host and host.lower() not in ("auto", "discover"):
        if host.startswith(("ws://", "wss://")):
            return host
        return f"ws://{host}:{port}/sendspin"

    from sendspin_bridge.services.music_assistant.ma_discovery import discover_ma_servers

    try:
        servers = await discover_ma_servers(timeout=3.0)
    except Exception:
        logger.warning("Source: MA discovery failed", exc_info=True)
        return None
    for entry in servers:
        parsed = urlparse(str(entry.get("url") or ""))
        if parsed.hostname:
            return f"ws://{parsed.hostname}:{port}/sendspin"
    return None


def _port(config: dict[str, Any]) -> int:
    raw = config.get("SENDSPIN_PORT")
    try:
        return int(raw) if raw is not None else DEFAULT_SENDSPIN_PORT
    except (TypeError, ValueError):
        return DEFAULT_SENDSPIN_PORT


def _bridge_name(config: dict[str, Any]) -> str:
    name = str(config.get("BRIDGE_NAME") or "").strip()
    return name or "Bridge"


async def start(*, config: dict[str, Any] | None = None, persist: bool = True) -> dict[str, Any]:
    """Enable the source: resolve MA, pick the capture backend and connect the client."""
    from sendspin_bridge.bridge.orchestrator import _resolve_config_dir
    from sendspin_bridge.config import load_config, update_config
    from sendspin_bridge.services.hardware.capabilities import probe_audio_server, probe_capture
    from sendspin_bridge.services.source.backends import backend_for
    from sendspin_bridge.services.source.client import SourceSettings

    if manager.running():
        return {"started": True, "already": True}
    if config is None:
        config = await asyncio.to_thread(load_config)
    if config is None:  # load_config is typed loosely; keep the contract explicit
        raise UseCaseError(500, "config_unavailable", "The bridge configuration could not be read.")
    server_name = await asyncio.to_thread(probe_audio_server)
    if not probe_capture(server_name):
        raise UseCaseError(
            422,
            "capture_unavailable",
            f"This host cannot capture Bluetooth audio: the {server_name or 'no'} audio server "
            "does not expose the phone's recording source (needs wpctl, or pactl and parecord).",
        )
    backend = backend_for(server_name)
    if backend is None:
        raise UseCaseError(422, "capture_unavailable", "No audio server answers; the phone cannot be captured.")
    server_url = await resolve_server_url(config)
    if not server_url:
        raise UseCaseError(
            422,
            "ma_unreachable",
            "Music Assistant's Sendspin address is unknown: set SENDSPIN_SERVER or connect a player first.",
        )
    settings = SourceSettings(
        server_url=server_url,
        state_dir=_resolve_config_dir(),
        client_name=f"Sendspin BT Bridge ({_bridge_name(config)})",
    )
    if persist:
        await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCE", True))
    return await manager.start(settings, backend)


async def stop() -> dict[str, Any]:
    """Disable the source and remember that choice."""
    from sendspin_bridge.config import update_config

    result = await manager.stop()
    await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCE", False))
    return result


async def open_ma_pairing() -> dict[str, Any]:
    """Open the Music Assistant pairing window and return its code."""
    client = manager.client()
    if client is None:
        raise UseCaseError(409, "source_off", "The source is off; enable it first.")
    try:
        return await client.open_ma_pairing()
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == "ma_already_paired":
            raise UseCaseError(409, "ma_already_paired", str(exc)) from exc
        raise UseCaseError(422, code or "ma_pairing_failed", str(exc)) from exc


async def cancel_ma_pairing(*, forget: bool = False) -> None:
    """Close the Music Assistant pairing window; ``forget`` also drops the stored PSK."""
    client = manager.client()
    if client is None:
        raise UseCaseError(409, "source_off", "The source is off; enable it first.")
    await client.cancel_ma_pairing(forget=forget)


async def open_pairing_window(seconds: int = DEFAULT_PAIRING_WINDOW_S) -> dict[str, Any]:
    """Make the adapters discoverable and pairable so a phone can pair."""
    return await pairing.open_window(seconds)


async def close_pairing_window() -> None:
    """Close the phone pairing window and stop being discoverable."""
    await pairing.close_window()


async def use_device(mac: str) -> dict[str, Any]:
    """Make a paired source device the one the source captures."""
    client = manager.client()
    if client is None:
        raise UseCaseError(409, "source_off", "The source is off; enable it first.")
    try:
        await client.use_device(mac)
    except Exception as exc:
        code = getattr(exc, "code", None) or "device_not_streaming"
        raise UseCaseError(409, code, str(exc)) from exc
    source_devices.invalidate()
    return {"active": mac.upper()}


async def forget_device(mac: str) -> None:
    """Forget a paired source device: stop its capture, then remove the bond."""
    from sendspin_bridge.bluetooth.address import DeviceAddress

    address = DeviceAddress.parse(mac)
    if address is None:
        raise UseCaseError(422, "invalid_mac", "A source device address looks like AA:BB:CC:DD:EE:FF.")
    client = manager.client()
    if client is not None:
        await client.forget_device(address.colons)
    else:
        from sendspin_bridge.services.bluetooth import bt_remove_device

        await asyncio.to_thread(bt_remove_device, address.colons)
    source_devices.invalidate()
