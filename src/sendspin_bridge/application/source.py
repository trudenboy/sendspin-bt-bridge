"""Use cases for the Sendspin source: the phone→Music Assistant feed as one entity.

The routes are adapters over these functions; the source's runtime state is read through
the typed snapshot in :mod:`sendspin_bridge.services.source.state`.  Nothing here touches
HTTP: a failure is a :class:`UseCaseError`, mapped by the API adapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.services.source import devices as source_devices
from sendspin_bridge.services.source import inputs as source_inputs
from sendspin_bridge.services.source import manager, pairing
from sendspin_bridge.services.source import state as source_state

__all__ = [
    "SourceStatus",
    "add_local_input",
    "cancel_ma_pairing",
    "close_pairing_window",
    "open_ma_pairing",
    "open_pairing_window",
    "remove_input",
    "rename_local_input",
    "resolve_server_url",
    "select_input",
    "set_target",
    "start",
    "status",
    "stop",
    "targets",
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
    target_player: str | None
    target_error: str | None
    active_input: str | None
    inputs: tuple[source_inputs.SourceInput, ...]
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
            "target_player": self.target_player,
            "target_error": self.target_error,
            "active_input": self.active_input,
            "inputs": [entry.as_dict() for entry in self.inputs],
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
        target_player=current.target_player,
        target_error=current.target_error,
        active_input=_effective_active(current),
        inputs=tuple(
            source_inputs.build_inputs(
                config,
                active_input=_effective_active(current),
                bluetooth=_source_devices(config, current.phone_mac, current.paired_phone_mac),
            )
        ),
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
    from sendspin_bridge.services.source.capture.router import SourceInputRouter
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
    base_backend = backend_for(server_name)
    if base_backend is None:
        raise UseCaseError(422, "capture_unavailable", "No audio server answers; the source cannot be captured.")
    active_input = str(config.get("SENDSPIN_SOURCE_ACTIVE") or "").strip() or None
    backend = SourceInputRouter(base_backend, active_input)
    server_url = await resolve_server_url(config)
    if not server_url:
        raise UseCaseError(
            422,
            "ma_unreachable",
            "Music Assistant's Sendspin address is unknown: set SENDSPIN_SERVER or connect a player first.",
        )
    target_player = str(config.get("SENDSPIN_SOURCE_TARGET") or "").strip() or None
    settings = SourceSettings(
        server_url=server_url,
        state_dir=_resolve_config_dir(),
        client_name=f"Sendspin BT Bridge ({_bridge_name(config)})",
        target_player=target_player,
        active_input=active_input,
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


def _selected_target(config: dict[str, Any] | None) -> str | None:
    return str((config or {}).get("SENDSPIN_SOURCE_TARGET") or "").strip() or None


async def targets() -> dict[str, Any]:
    """The Music Assistant players/groups the source can be pinned to.

    No MA API token means the operator chooses the target inside Music
    Assistant; the UI keeps the selector disabled rather than hiding the
    feature, so the reason stays visible.
    """
    from sendspin_bridge.config import load_config
    from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials
    from sendspin_bridge.services.source.target import fetch_targets

    config = await asyncio.to_thread(load_config)
    selected = _selected_target(config)
    ma_url, ma_token = get_ma_api_credentials()
    if not (ma_url and ma_token):
        return {"targets": [], "selected": selected, "ma_api_available": False, "error": None}
    client = manager.client()
    client_id = getattr(client, "client_id", None) if client is not None else None
    try:
        found, _uri = await fetch_targets(ma_url, ma_token, client_id)
    except Exception:
        logger.warning("Source: Music Assistant target list failed", exc_info=True)
        return {
            "targets": [],
            "selected": selected,
            "ma_api_available": True,
            "error": "ma_api_unreachable",
        }
    return {"targets": found, "selected": selected, "ma_api_available": True, "error": None}


async def set_target(target: str | None) -> dict[str, Any]:
    """Pin automatic playback to one MA queue (or clear it), persisting the choice."""
    from sendspin_bridge.config import update_config
    from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials
    from sendspin_bridge.services.source.target import fetch_targets

    client = manager.client()
    if client is None:
        raise UseCaseError(409, "source_off", "The source is off; enable it first.")
    chosen = str(target or "").strip() or None
    name: str | None = None
    if chosen is not None:
        ma_url, ma_token = get_ma_api_credentials()
        if not (ma_url and ma_token):
            raise UseCaseError(
                409,
                "ma_api_unavailable",
                "Add a Music Assistant API token to choose the playback target; until then choose it in Music Assistant.",
            )
        try:
            found, _uri = await fetch_targets(ma_url, ma_token, getattr(client, "client_id", None))
        except Exception as exc:
            raise UseCaseError(502, "ma_api_unreachable", "Music Assistant's API did not answer.") from exc
        match = next((entry for entry in found if entry["player_id"] == chosen), None)
        if match is None:
            raise UseCaseError(404, "unknown_target", "That Music Assistant player or group is not available.")
        name = str(match.get("name") or chosen)
    await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCE_TARGET", chosen))
    target_error = await client.set_target(chosen)
    return {"target_player": chosen, "target_name": name, "target_error": target_error}


async def _hardware_audio() -> Any:
    """The audio half of the hardware read, or None when the host cannot answer."""
    try:
        from sendspin_bridge.application import hardware as hardware_uc

        snapshot = await hardware_uc.snapshot()
    except Exception:
        logger.warning("Source: local audio inputs could not be read", exc_info=True)
        return None
    return snapshot


def _effective_active(current: Any) -> str | None:
    raw = current.active_input
    if raw is None and current.phone_mac:
        return source_inputs.bluetooth_id(current.phone_mac)
    return None if raw in (None, "none") else raw


def _active_id(config: dict[str, Any], current: Any) -> str | None:
    if current.enabled:
        return _effective_active(current)
    raw = str(config.get("SENDSPIN_SOURCE_ACTIVE") or "").strip() or None
    return None if raw in (None, "none") else raw


def _configured_inputs(config: dict[str, Any], current: Any, snapshot: Any) -> tuple[list[Any], list[Any]]:
    bluetooth = _source_devices(config, current.phone_mac, current.paired_phone_mac)
    audio = getattr(getattr(snapshot, "audio", None), "sources", None)
    active = _active_id(config, current)
    configured = source_inputs.build_inputs(
        config,
        active_input=active,
        bluetooth=bluetooth,
        audio_sources=audio,
    )
    return configured, list(audio or ())


async def inputs(*, include_monitors: bool = False) -> dict[str, Any]:
    """Configured sources and local inputs available to the add dialog."""
    from sendspin_bridge.config import load_config
    from sendspin_bridge.services.hardware.capabilities import classify_audio_server

    config = await asyncio.to_thread(load_config) or {}
    current = source_state.snapshot()
    snapshot = await _hardware_audio()
    active = _active_id(config, current)
    if snapshot is None:
        configured = source_inputs.build_inputs(
            config,
            active_input=active,
            bluetooth=_source_devices(config, current.phone_mac, current.paired_phone_mac),
        )
        return {
            "active_input": active,
            "inputs": [entry.as_dict() for entry in configured],
            "local_candidates": [],
            "capture_available": False,
            "capture_kind": "unknown",
        }
    configured, audio = _configured_inputs(config, current, snapshot)
    configured_ids = {entry.id for entry in configured}
    candidates = [
        entry.as_dict()
        for entry in source_inputs.local_candidates(audio, include_monitors=include_monitors)
        if entry.id not in configured_ids
    ]
    capabilities = snapshot.capabilities
    return {
        "active_input": active,
        "inputs": [entry.as_dict() for entry in configured],
        "local_candidates": candidates,
        "capture_available": bool(capabilities.capture),
        "capture_kind": classify_audio_server(capabilities.audio_server or ""),
    }


async def add_local_input(node: str, name: str | None = None, *, allow_monitor: bool = False) -> dict[str, Any]:
    """Save a local audio input; adding never enables the source or steals the active one."""
    from sendspin_bridge.config import load_config, update_config

    cleaned_node = str(node or "").strip()
    if not cleaned_node:
        raise UseCaseError(422, "input_unavailable", "A local audio input needs its source name.")
    config = await asyncio.to_thread(load_config) or {}
    existing = source_inputs.local_profiles(config)
    if any(profile["node"] == cleaned_node for profile in existing):
        raise UseCaseError(409, "source_duplicate", "That local audio input is already saved.")
    snapshot = await _hardware_audio()
    if snapshot is not None and not snapshot.capabilities.capture:
        raise UseCaseError(422, "capture_unavailable", "This host cannot capture audio.")
    audio = getattr(getattr(snapshot, "audio", None), "sources", None) if snapshot is not None else None
    match = next((source for source in audio or () if str(getattr(source, "name", "")) == cleaned_node), None)
    if match is not None and getattr(match, "monitor_of", None) and not allow_monitor:
        raise UseCaseError(
            422,
            "monitor_not_allowed",
            "This input mirrors another output. Enable system-audio capture explicitly to use it.",
        )
    display = str(name or "").strip() or str(getattr(match, "description", "") or "").strip() or cleaned_node
    profile = {"id": source_inputs.local_id(cleaned_node), "kind": "local", "node": cleaned_node, "name": display}
    chosen_active = str(config.get("SENDSPIN_SOURCE_ACTIVE") or "").strip() or None
    current = source_state.snapshot()
    if chosen_active == "none":
        chosen_active = profile["id"]
    elif chosen_active is None and not (current.enabled and current.phone_mac):
        # A phone already streaming under the legacy auto policy is the
        # active source; adding a local input must not steal it.
        chosen_active = profile["id"]
    profiles = [*existing, profile]

    def mutate(cfg: dict[str, Any]) -> None:
        cfg["SENDSPIN_SOURCES"] = profiles
        cfg["SENDSPIN_SOURCE_ACTIVE"] = chosen_active

    await asyncio.to_thread(update_config, mutate)
    client = manager.client()
    if client is not None and chosen_active == profile["id"]:
        with contextlib.suppress(Exception):
            await client.set_active(profile["id"])
    source_state.publish(active_input=chosen_active)
    entry = source_inputs.SourceInput(
        id=profile["id"],
        kind="local",
        name=display,
        description=str(getattr(match, "description", "") or ""),
        active=chosen_active == profile["id"],
        available=match is not None,
        monitor=bool(getattr(match, "monitor_of", None)),
        node=cleaned_node,
    )
    return entry.as_dict()


async def rename_local_input(source_id: str, name: str) -> dict[str, Any]:
    from sendspin_bridge.config import load_config, update_config

    parsed = source_inputs.parse_source_id(source_id)
    if parsed is None or parsed[0] != "local":
        raise UseCaseError(422, "unsupported_source", "Only local inputs can be renamed.")
    node = parsed[1]
    config = await asyncio.to_thread(load_config) or {}
    profiles = source_inputs.local_profiles(config)
    target = next((entry for entry in profiles if entry["node"] == node), None)
    if target is None:
        raise UseCaseError(404, "input_unknown", "That local audio input is not saved.")
    target["name"] = str(name or "").strip() or node
    update = [*[entry for entry in profiles if entry["node"] != node], target]
    await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCES", update))
    return {**target}


async def remove_input(source_id: str) -> None:
    from sendspin_bridge.config import load_config, update_config
    from sendspin_bridge.services.bluetooth import bt_remove_device

    parsed = source_inputs.parse_source_id(source_id)
    if parsed is None:
        raise UseCaseError(422, "invalid_source", "A source id looks like local:<node> or bluetooth:<MAC>.")
    kind, value = parsed
    config = await asyncio.to_thread(load_config) or {}
    active = str(config.get("SENDSPIN_SOURCE_ACTIVE") or "").strip() or None
    client = manager.client()
    if kind == "local":
        profiles = source_inputs.local_profiles(config)
        if not any(entry["node"] == value for entry in profiles):
            raise UseCaseError(404, "input_unknown", "That local audio input is not saved.")
        remaining = [entry for entry in profiles if entry["node"] != value]
        if active == source_id:
            active = "none"
        if client is not None and active == "none":
            with contextlib.suppress(Exception):
                await client.set_active(None)

        def mutate(cfg: dict[str, Any]) -> None:
            cfg["SENDSPIN_SOURCES"] = remaining
            cfg["SENDSPIN_SOURCE_ACTIVE"] = active

        await asyncio.to_thread(update_config, mutate)
        source_state.publish(active_input=active)
        return
    known = {
        source_inputs.bluetooth_id(device.mac)
        for device in source_devices.list_source_devices(excluded_macs=_configured_player_macs(config))
    }
    if source_id not in known:
        raise UseCaseError(404, "input_unknown", "That Bluetooth source is not paired.")
    if active == source_id:
        active = "none"
    if client is not None and active == "none":
        with contextlib.suppress(Exception):
            await client.set_active(None)
    await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCE_ACTIVE", active))
    source_state.publish(active_input=active)
    await asyncio.to_thread(bt_remove_device, value)
    source_devices.invalidate()


async def select_input(source_id: str) -> dict[str, Any]:
    """Persist and activate one source; the caller may pin a target separately."""
    from sendspin_bridge.config import load_config, update_config

    parsed = source_inputs.parse_source_id(source_id)
    if parsed is None:
        raise UseCaseError(422, "invalid_source", "A source id looks like local:<node> or bluetooth:<MAC>.")
    kind, value = parsed
    config = await asyncio.to_thread(load_config) or {}
    if kind == "local":
        known = {entry["node"] for entry in source_inputs.local_profiles(config)}
        if value not in known:
            raise UseCaseError(404, "input_unknown", "That local audio input is not saved.")
    else:
        known_macs = {
            device.mac for device in source_devices.list_source_devices(excluded_macs=_configured_player_macs(config))
        }
        if value not in known_macs:
            raise UseCaseError(404, "input_unknown", "That Bluetooth source is not paired.")
    client = manager.client()
    target_error: str | None = None
    if client is not None:
        try:
            target_error = await client.set_active(source_id)
        except Exception as exc:
            raise UseCaseError(409, getattr(exc, "code", "input_unavailable"), str(exc)) from exc
    await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCE_ACTIVE", source_id))
    source_state.publish(active_input=source_id)
    return {"active_input": source_id, "target_error": target_error}
