"""Volume, mute, play/pause and per-device latency — for one speaker or a group.

Volume and mute go straight to the speaker's PulseAudio sink; the daemon's
volume controller sees the sink change and tells Music Assistant, so MA's UI
follows without a round trip through MA.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Literal

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.runtime import bridge_loop, run_on_loop, submit_on_loop
from sendspin_bridge.application.status import find_client
from sendspin_bridge.config import load_config, save_device_buffer_setting, save_device_static_delay, save_device_volume
from sendspin_bridge.services.audio.pulse import get_sink_mute, set_sink_mute, set_sink_volume
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.ipc.commands import OpenPairingWindow, Pause, Play, SetMute, SetVolume
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot_pairs
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials, get_ma_group_for_player

logger = logging.getLogger(__name__)

PlaybackAction = Literal["pause", "play"]
LatencyField = Literal["static_delay_ms", "required_lead_time_ms", "min_buffer_ms"]

# ---------------------------------------------------------------------------
# Volume persistence: the sink changes at once, config.json one second after
# the last change (a slider drag is dozens of changes).
# ---------------------------------------------------------------------------

_volume_timers: dict[str, threading.Timer] = {}
_volume_timers_lock = threading.Lock()


def _persist_volume(mac: str, volume: int) -> None:
    with _volume_timers_lock:
        _volume_timers.pop(mac, None)
    save_device_volume(mac, volume)


def schedule_volume_persist(mac: str, volume: int) -> None:
    with _volume_timers_lock:
        old = _volume_timers.pop(mac, None)
        if old:
            old.cancel()
        for key in [k for k, t in _volume_timers.items() if not t.is_alive()]:
            del _volume_timers[key]
        timer = threading.Timer(1.0, _persist_volume, args=(mac, volume))
        timer.daemon = True
        _volume_timers[mac] = timer
        timer.start()


def _mac_of(client) -> str | None:
    return getattr(getattr(client, "bt_manager", None), "mac_address", None)


def _require_sink(client) -> str:
    sink = getattr(client, "bluetooth_sink_name", None)
    if not sink:
        raise UseCaseError(409, "no_sink", "The speaker has no audio sink yet")
    return sink


def _apply_volume(client, volume: int) -> bool:
    sink = getattr(client, "bluetooth_sink_name", None)
    if not sink or not set_sink_volume(sink, volume):
        return False
    client._update_status({"volume": volume})
    submit_on_loop(
        client._send_subprocess_command(SetVolume(value=volume)), description=f"set_volume for {client.player_name}"
    )
    mac = _mac_of(client)
    if mac:
        schedule_volume_persist(mac, volume)
    return True


def set_device_volume(device_id: str, volume: int) -> dict[str, Any]:
    client = find_client(device_id)
    _require_sink(client)
    volume = max(0, min(100, int(volume)))
    if not _apply_volume(client, volume):
        raise UseCaseError(502, "sink_update_failed", "The audio server rejected the volume change")
    return {"volume": volume}


def _group_members(group_id: str) -> list[tuple[Any, Any]]:
    pairs = build_device_snapshot_pairs(get_device_registry_snapshot().active_clients)
    # A group is addressed by its Sendspin group id or, when Sendspin does not
    # report one, by the Music Assistant sync group /api/v1/groups names it by.
    members = [
        (client, device)
        for client, device in pairs
        if group_id in (device.extra.get("group_id"), device.extra.get("ma_syncgroup_id"))
    ]
    if not members:
        raise UseCaseError(404, "unknown_group", f"Unknown group or no members on this bridge: {group_id}")
    return members


def set_group_volume(group_id: str, volume: int) -> dict[str, Any]:
    volume = max(0, min(100, int(volume)))
    results = [
        {"device_id": str(client.player_id), "ok": _apply_volume(client, volume)}
        for client, _ in _group_members(group_id)
    ]
    if not any(r["ok"] for r in results):
        raise UseCaseError(409, "no_sink", "No member of the group has an audio sink")
    return {"volume": volume, "results": results}


def set_device_mute(device_id: str, muted: bool | None) -> dict[str, Any]:
    """Set mute, or toggle when *muted* is ``None``."""
    client = find_client(device_id)
    sink = _require_sink(client)
    if not set_sink_mute(sink, muted):
        raise UseCaseError(502, "sink_update_failed", "The audio server rejected the mute change")
    actual = get_sink_mute(sink)
    if actual is None:
        actual = bool(muted) if muted is not None else not bool(client.status.get("muted"))
    client._update_status({"muted": actual})
    submit_on_loop(
        client._send_subprocess_command(SetMute(muted=actual)), description=f"set_mute for {client.player_name}"
    )
    return {"muted": actual}


def unmute_sink(device_id: str) -> dict[str, Any]:
    """Recovery: unmute the PulseAudio sink itself (muted at system level after a crash)."""
    client = find_client(device_id)
    sink = _require_sink(client)
    if not set_sink_mute(sink, False):
        raise UseCaseError(502, "sink_update_failed", "Failed to unmute sink")
    muted = get_sink_mute(sink)
    client._update_status({"sink_muted": bool(muted) if muted is not None else False})
    submit_on_loop(
        client._send_subprocess_command(SetMute(muted=False)), description=f"unmute_sink for {client.player_name}"
    )
    logger.info("Sink unmuted via recovery action for %s", client.player_name)
    return {"sink_muted": False}


def _send_playback(client, action: PlaybackAction, description: str) -> None:
    command = Pause() if action == "pause" else Play()
    if not submit_on_loop(client._send_subprocess_command(command), description=description):
        raise UseCaseError(503, "not_ready", "Could not schedule command")


def device_playback(device_id: str, action: PlaybackAction) -> dict[str, Any]:
    """Pause or play through the speaker's Sendspin connection (MA initiates, group sync is kept)."""
    client = find_client(device_id)
    if not client.is_running():
        raise UseCaseError(409, "not_running", "The speaker's player is not running")
    bridge_loop()
    _send_playback(client, action, f"{action} for {client.player_name}")
    return {"action": action}


