"""Use cases for the Sendspin source: the phone→Music Assistant feed as one entity.

The routes are adapters over these functions; the source's runtime state is read through
the typed snapshot in :mod:`sendspin_bridge.services.source.state`.  Nothing here touches
HTTP: a failure is a :class:`UseCaseError`, mapped by the API adapter.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.services.source import devices as source_devices
from sendspin_bridge.services.source import inputs as source_inputs
from sendspin_bridge.services.source import manager, pairing
from sendspin_bridge.services.source import state as source_state
from sendspin_bridge.services.source.read_model import (
    SourceStatus,
    audio_sources_from_read,
    devices_from_read,
    project_status,
)
from sendspin_bridge.services.source.read_model import (
    active_id as _active_id,
)

if TYPE_CHECKING:
    from sendspin_bridge.services.hardware.inventory import InventoryRead

__all__ = [
    "SourceStatus",
    "add_local_input",
    "cancel_ma_pairing",
    "clear_active_input",
    "close_pairing_window",
    "open_ma_pairing",
    "open_pairing_window",
    "remove_input",
    "rename_local_input",
    "resolve_server_url",
    "select_input",
    "set_active_mute",
    "set_active_volume",
    "set_target",
    "start",
    "status",
    "stop",
    "targets",
]

logger = logging.getLogger(__name__)

DEFAULT_PAIRING_WINDOW_S = 120
DEFAULT_SENDSPIN_PORT = 8927


def status(config: dict[str, Any], *, inventory: InventoryRead | None = None) -> SourceStatus:
    """Synchronous query; async callers supply their already-offloaded inventory.

    Only an enabled feed needs hardware facts: its inputs and the paired source
    devices come from the audio server and BlueZ.  A bridge whose feed is off
    still reports a pairing window, the bond it produced or an error, and those
    are runtime facts — building that block must not refresh the shared
    inventory on every status tick for the life of the process.
    """
    current = source_state.snapshot()
    if inventory is None and (current.enabled or config.get("SENDSPIN_SOURCE")):
        inventory = _read_inventory()
    return project_status(config, current, inventory, headsets=_headsets(config))


def _read_inventory() -> InventoryRead | None:
    from sendspin_bridge.services.hardware import inventory

    try:
        return inventory.read_inventory()
    except inventory.HardwareUnavailable:
        logger.debug("Source: hardware inventory unavailable", exc_info=True)
        return None


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
    from sendspin_bridge.services.bluetooth.headset import get_headset_profiles

    backend = SourceInputRouter(base_backend, active_input, headset=get_headset_profiles())
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
    await asyncio.to_thread(source_devices.invalidate)
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
    await asyncio.to_thread(source_devices.invalidate)


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
    found = [entry for entry in found if not _target_is_active_microphone(config, str(entry.get("name") or ""))]
    return {"targets": found, "selected": selected, "ma_api_available": True, "error": None}


async def set_target(target: str | None) -> dict[str, Any]:
    """Pin automatic playback to one MA queue (or clear it), persisting the choice."""
    from sendspin_bridge.config import load_config, update_config
    from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials
    from sendspin_bridge.services.source.target import fetch_targets

    client = manager.client()
    if client is None:
        raise UseCaseError(409, "source_off", "The source is off; enable it first.")
    config = await asyncio.to_thread(load_config)
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
        if not match.get("supported", True):
            raise UseCaseError(
                422,
                "target_unsupported",
                "That Music Assistant player does not accept live audio sources; choose another player or group.",
            )
        if _target_is_active_microphone(config, str(match.get("name") or "")):
            raise UseCaseError(
                422,
                "target_is_source",
                "That player owns the active headset microphone and cannot also play it.",
            )
        name = str(match.get("name") or chosen)
    await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCE_TARGET", chosen))
    target_error = await client.set_target(chosen)
    return {"target_player": chosen, "target_name": name, "target_error": target_error}


async def _hardware_audio() -> InventoryRead | None:
    """Read the shared physical facts off the bridge loop, without building hardware UI."""
    return await asyncio.to_thread(_read_inventory)


def _active_headset_mac(config: dict[str, Any] | None, active_input: str | None) -> str | None:
    raw = str(active_input or "")
    if not raw.startswith("headset-mic:"):
        return None
    return raw.split(":", 1)[1].upper() or None


def _headset_player_name(config: dict[str, Any] | None, mac: str) -> str | None:
    for player in (config or {}).get("players") or ():
        backend = player.get("backend") if isinstance(player, dict) else None
        if isinstance(backend, dict) and str(backend.get("mac") or "").upper() == mac.upper():
            return str(player.get("name") or "").strip() or None
    return None


def _target_is_active_microphone(config: dict[str, Any] | None, target_name: str) -> bool:
    mac = _active_headset_mac(config, _active_id(config or {}, source_state.snapshot()))
    if mac is None:
        return False
    name = _headset_player_name(config, mac)
    return bool(name and name.casefold() == str(target_name or "").strip().casefold())


def _headsets(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    from sendspin_bridge.services.bluetooth.headset import headset_inputs

    try:
        active = _active_id(config or {}, source_state.snapshot())
        return headset_inputs(config, active_input=active)
    except Exception:
        logger.debug("Source: headset inputs could not be read", exc_info=True)
        return []


def _configured_inputs(
    config: dict[str, Any], current: source_state.SourceState, snapshot: InventoryRead | None
) -> tuple[list[Any], list[Any]]:
    view = project_status(config, current, snapshot, headsets=_headsets(config))
    return list(view.inputs), list(audio_sources_from_read(snapshot) or ())


async def inputs(*, include_monitors: bool = False) -> dict[str, Any]:
    """Configured sources and local inputs available to the add dialog."""
    from sendspin_bridge.config import load_config
    from sendspin_bridge.services.hardware.capabilities import classify_audio_server

    config = await asyncio.to_thread(load_config) or {}
    current = source_state.snapshot()
    snapshot = await _hardware_audio()
    active = _active_id(config, current)
    configured, audio = _configured_inputs(config, current, snapshot)
    configured_ids = {entry.id for entry in configured}
    candidates = [
        entry.as_dict()
        for entry in source_inputs.local_candidates(audio, include_monitors=include_monitors)
        if entry.id not in configured_ids
    ]
    capabilities = snapshot.capabilities if snapshot is not None else None
    return {
        "active_input": active,
        "inputs": [entry.as_dict() for entry in configured],
        "local_candidates": candidates,
        "capture_available": bool(capabilities and capabilities.capture.available),
        "capture_kind": classify_audio_server(capabilities.audio_server or "")
        if capabilities is not None
        else "unknown",
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
    if snapshot is not None and not snapshot.capabilities.capture.available:
        raise UseCaseError(422, "capture_unavailable", "This host cannot capture audio.")
    audio = audio_sources_from_read(snapshot)
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
        for device in devices_from_read(config, source_state.snapshot(), await _hardware_audio())
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
    await asyncio.to_thread(source_devices.invalidate)


async def _require_active_input() -> tuple[str, Any]:
    """The active input id and the running client, or the stable "nothing active" problem."""
    from sendspin_bridge.config import load_config

    config = await asyncio.to_thread(load_config) or {}
    active = _active_id(config, source_state.snapshot())
    client = manager.client()
    if active is None or client is None:
        raise UseCaseError(409, "no_active_input", "No source input is active.")
    return active, client


async def _input_row(active_input: str) -> dict[str, Any]:
    """The read-model row of *active_input*, the shape ``GET /bridge/source/inputs`` returns."""
    from dataclasses import replace

    from sendspin_bridge.config import load_config

    config = await asyncio.to_thread(load_config) or {}
    view = project_status(config, source_state.snapshot(), await _hardware_audio(), headsets=_headsets(config))
    row = next((entry for entry in view.inputs if entry.id == active_input), None)
    if row is not None:
        # The command just acted on this input, whatever a stale snapshot says.
        return (row if row.active else replace(row, active=True)).as_dict()
    parsed = source_inputs.parse_source_id(active_input)
    kind: Any = "local"
    value = active_input
    mac = node = None
    if parsed is not None:
        kind, value = parsed
        if kind == "local":
            node = value
        else:
            mac = value
    volume, muted = source_inputs.input_audio(config, active_input)
    return source_inputs.SourceInput(
        id=active_input,
        kind=kind,
        name=value,
        active=True,
        available=False,
        mac=mac,
        node=node,
        volume=volume,
        muted=muted,
    ).as_dict()


async def set_active_volume(volume: int) -> dict[str, Any]:
    """Set the active input's gain, apply it to its capture node, and remember it."""
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise UseCaseError(422, "invalid_volume", "The input volume is 0 to 100.")
    active, client = await _require_active_input()
    try:
        applied = bool(await client.set_input_gain(volume))
    except Exception:
        logger.warning("Source: applying the input gain failed", exc_info=True)
        applied = False
    if not applied:
        raise UseCaseError(409, "input_unavailable", "The active input has no capture node to apply the volume to.")
    from sendspin_bridge.config import save_source_input_state

    await asyncio.to_thread(save_source_input_state, active, volume=volume)
    row = await _input_row(active)
    row["volume"] = volume
    return row