def _ma_group_play(player_id: str) -> dict[str, Any] | None:
    """Resume through Music Assistant's persistent sync group, so all members resume together."""
    ma_url, ma_token = get_ma_api_credentials()
    if not (ma_url and ma_token):
        return None
    ma_group = get_ma_group_for_player(player_id)
    if not ma_group:
        return None
    from sendspin_bridge.services.music_assistant.ma_client import ma_group_play

    try:
        if run_on_loop(ma_group_play(ma_url, ma_token, ma_group["id"]), timeout=10.0, description="MA group play"):
            return ma_group
    except Exception as exc:
        logger.warning("MA group play failed for %s, falling back: %s", ma_group.get("id"), exc)
    return None


def group_playback(group_id: str, action: PlaybackAction) -> dict[str, Any]:
    members = [(c, d) for c, d in _group_members(group_id) if c.is_running()]
    if not members:
        raise UseCaseError(404, "unknown_group", "Group not found or no running members")
    client, device = members[0]
    if action == "play":
        ma_group = _ma_group_play(str(getattr(client, "player_id", "")))
        if ma_group:
            return {
                "action": action,
                "group_id": group_id,
                "ma_syncgroup_id": ma_group["id"],
                "ma_syncgroup_name": ma_group["name"],
            }
    _send_playback(client, action, f"{action} for group {group_id}")
    return {"action": action, "group_id": group_id, "group_name": device.extra.get("group_name")}


def all_playback(action: PlaybackAction) -> dict[str, Any]:
    """Pause or resume everything: one command per session group, solo players directly."""
    bridge_loop()
    count = 0
    pairs = build_device_snapshot_pairs(get_device_registry_snapshot().active_clients)
    seen_groups: set = set()
    seen_ma: set = set()
    for client, device in pairs:
        if not client.is_running():
            continue
        if action == "play":
            ma_group = get_ma_group_for_player(getattr(client, "player_id", ""))
            if ma_group:
                if ma_group["id"] in seen_ma:
                    continue
                seen_ma.add(ma_group["id"])
                if _ma_group_play(str(client.player_id)):
                    count += 1
                    continue
        gid = device.extra.get("ma_syncgroup_id") or device.extra.get("group_id")
        if gid:
            if gid in seen_groups:
                continue
            seen_groups.add(gid)
        command = Pause() if action == "pause" else Play()
        if submit_on_loop(client._send_subprocess_command(command), description=f"{action} for {client.player_name}"):
            count += 1
    return {"action": action, "count": count}


def set_latency(
    device_id: str,
    field: LatencyField,
    value: float,
    *,
    source: str = "manual",
    recommendation_revision: str | None = None,
) -> dict[str, Any]:
    """Hot-apply and persist one per-device latency setting."""
    max_value = 5000 if field == "static_delay_ms" else 30000
    if not 0 <= value <= max_value:
        raise UseCaseError(400, "out_of_range", f"{field} must be between 0 and {max_value}")
    client = find_client(device_id)
    if recommendation_revision is not None and recommendation_revision != client.status.get(
        "latency_suggestion_revision"
    ):
        raise UseCaseError(409, "stale_recommendation", "Latency recommendation changed; refresh and retry")
    applied = run_on_loop(client.apply_hot_config({field: value}), timeout=2.0, description="Latency hot-apply")
    if field not in (applied or ()):
        raise UseCaseError(503, "not_applied", "Latency value was not applied")
    mac = _mac_of(client)
    if field == "static_delay_ms" and mac:
        codec = client.status.get("bt_codec_name") or client.status.get("audio_format")
        save_device_static_delay(mac, round(value), source=source, codec=codec)
        client._update_status({"static_delay_source": source, "static_delay_codec": codec})
    elif mac:
        save_device_buffer_setting(mac, field, round(value))
    return {"field": field, "value": round(value)}


def open_pairing_window(device_id: str) -> None:
    """Let Music Assistant pair with the speaker's Sendspin player (when pairing is required)."""
    if not load_config().get("SENDSPIN_PAIRING", False):
        raise UseCaseError(409, "pairing_disabled", "Sendspin pairing is disabled")
    client = find_client(device_id)
    if not client.is_running():
        raise UseCaseError(409, "not_running", "Player daemon is not running")
    run_on_loop(client._send_subprocess_command(OpenPairingWindow()), timeout=2.0, description="pairing command")


TransportAction = Literal[
    "play",
    "pause",
    "stop",
    "next",
    "previous",
    "volume",
    "mute",
    "repeat_off",
    "repeat_one",
    "repeat_all",
    "shuffle",
    "unshuffle",
    "switch",
]


def transport(device_id: str, action: TransportAction, value: Any = None) -> None:
    """A native Sendspin controller command (lower latency than going through MA)."""
    client = find_client(device_id)
    supported = client.status.get("supported_commands")
    if supported is not None and action not in supported:
        raise UseCaseError(409, "unsupported_command", f"Command {action!r} not supported by device")
    if not run_on_loop(
        client.send_transport_command(action, value=value), timeout=5.0, description=f"transport {action}"
    ):
        raise UseCaseError(409, "not_running", "Daemon subprocess not running")