async def set_active_mute(muted: bool) -> dict[str, Any]:
    """Mute or unmute the active input without stopping the stream to Music Assistant."""
    if not isinstance(muted, bool):
        raise UseCaseError(422, "invalid_mute", "The mute state is true or false.")
    active, client = await _require_active_input()
    try:
        applied = bool(await client.set_input_mute(muted))
    except Exception:
        logger.warning("Source: applying the input mute failed", exc_info=True)
        applied = False
    if not applied:
        raise UseCaseError(409, "input_unavailable", "The active input has no capture node to mute.")
    from sendspin_bridge.config import save_source_input_state

    await asyncio.to_thread(save_source_input_state, active, muted=muted)
    row = await _input_row(active)
    row["muted"] = muted
    return row


async def clear_active_input() -> None:
    """Release the active input (capture and hand-off) without switching the feed off.

    Idempotent: with nothing active there is nothing to release and nothing to
    persist, so the call is a no-op.  A successful clear stores the explicit
    "none" choice, exactly like :func:`select_input` stores a pick, so a restart
    does not silently fall back to the automatic Bluetooth policy.
    """
    from sendspin_bridge.config import load_config, update_config

    config = await asyncio.to_thread(load_config) or {}
    active = _active_id(config, source_state.snapshot())
    client = manager.client()
    if active is None and (client is None or getattr(client, "active_input", None) in (None, "none")):
        return
    if client is not None:
        try:
            await client.set_active(None)
        except Exception as exc:
            raise UseCaseError(409, getattr(exc, "code", "input_release_failed"), str(exc)) from exc
    await asyncio.to_thread(update_config, lambda cfg: cfg.__setitem__("SENDSPIN_SOURCE_ACTIVE", "none"))
    source_state.publish(active_input=None, phone=None, phone_mac=None)


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
    elif kind == "headset_microphone":
        known = {str(entry["mac"]).upper() for entry in _headsets(config)}
        if value not in known:
            raise UseCaseError(404, "input_unknown", "That headset microphone is not available.")
    else:
        known_macs = {
            device.mac for device in devices_from_read(config, source_state.snapshot(), await _hardware_audio())
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
