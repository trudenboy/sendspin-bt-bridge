"""
API Blueprint for sendspin-bt-bridge.

All /api/* routes and the helper functions they depend on.
"""

import asyncio
import concurrent.futures
import functools
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, Response, jsonify, request

import state
from config import (
    BUILD_DATE,
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    VERSION,
    load_config,
)
from config import (
    _config_lock as config_lock,
)
from config import (
    save_device_volume as _save_device_volume,
)
from services import (
    bt_remove_device as _bt_remove_device,
)
from services import (
    persist_device_enabled as _persist_device_enabled,
)
from services.bluetooth import _AUDIO_UUIDS
from services.pulse import (
    get_server_name,
    get_sink_mute,
    list_sinks,
    set_sink_mute,
    set_sink_volume,
)
from state import (
    _adapter_cache_lock,
    create_scan_job,
    finish_scan_job,
    get_adapter_name,
    get_scan_job,
    is_scan_running,
    load_adapter_name_cache,
)
from state import clients as _clients
from state import (
    clients_lock as _clients_lock,
)

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (avoid recompilation per request)
# ---------------------------------------------------------------------------

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DEV_PAT = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)")
_NEW_DEV_PAT = re.compile(r"\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.*)")
_CHG_NAME_PAT = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s+(.*)")
_CHG_RSSI_PAT = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+RSSI:")
_SHOW_CTRL_PAT = re.compile(r"^Controller\s+([0-9A-Fa-f:]{17})")
_SHOW_DEV_PAT = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})")

# ---------------------------------------------------------------------------
# Volume persistence debounce — decouple immediate pactl call from slow disk write
# ---------------------------------------------------------------------------

_volume_timers: dict[str, threading.Timer] = {}
_volume_timers_lock = threading.Lock()


def _persist_volume(mac: str, volume: int) -> None:
    """Write volume to config.json (called via debounce timer, not inline)."""
    with _volume_timers_lock:
        _volume_timers.pop(mac, None)
    _save_device_volume(mac, volume)


def _schedule_volume_persist(mac: str, volume: int) -> None:
    """Schedule a debounced config.json write 1 s after the last volume change."""
    with _volume_timers_lock:
        old = _volume_timers.pop(mac, None)
        if old:
            old.cancel()
        t = threading.Timer(1.0, _persist_volume, args=(mac, volume))
        t.daemon = True
        _volume_timers[mac] = t
        t.start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _detect_runtime() -> str:
    """Detect whether running under systemd, HA addon, or Docker. Result is cached."""
    if os.path.exists("/etc/systemd/system/sendspin-client.service") or os.path.exists(
        "/run/systemd/system/sendspin-client.service"
    ):
        return "systemd"
    elif os.path.exists("/data/options.json"):
        return "ha_addon"
    else:
        return "docker"


def get_client_status_for(client):
    """Get status dict for a specific client."""
    try:
        if client is None:
            return {
                "connected": False,
                "server_connected": False,
                "bluetooth_connected": False,
                "bluetooth_available": False,
                "playing": False,
                "error": "Client not running",
                "version": VERSION,
                "build_date": BUILD_DATE,
                "bluetooth_mac": None,
            }

        if not hasattr(client, "status"):
            return {
                "connected": False,
                "server_connected": False,
                "bluetooth_connected": False,
                "bluetooth_available": False,
                "playing": False,
                "error": "Client initializing",
                "version": VERSION,
                "build_date": BUILD_DATE,
                "bluetooth_mac": None,
            }

        with client._status_lock:
            status = client.status.copy()

        if "uptime_start" in status:
            uptime = datetime.now() - status["uptime_start"]
            status["uptime"] = str(timedelta(seconds=int(uptime.total_seconds())))
            del status["uptime_start"]

        status["version"] = VERSION
        status["build_date"] = BUILD_DATE
        status["connected"] = client.is_running()
        status["player_name"] = getattr(client, "player_name", None)
        status["listen_port"] = getattr(client, "listen_port", None)
        status["server_host"] = getattr(client, "server_host", None)
        status["server_port"] = getattr(client, "server_port", None)
        status["static_delay_ms"] = getattr(client, "static_delay_ms", None)
        status["connected_server_url"] = getattr(client, "connected_server_url", "") or (
            f"ws://{client.server_host}:{client.server_port}/sendspin"
            if getattr(client, "server_host", None) and client.server_host.lower() not in ("auto", "discover", "")
            else ""
        )

        bt_mgr = getattr(client, "bt_manager", None)
        status["bluetooth_mac"] = bt_mgr.mac_address if bt_mgr else None
        status["bluetooth_adapter"] = (bt_mgr.effective_adapter_mac or bt_mgr.adapter) if bt_mgr else None
        adapter_name = None
        if bt_mgr:
            lookup_mac = bt_mgr.effective_adapter_mac or bt_mgr.adapter
            if lookup_mac:
                adapter_name = get_adapter_name(lookup_mac.upper())
        status["bluetooth_adapter_name"] = adapter_name
        status["bluetooth_adapter_hci"] = getattr(bt_mgr, "adapter_hci_name", "") if bt_mgr else ""
        status["has_sink"] = bool(getattr(client, "bluetooth_sink_name", None))
        status["sink_name"] = getattr(client, "bluetooth_sink_name", None)
        status["bt_management_enabled"] = getattr(client, "bt_management_enabled", True)

        logger.debug("Status retrieved: %s", status)
        return status

    except Exception as e:
        logger.exception("Error getting client status: %s", e)
        return {
            "connected": False,
            "server_connected": False,
            "bluetooth_connected": False,
            "bluetooth_available": False,
            "playing": False,
            "error": str(e),
            "version": VERSION,
            "build_date": BUILD_DATE,
            "bluetooth_mac": None,
        }


def get_client_status():
    """Get status from the first client (backward compatibility)."""
    if not _clients:
        return {
            "connected": False,
            "server_connected": False,
            "bluetooth_connected": False,
            "bluetooth_available": False,
            "playing": False,
            "error": "No clients",
            "version": VERSION,
            "build_date": BUILD_DATE,
            "bluetooth_mac": None,
        }
    return get_client_status_for(_clients[0])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@api_bp.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart the service (systemd, HA addon, or Docker)."""
    runtime = _detect_runtime()
    try:
        if runtime == "systemd":

            def _do_systemd():
                time.sleep(0.5)
                subprocess.run(
                    ["systemctl", "restart", "sendspin-client"],
                    capture_output=True,
                    timeout=10,
                )

            threading.Thread(target=_do_systemd, daemon=True).start()
        elif runtime == "ha_addon":

            def _do_ha_restart():
                import urllib.request as _ur

                time.sleep(0.5)
                token = os.environ.get("SUPERVISOR_TOKEN", "")
                if token:
                    try:
                        req = _ur.Request(
                            "http://supervisor/addons/self/restart",
                            data=b"{}",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json",
                            },
                            method="POST",
                        )
                        _ur.urlopen(req, timeout=15)
                    except Exception as e:
                        logger.warning("Supervisor restart failed: %s; falling back to SIGTERM", e)
                        try:
                            os.kill(1, signal.SIGTERM)
                        except ProcessLookupError:
                            os.kill(os.getpid(), signal.SIGTERM)
                else:
                    os.kill(os.getpid(), signal.SIGTERM)

            threading.Thread(target=_do_ha_restart, daemon=True).start()
        else:

            def _do_docker():
                time.sleep(0.5)
                try:
                    os.kill(1, signal.SIGTERM)
                except ProcessLookupError:
                    os.kill(os.getpid(), signal.SIGTERM)

            threading.Thread(target=_do_docker, daemon=True).start()

        return jsonify({"success": True, "runtime": runtime})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/volume", methods=["POST"])
def set_volume():
    """Set player volume. Accepts player_name (single), player_names (list), or neither (all)."""
    try:
        data = request.get_json()
        try:
            volume = max(0, min(100, int(data.get("volume", 100))))
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Invalid volume value"}), 400
        player_names = data.get("player_names")
        player_name = data.get("player_name")
        group_id = data.get("group_id")

        if group_id is not None:
            targets = [c for c in _clients if c.status.get("group_id") == group_id]
        elif player_names is not None:
            targets = [c for c in _clients if getattr(c, "player_name", None) in player_names]
        elif player_name:
            targets = [c for c in _clients if getattr(c, "player_name", None) == player_name]
        else:
            targets = list(_clients)

        def _set_one(client):
            if not client.bluetooth_sink_name:
                return None
            ok = set_sink_volume(client.bluetooth_sink_name, volume)
            if ok:
                client._update_status({"volume": volume})
                mac = getattr(getattr(client, "bt_manager", None), "mac_address", None)
                if mac:
                    _schedule_volume_persist(mac, volume)
            return {"player": getattr(client, "player_name", "?"), "ok": ok}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets) or 1) as pool:
            results = [r for r in pool.map(_set_one, targets) if r is not None]
        if not results:
            return jsonify({"success": False, "error": "No clients available"}), 503
        return jsonify({"success": True, "volume": volume, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/mute", methods=["POST"])
def set_mute():
    """Toggle or set mute."""
    try:
        data = request.get_json() or {}
        player_names = data.get("player_names")
        player_name = data.get("player_name")
        mute_value = data.get("mute")

        if player_names is not None:
            targets = [c for c in _clients if getattr(c, "player_name", None) in player_names]
        elif player_name:
            targets = [c for c in _clients if getattr(c, "player_name", None) == player_name]
        else:
            targets = _clients[:1]

        results = []
        for client in targets:
            if client.bluetooth_sink_name:
                ok = set_sink_mute(client.bluetooth_sink_name, mute_value)
                if ok:
                    muted = get_sink_mute(client.bluetooth_sink_name)
                    if muted is None:
                        muted = bool(mute_value) if mute_value is not None else not client.status.get("muted", False)
                    client._update_status({"muted": muted})
                    results.append(
                        {
                            "player": getattr(client, "player_name", "?"),
                            "ok": True,
                            "muted": muted,
                        }
                    )
                else:
                    results.append({"player": getattr(client, "player_name", "?"), "ok": False})
        if not results:
            return jsonify({"success": False, "error": "Client not available"}), 503
        muted = results[0].get("muted", False) if results else False
        return jsonify({"success": True, "muted": muted, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/pause_all", methods=["POST"])
def pause_all():
    """Pause or play all running daemon subprocesses via WS controller command.

    Sends IPC cmd once per unique MA sync group — the daemon calls
    send_group_command() over the existing WS connection so MA is the
    playback initiator and group sync is preserved.
    Sending to every member of the same group would cause MA to break the
    group into separate sessions.
    """
    data = request.get_json() or {}
    action = data.get("action", "pause")
    loop = state.get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    seen_groups: set = set()
    count = 0
    for client in _clients:
        if not client.is_running():
            continue
        gid = client.status.get("group_id")
        if gid:
            if gid in seen_groups:
                continue  # already sent for this group
            seen_groups.add(gid)
        try:
            fut = asyncio.run_coroutine_threadsafe(client._send_subprocess_command({"cmd": action}), loop)
            fut.result(timeout=2.0)
            count += 1
        except Exception as _exc:
            logger.debug("Could not send %s to %s: %s", action, client.player_name, _exc)
    return jsonify({"success": True, "action": action, "count": count})


@api_bp.route("/api/group/pause", methods=["POST"])
def api_group_pause():
    """Pause or resume a specific MA sync group by group_id.

    For action="play": if MA API (MA_API_URL + MA_API_TOKEN) is configured,
    sends play to the persistent MA syncgroup player so all members resume in sync.
    Falls back to Sendspin session group command when MA API is not configured.

    For action="pause": always uses Sendspin session group command (one member,
    MA propagates to all).
    """
    data = request.get_json() or {}
    group_id = data.get("group_id")
    action = data.get("action", "pause")
    if not group_id:
        return jsonify({"success": False, "error": "group_id is required"}), 400

    loop = state.get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    # Find one running member of the specified group
    target = next(
        (c for c in _clients if c.is_running() and c.status.get("group_id") == group_id),
        None,
    )
    if not target:
        return jsonify({"success": False, "error": "Group not found or no running members"}), 404

    # For play: prefer MA API so the persistent syncgroup resumes all members in sync
    if action == "play":
        ma_url, ma_token = state.get_ma_api_credentials()
        if ma_url and ma_token:
            ma_group = state.get_ma_group_for_player(target.player_name)
            if ma_group:
                try:
                    from services.ma_client import ma_group_play

                    fut = asyncio.run_coroutine_threadsafe(ma_group_play(ma_url, ma_token, ma_group["id"]), loop)
                    ok = fut.result(timeout=10.0)
                    if ok:
                        return jsonify(
                            {
                                "success": True,
                                "action": action,
                                "group_id": group_id,
                                "ma_syncgroup_id": ma_group["id"],
                                "ma_syncgroup_name": ma_group["name"],
                            }
                        )
                except Exception as exc:
                    logger.warning("MA API group play failed, falling back: %s", exc)

    try:
        fut = asyncio.run_coroutine_threadsafe(target._send_subprocess_command({"cmd": action}), loop)
        fut.result(timeout=2.0)
        group_name = target.status.get("group_name")
        return jsonify({"success": True, "action": action, "group_id": group_id, "group_name": group_name})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@api_bp.route("/api/ma/groups", methods=["GET"])
def api_ma_groups():
    """Return all MA syncgroup players discovered from the MA API.

    Each group includes id, name, and members with id/name/state/volume/available.
    Returns empty list if MA API is not configured or discovery has not run yet.
    """
    return jsonify(state.get_ma_groups())


@api_bp.route("/api/ma/rediscover", methods=["POST"])
def api_ma_rediscover():
    """Re-run MA syncgroup discovery without restarting the addon.

    Reads current MA_API_URL / MA_API_TOKEN from config.json and updates the state cache.
    Useful after changing MA API credentials via the web UI.
    """
    cfg = load_config()
    ma_url = cfg.get("MA_API_URL", "").strip()
    ma_token = cfg.get("MA_API_TOKEN", "").strip()
    if not ma_url or not ma_token:
        return jsonify({"success": False, "error": "MA_API_URL or MA_API_TOKEN not configured"}), 400

    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    try:
        from services.ma_client import discover_ma_groups

        player_names = [c.player_name for c in _clients]
        fut = asyncio.run_coroutine_threadsafe(discover_ma_groups(ma_url, ma_token, player_names), loop)
        name_map, all_groups = fut.result(timeout=15.0)
        state.set_ma_api_credentials(ma_url, ma_token)
        state.set_ma_groups(name_map, all_groups)
        return jsonify(
            {
                "success": True,
                "syncgroups": len(all_groups),
                "mapped_players": len(name_map),
                "groups": [{"id": g["id"], "name": g["name"]} for g in all_groups],
            }
        )
    except Exception as exc:
        logger.warning("MA rediscover failed: %s", exc)
        return jsonify({"success": False, "error": str(exc)}), 500


@api_bp.route("/api/ma/nowplaying", methods=["GET"])
def api_ma_nowplaying():
    """Return current MA now-playing metadata.

    Returns {"connected": false} when MA integration is not active.
    Fields when connected: state, track, artist, album, image_url,
    elapsed, elapsed_updated_at, duration, shuffle, repeat,
    queue_index, queue_total, syncgroup_id.
    """
    if not state.is_ma_connected():
        return jsonify({"connected": False})
    return jsonify(state.get_ma_now_playing())


@api_bp.route("/api/ma/queue/cmd", methods=["POST"])
def api_ma_queue_cmd():
    """Send a playback control command to the active MA syncgroup queue.

    Body: {"action": "next"|"previous"|"shuffle"|"repeat"|"seek", "value": ...}
    - shuffle: value=true|false
    - repeat: value="off"|"all"|"one"
    - seek: value=<seconds int>
    """
    if not state.is_ma_connected():
        return jsonify({"success": False, "error": "MA not connected"}), 503

    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    value = data.get("value")

    if action not in ("next", "previous", "shuffle", "repeat", "seek"):
        return jsonify({"success": False, "error": f"Unknown action: {action}"}), 400

    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    try:
        from services.ma_monitor import send_queue_cmd

        fut = asyncio.run_coroutine_threadsafe(send_queue_cmd(action, value), loop)
        ok = fut.result(timeout=10.0)
        return jsonify({"success": ok})
    except Exception as exc:
        logger.warning("MA queue cmd %s failed: %s", action, exc)
        return jsonify({"success": False, "error": str(exc)}), 500


def pause_player():
    """Pause or play a single daemon subprocess via WS controller command.

    Sends IPC cmd to the target daemon which calls send_group_command() over
    the existing WS connection — MA is the playback initiator and can
    re-establish group sync.
    """
    data = request.get_json() or {}
    player_name = data.get("player_name", "")
    action = data.get("action", "pause")
    target = next((c for c in _clients if getattr(c, "player_name", None) == player_name), None)
    if not target or not target.is_running():
        return jsonify({"success": False, "error": "Player not found or not running"}), 404
    loop = state.get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    try:
        fut = asyncio.run_coroutine_threadsafe(target._send_subprocess_command({"cmd": action}), loop)
        fut.result(timeout=2.0)
        return jsonify({"success": True, "action": action, "count": 1})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@api_bp.route("/api/bt/reconnect", methods=["POST"])
def api_bt_reconnect():
    """Force reconnect a BT device (connect without re-pairing)."""
    try:
        data = request.get_json() or {}
        player_name = data.get("player_name")
        client = next(
            (c for c in _clients if getattr(c, "player_name", None) == player_name),
            None,
        )
        if client is None and _clients:
            client = _clients[0]
        if not client or not client.bt_manager:
            return jsonify({"success": False, "error": "No BT manager for this player"}), 503

        bt = client.bt_manager

        def _do_reconnect():
            try:
                bt.disconnect_device()
                time.sleep(1)
                bt.connect_device()
            except Exception as e:
                logger.error("Force reconnect failed: %s", e)

        threading.Thread(target=_do_reconnect, daemon=True).start()
        return jsonify({"success": True, "message": "Reconnect started"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/bt/pair", methods=["POST"])
def api_bt_pair():
    """Force re-pair a BT device. Device must be in pairing mode."""
    try:
        data = request.get_json() or {}
        player_name = data.get("player_name")
        client = next(
            (c for c in _clients if getattr(c, "player_name", None) == player_name),
            None,
        )
        if client is None and _clients:
            client = _clients[0]
        if not client or not client.bt_manager:
            return jsonify({"success": False, "error": "No BT manager for this player"}), 503

        bt = client.bt_manager

        def _do_pair():
            try:
                bt.pair_device()
                bt.connect_device()
            except Exception as e:
                logger.error("Force pair failed: %s", e)

        threading.Thread(target=_do_pair, daemon=True).start()
        return jsonify({"success": True, "message": "Pairing started (~25s)"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@api_bp.route("/api/bt/management", methods=["POST"])
def api_bt_management():
    """Release or reclaim the BT adapter for a player."""
    data = request.get_json() or {}
    player_name = data.get("player_name")
    enabled = data.get("enabled")
    if enabled is None:
        return jsonify({"success": False, "error": 'Missing "enabled" field'}), 400
    client = next((c for c in _clients if getattr(c, "player_name", None) == player_name), None)
    if not client and _clients:
        client = _clients[0]
    if not client:
        return jsonify({"success": False, "error": "No client found"}), 503
    enabled = bool(enabled)
    threading.Thread(target=client.set_bt_management_enabled, args=(enabled,), daemon=True).start()
    _persist_device_enabled(player_name, enabled)
    # Sync enabled state to HA Supervisor so the Configuration page reflects it
    try:
        with config_lock, open(CONFIG_FILE) as _f:
            _cfg = json.load(_f)
        threading.Thread(target=_sync_ha_options, args=(_cfg,), daemon=True).start()
    except Exception:
        pass
    action = "reclaimed" if enabled else "released"
    return jsonify({"success": True, "message": f"BT adapter {action}", "enabled": enabled})


@api_bp.route("/api/status")
def api_status():
    """Return status for all client instances."""
    with _clients_lock:
        snapshot = list(_clients)
    if not snapshot:
        return jsonify({"error": "No clients"}), 503
    if len(snapshot) == 1:
        return jsonify(get_client_status_for(snapshot[0]))
    first = get_client_status_for(snapshot[0])
    result = {**first, "devices": [get_client_status_for(c) for c in snapshot]}
    return jsonify(result)


def _build_groups_summary(clients: list) -> list[dict]:
    """Build a list of group objects from the current client list.

    Players sharing the same non-None group_id are merged into one group entry.
    Solo players (group_id=None) each appear as their own single-member group.
    """
    groups: dict[str | None, dict] = {}
    solo_counter = 0

    for client in clients:
        status = client.status
        gid = status.get("group_id")
        # Give each solo player a unique key so they don't merge
        key = gid if gid is not None else f"__solo_{solo_counter}"
        if gid is None:
            solo_counter += 1

        member = {
            "player_name": getattr(client, "player_name", None),
            "volume": status.get("volume", 100),
            "playing": bool(status.get("playing")),
            "connected": bool(status.get("connected")),
            "bluetooth_connected": bool(status.get("bluetooth_connected")),
        }

        if key not in groups:
            groups[key] = {
                "group_id": gid,
                "group_name": status.get("group_name"),
                "members": [],
            }

        groups[key]["members"].append(member)

    result = []
    for entry in groups.values():
        members = entry["members"]
        volumes = [m["volume"] for m in members]
        entry["avg_volume"] = round(sum(volumes) / len(volumes)) if volumes else 100
        entry["playing"] = any(m["playing"] for m in members)
        result.append(entry)

    return result


@api_bp.route("/api/groups")
def api_groups():
    """Return a list of MA player groups with their members.

    Players sharing the same group_id (assigned by MA when placed in a Sync Group)
    are returned as one entry. Solo players (not in any MA group) each appear as
    their own single-member entry with group_id=null.
    """
    with _clients_lock:
        snapshot = list(_clients)
    return jsonify(_build_groups_summary(snapshot))


@api_bp.route("/api/status/stream")
def api_status_stream():
    """Server-Sent Events endpoint — pushes status when it changes.

    Clients connect once and receive real-time updates instead of polling
    /api/status every 2 seconds.  A heartbeat comment is sent every 30 s to
    keep the connection alive through proxies.

    Uses ``threading.Condition.wait_for()`` to avoid the race between reading
    ``_status_version`` and blocking: the Condition lock ensures that any
    ``notify_status_changed()`` call either happens before we start waiting
    (so ``wait_for`` returns immediately) or wakes us up cleanly.
    """

    def _generate():
        last_version = -1
        while True:
            with state._status_condition:
                # Wait until version changes or 30 s elapse (heartbeat).
                # Capture last_version as default arg to avoid B023 late-binding.
                changed = state._status_condition.wait_for(
                    lambda v=last_version: state._status_version != v,
                    timeout=30,
                )

            if changed:
                last_version = state._status_version
                with _clients_lock:
                    snapshot = list(_clients)
                if snapshot:
                    if len(snapshot) == 1:
                        data = get_client_status_for(snapshot[0])
                    else:
                        first = get_client_status_for(snapshot[0])
                        data = {**first, "devices": [get_client_status_for(c) for c in snapshot]}
                    data["groups"] = _build_groups_summary(snapshot)
                    if state.is_ma_connected():
                        data["nowplaying"] = state.get_ma_now_playing()
                    yield f"data: {json.dumps(data)}\n\n"
            else:
                # 30 s timeout — send a keepalive comment so proxies don't close the connection
                yield ": heartbeat\n\n"

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sync_ha_options(config: dict) -> None:
    """Push current config to HA Supervisor options (no-op outside HA addon)."""
    if _detect_runtime() != "ha_addon":
        return
    try:
        import urllib.request as _ur

        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            return
        sup_devices = []
        for d in config.get("BLUETOOTH_DEVICES", []):
            entry = {"mac": d.get("mac", ""), "player_name": d.get("player_name", "")}
            if d.get("adapter"):
                entry["adapter"] = d["adapter"]
            if d.get("static_delay_ms"):
                entry["static_delay_ms"] = int(d["static_delay_ms"])
            if d.get("listen_host"):
                entry["listen_host"] = d["listen_host"]
            if d.get("listen_port"):
                entry["listen_port"] = int(d["listen_port"])
            if "enabled" in d:
                entry["enabled"] = bool(d["enabled"])
            if d.get("preferred_format"):
                entry["preferred_format"] = d["preferred_format"]
            sup_devices.append(entry)
        sup_adapters = [
            dict(
                {"id": a["id"], "mac": a.get("mac", "")},
                **({"name": a["name"]} if a.get("name") else {}),
            )
            for a in config.get("BLUETOOTH_ADAPTERS", [])
            if a.get("id")
        ]
        sup_opts = {
            "options": {
                "sendspin_server": config.get("SENDSPIN_SERVER", "auto"),
                "sendspin_port": int(config.get("SENDSPIN_PORT", 9000)),
                "bridge_name": config.get("BRIDGE_NAME", ""),
                "bridge_name_suffix": bool(config.get("BRIDGE_NAME_SUFFIX", False)),
                "tz": config.get("TZ", ""),
                "pulse_latency_msec": int(config.get("PULSE_LATENCY_MSEC", 200)),
                "prefer_sbc_codec": bool(config.get("PREFER_SBC_CODEC", False)),
                "bt_check_interval": int(config.get("BT_CHECK_INTERVAL", 10)),
                "bt_max_reconnect_fails": int(config.get("BT_MAX_RECONNECT_FAILS", 0)),
                "auth_enabled": bool(config.get("AUTH_ENABLED", False)),
                "bluetooth_devices": sup_devices,
                "bluetooth_adapters": sup_adapters,
            }
        }
        body = json.dumps(sup_opts).encode()
        req = _ur.Request(
            "http://supervisor/addons/self/options",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _ur.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning("Failed to sync Supervisor options: %s", e)


@api_bp.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Read or write the service configuration."""
    if request.method == "GET":
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()

        # Never expose secrets to the browser
        config.pop("AUTH_PASSWORD_HASH", None)
        config.pop("SECRET_KEY", None)

        # Enrich BLUETOOTH_DEVICES with resolved listen_port / listen_host from running clients
        client_map = {getattr(c, "player_name", None): c for c in _clients}
        mac_map = {getattr(getattr(c, "bt_manager", None), "mac_address", None): c for c in _clients}
        for dev in config.get("BLUETOOTH_DEVICES", []):
            client = client_map.get(dev.get("player_name")) or mac_map.get(dev.get("mac"))
            if client:
                if "listen_port" not in dev or not dev["listen_port"]:
                    dev["listen_port"] = getattr(client, "listen_port", None)
                if "listen_host" not in dev or not dev["listen_host"]:
                    dev["listen_host"] = getattr(client, "listen_host", None) or client.status.get("ip_address")

        return jsonify(config)

    # POST
    config = request.get_json()
    if not isinstance(config, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    # Validate top-level string fields
    for str_key in ("SENDSPIN_SERVER", "BRIDGE_NAME", "TZ", "LOG_LEVEL"):
        val = config.get(str_key)
        if val is not None and not isinstance(val, str):
            return jsonify({"error": f"{str_key} must be a string"}), 400

    # Validate BLUETOOTH_DEVICES entries
    bt_devices = config.get("BLUETOOTH_DEVICES", [])
    if not isinstance(bt_devices, list):
        return jsonify({"error": "BLUETOOTH_DEVICES must be an array"}), 400
    for dev in bt_devices:
        if not isinstance(dev, dict):
            return jsonify({"error": "Each device must be an object"}), 400
        mac = str(dev.get("mac", ""))
        if mac and not _MAC_RE.match(mac):
            return jsonify({"error": f"Invalid MAC address: {mac}"}), 400
        lp = dev.get("listen_port")
        if lp is not None:
            try:
                lp = int(lp)
                if not (1024 <= lp <= 65535):
                    raise ValueError
            except (ValueError, TypeError):
                return jsonify({"error": f"Invalid listen_port: {dev.get('listen_port')}"}), 400
        ki = dev.get("keepalive_interval")
        if ki is not None:
            try:
                ki = int(ki)
                if ki != 0 and not (30 <= ki <= 3600):
                    raise ValueError
            except (ValueError, TypeError):
                return jsonify(
                    {"error": f"Invalid keepalive_interval: {dev.get('keepalive_interval')} (must be 0 or 30-3600)"}
                ), 400

    # Validate BLUETOOTH_ADAPTERS entries
    bt_adapters = config.get("BLUETOOTH_ADAPTERS", [])
    if not isinstance(bt_adapters, list):
        return jsonify({"error": "BLUETOOTH_ADAPTERS must be an array"}), 400
    for adp in bt_adapters:
        if not isinstance(adp, dict):
            return jsonify({"error": "Each adapter must be an object"}), 400
        amac = str(adp.get("mac", ""))
        if amac and not _MAC_RE.match(amac):
            return jsonify({"error": f"Invalid adapter MAC address: {amac}"}), 400

    # Validate top-level port
    sp = config.get("SENDSPIN_PORT")
    if sp is not None:
        try:
            sp = int(sp)
            if not (1 <= sp <= 65535):
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid SENDSPIN_PORT: {config.get('SENDSPIN_PORT')}"}), 400

    # Strip unknown top-level keys (whitelist)
    _ALLOWED_POST_KEYS = {
        "SENDSPIN_SERVER",
        "SENDSPIN_PORT",
        "BRIDGE_NAME",
        "BRIDGE_NAME_SUFFIX",
        "BLUETOOTH_MAC",
        "BLUETOOTH_DEVICES",
        "BLUETOOTH_ADAPTERS",
        "TZ",
        "PULSE_LATENCY_MSEC",
        "PREFER_SBC_CODEC",
        "BT_CHECK_INTERVAL",
        "BT_MAX_RECONNECT_FAILS",
        "AUTH_ENABLED",
        "LAST_VOLUMES",
        "LAST_VOLUME",
        "LOG_LEVEL",
        "MA_API_URL",
        "MA_API_TOKEN",
        "_new_device_default_volume",
    }
    config = {k: v for k, v in config.items() if k in _ALLOWED_POST_KEYS}

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with config_lock:
        existing = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
                # Preserve keys that are never submitted via the form
                for key in (
                    "LAST_VOLUMES",
                    "LAST_VOLUME",
                    "AUTH_PASSWORD_HASH",
                    "SECRET_KEY",
                ):
                    if key in existing and key not in config:
                        config[key] = existing[key]
                # Preserve MA_API_TOKEN if form submitted empty (user didn't change it)
                if not config.get("MA_API_TOKEN") and existing.get("MA_API_TOKEN"):
                    config["MA_API_TOKEN"] = existing["MA_API_TOKEN"]
            except Exception as _exc:
                logger.debug("Could not read existing config for merge: %s", _exc)

        # Normalize MA_API_URL: add http:// scheme if missing
        ma_url = config.get("MA_API_URL", "").strip()
        if ma_url and "://" not in ma_url:
            config["MA_API_URL"] = f"http://{ma_url}"

        old_devices = {d["mac"]: d for d in existing.get("BLUETOOTH_DEVICES", []) if d.get("mac")}
        new_devices = {d["mac"]: d for d in config.get("BLUETOOTH_DEVICES", []) if d.get("mac")}

        client_adapter = {
            getattr(getattr(c, "bt_manager", None), "mac_address", None): getattr(
                getattr(c, "bt_manager", None), "_adapter_select", ""
            )
            for c in _clients
        }

        for mac, old_dev in old_devices.items():
            new_dev = new_devices.get(mac)
            adapter_changed = new_dev and new_dev.get("adapter") != old_dev.get("adapter")
            deleted = new_dev is None
            if deleted or adapter_changed:
                adapter_mac = client_adapter.get(mac) or ""
                _bt_remove_device(mac, adapter_mac)

        default_vol = config.pop("_new_device_default_volume", None)
        if default_vol is not None:
            last_volumes = config.setdefault("LAST_VOLUMES", existing.get("LAST_VOLUMES", {}))
            for mac in new_devices:
                if mac and mac not in last_volumes:
                    last_volumes[mac] = default_vol

        tmp = str(CONFIG_FILE) + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, str(CONFIG_FILE))
        except Exception:
            # Remove partial temp file on failure
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # Invalidate adapter name cache so next status poll picks up changes
    with _adapter_cache_lock:
        load_adapter_name_cache()

    _sync_ha_options(config)

    return jsonify({"success": True})


@api_bp.route("/api/set-password", methods=["POST"])
def api_set_password():
    """Set (or change) the standalone web UI password.

    Only available in non-HA-addon mode.  Requires the request body to contain
    a JSON object with a 'password' key (string, ≥8 characters).
    """
    if os.environ.get("SUPERVISOR_TOKEN"):
        return jsonify({"error": "Use HA user management in HA addon mode"}), 400

    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password", "")
    if not password:
        return jsonify({"error": "password is required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    from config import hash_password as _hash_pw

    pw_hash = _hash_pw(password)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with config_lock:
        existing: dict = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing["AUTH_PASSWORD_HASH"] = pw_hash
        tmp = str(CONFIG_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(existing, f, indent=2)
        os.replace(tmp, str(CONFIG_FILE))

    return jsonify({"success": True})


@api_bp.route("/api/settings/log_level", methods=["POST"])
def api_set_log_level():
    """Apply log level immediately (INFO or DEBUG) and persist to config.json."""
    data = request.get_json(force=True, silent=True) or {}
    level = str(data.get("level", "")).upper()
    if level not in ("INFO", "DEBUG"):
        return jsonify({"error": "level must be 'info' or 'debug'"}), 400

    # Apply to main process root logger immediately
    logging.getLogger().setLevel(getattr(logging, level))
    os.environ["LOG_LEVEL"] = level

    # Persist to config.json
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with config_lock:
        existing: dict = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing["LOG_LEVEL"] = level
        tmp = str(CONFIG_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(existing, f, indent=2)
        os.replace(tmp, str(CONFIG_FILE))

    # Propagate to all running subprocesses via stdin IPC
    loop = state.get_main_loop()
    if loop is not None:
        cmd = {"cmd": "set_log_level", "level": level}
        for client in _clients:
            if client.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(client._send_subprocess_command(cmd), loop).result(timeout=2.0)
                except Exception as exc:
                    logger.debug("Could not send set_log_level to %s: %s", client.player_name, exc)

    return jsonify({"success": True, "level": level})


@api_bp.route("/api/logs")
def api_logs():
    """Return real service logs (journalctl, Supervisor, or docker logs)."""
    lines = min(request.args.get("lines", 150, type=int), 500)
    try:
        runtime = _detect_runtime()
        if runtime == "systemd":
            result = subprocess.run(
                [
                    "journalctl",
                    "-u",
                    "sendspin-client",
                    "-n",
                    str(lines),
                    "--no-pager",
                    "--output=short-iso",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            log_lines = result.stdout.splitlines()
            if not log_lines and result.stderr:
                log_lines = result.stderr.splitlines()
        elif runtime == "ha_addon":
            import urllib.request as _ur

            token = os.environ.get("SUPERVISOR_TOKEN", "")
            if token:
                req = _ur.Request(
                    "http://supervisor/addons/self/logs",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "text/plain",
                    },
                )
                with _ur.urlopen(req, timeout=10) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                log_lines = text.splitlines()[-lines:]
            else:
                log_lines = ["(SUPERVISOR_TOKEN not available — check addon permissions)"]
        else:
            result = subprocess.run(
                ["docker", "logs", "--tail", str(lines), "sendspin-client"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            log_lines = (result.stdout + result.stderr).splitlines()

        if not log_lines:
            log_lines = ["(No logs available)"]

        return jsonify({"logs": log_lines, "runtime": runtime})
    except Exception as e:
        logger.error("Error reading logs: %s", e)
        return jsonify({"logs": [f"Error reading logs: {e}"]}), 500


@api_bp.route("/api/bt/adapters")
def api_bt_adapters():
    """List available Bluetooth adapters."""
    try:
        result = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
        macs = []
        for line in result.stdout.splitlines():
            if "Controller" not in line:
                continue
            parts = line.split()
            mac = next((p for p in parts if len(p) == 17 and p.count(":") == 5), None)
            if mac:
                macs.append(mac)
        adapters = []
        for i, mac in enumerate(macs):
            show_out = subprocess.run(
                ["bluetoothctl"],
                input=f"select {mac}\nshow\n",
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            powered = "Powered: yes" in show_out
            alias = next(
                (ln.split("Alias:")[1].strip() for ln in show_out.splitlines() if "Alias:" in ln),
                f"hci{i}",
            )
            adapters.append({"id": f"hci{i}", "mac": mac, "name": alias, "powered": powered})
        return jsonify({"adapters": adapters})
    except Exception as e:
        return jsonify({"adapters": [], "error": str(e)})


@api_bp.route("/api/bt/paired")
def api_bt_paired():
    """Return already-paired Bluetooth devices."""
    named_only = request.args.get("filter", "1") != "0"
    try:
        result = subprocess.run(
            ["bluetoothctl"],
            input="devices\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        devices = []
        seen = set()
        for line in result.stdout.splitlines():
            clean = _ANSI_RE.sub("", line)
            m = _DEV_PAT.search(clean)
            if m:
                mac = m.group(1).upper()
                name = m.group(2).strip()
                if mac not in seen:
                    seen.add(mac)
                    if re.match(r"^[0-9A-Fa-f]{2}[-:]", name):
                        name = ""
                    if named_only and not name:
                        continue
                    devices.append({"mac": mac, "name": name or mac})
        # Bridge devices first, then others; alphabetically within each group
        cfg = load_config()
        bridge_macs = {d.get("mac", "").upper() for d in cfg.get("BLUETOOTH_DEVICES", []) if d.get("mac")}
        devices.sort(key=lambda d: (0 if d["mac"] in bridge_macs else 1, d["name"].lower()))
        return jsonify({"devices": devices})
    except Exception as e:
        return jsonify({"devices": [], "error": str(e)})


def _run_bt_scan(job_id: str) -> None:
    """Perform BT scan in a background thread and store result in state."""
    try:
        list_result = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
        adapter_macs = re.findall(r"Controller\s+([0-9A-Fa-f:]{17})", list_result.stdout)

        post_scan_cmds = []
        for m in adapter_macs:
            post_scan_cmds.extend([f"select {m}", "show", "devices"])
        bt_timeout = 12 + len(adapter_macs) * 2

        proc = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            if adapter_macs:
                init_cmds: list[str] = []
                for m in adapter_macs:
                    init_cmds.extend([f"select {m}", "power on", "scan on"])
            else:
                init_cmds = ["power on", "agent on", "scan on"]
            if proc.stdin is None:
                raise RuntimeError("bluetoothctl subprocess stdin unavailable")
            proc.stdin.write("\n".join(init_cmds) + "\n")
            proc.stdin.flush()
            time.sleep(10)
            proc.stdin.write("scan off\n" + "\n".join(post_scan_cmds) + "\n")
            proc.stdin.flush()
            time.sleep(1)
            result_stdout, _ = proc.communicate(timeout=bt_timeout + 4)
        except Exception:
            proc.kill()
            proc.wait()
            raise

        seen: set = set()
        names: dict = {}
        device_adapter: dict = {}
        active_macs: set = set()
        current_show_adapter: str = ""
        for line in result_stdout.splitlines():
            clean = _ANSI_RE.sub("", line).strip()
            if not clean.startswith("["):
                ctrl_m = _SHOW_CTRL_PAT.match(clean)
                if ctrl_m:
                    current_show_adapter = ctrl_m.group(1).upper()
                    continue
                if current_show_adapter:
                    dev_m = _SHOW_DEV_PAT.match(clean)
                    if dev_m:
                        dmac = dev_m.group(1).upper()
                        if dmac not in device_adapter:
                            device_adapter[dmac] = current_show_adapter
                        continue
            scan_m = _NEW_DEV_PAT.search(clean)
            if scan_m:
                mac = scan_m.group(1).upper()
                name = scan_m.group(2).strip()
                seen.add(mac)
                if name and not re.match(r"^[0-9A-Fa-f]{2}[-:]", name):
                    names[mac] = name
                continue
            chg_n = _CHG_NAME_PAT.search(clean)
            if chg_n:
                mac = chg_n.group(1).upper()
                names[mac] = chg_n.group(2).strip()
                continue
            chg_r = _CHG_RSSI_PAT.search(clean)
            if chg_r:
                active_macs.add(chg_r.group(1).upper())
        all_macs = seen | active_macs

        # Cap results to avoid DoS in dense BT environments
        _MAX_SCAN_RESULTS = 50
        if len(all_macs) > _MAX_SCAN_RESULTS:
            logger.warning("BT scan found %d devices, capping to %d", len(all_macs), _MAX_SCAN_RESULTS)
            all_macs = set(list(all_macs)[:_MAX_SCAN_RESULTS])

        # Look up names for devices seen only by RSSI (already-paired/cached)
        unnamed = {mac for mac in all_macs if mac not in names}
        if unnamed:
            db_result = subprocess.run(
                ["bluetoothctl"],
                input="devices\n",
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in db_result.stdout.splitlines():
                clean = _ANSI_RE.sub("", line)
                db_m = _DEV_PAT.search(clean)
                if db_m:
                    mac = db_m.group(1).upper()
                    name = db_m.group(2).strip()
                    if mac in unnamed and name and not re.match(r"^[0-9A-Fa-f]{2}[-:]", name):
                        names[mac] = name

        # Filter to audio-capable devices and enrich with bluetoothctl info (parallel)
        def _enrich_device(mac: str) -> "dict | None":
            try:
                r = subprocess.run(
                    ["bluetoothctl", "info", mac],
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                out = r.stdout
                out_lower = out.lower()
            except Exception:
                return {"mac": mac, "name": names.get(mac, mac)}
            if mac not in names:
                nm = re.search(r"\bName:\s+(.*)", out)
                if nm:
                    n = nm.group(1).strip()
                    if n and not re.match(r"^[0-9A-Fa-f]{2}[-:]", n):
                        names[mac] = n
            class_m = re.search(r"Class:\s+(0x[0-9A-Fa-f]+)", out)
            if class_m:
                cls = int(class_m.group(1), 16)
                if (cls >> 8) & 0x1F != 4:
                    return None
            elif any(u in out_lower for u in _AUDIO_UUIDS):
                pass
            elif "UUID:" in out:
                return None
            return {"mac": mac, "name": names.get(mac, mac)}

        devices = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_enrich_device, mac): mac for mac in all_macs}
            for fut in concurrent.futures.as_completed(futures):
                result = fut.result()
                if result is not None:
                    devices.append(result)

        for d in devices:
            d["adapter"] = device_adapter.get(d["mac"], "")

        devices.sort(key=lambda d: (d["name"] == d["mac"], d["name"]))
        finish_scan_job(job_id, {"devices": devices})
    except Exception as e:
        logger.error("BT scan failed: %s", e)
        finish_scan_job(job_id, {"devices": [], "error": str(e)})


@api_bp.route("/api/bt/scan", methods=["POST"])
def api_bt_scan():
    """Start an async BT device scan; returns a job_id immediately."""
    if is_scan_running():
        return jsonify({"error": "A scan is already in progress"}), 409
    job_id = str(uuid.uuid4())
    create_scan_job(job_id)
    t = threading.Thread(target=_run_bt_scan, args=(job_id,), daemon=True, name=f"bt-scan-{job_id[:8]}")
    t.start()
    return jsonify({"job_id": job_id})


@api_bp.route("/api/bt/scan/result/<job_id>", methods=["GET"])
def api_bt_scan_result(job_id: str):
    """Poll for BT scan result by job_id."""
    job = get_scan_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] == "running":
        return jsonify({"status": "running"})
    return jsonify({"status": "done", "devices": job.get("devices", []), "error": job.get("error")})


@api_bp.route("/api/diagnostics")
def api_diagnostics():
    """Return structured health diagnostics."""
    try:
        diag: dict = {}

        try:
            r = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and "Controller" in r.stdout:
                diag["bluetooth_daemon"] = "active"
            else:
                r2 = subprocess.run(
                    ["systemctl", "is-active", "bluetooth"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                diag["bluetooth_daemon"] = r2.stdout.strip() or "inactive"
        except Exception:
            diag["bluetooth_daemon"] = "unknown"

        dbus_env = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
        dbus_path = dbus_env.replace("unix:path=", "") if dbus_env else "/run/dbus/system_bus_socket"
        diag["dbus_available"] = os.path.exists(dbus_path)

        try:
            r = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
            adapters = []
            for i, line in enumerate(r.stdout.splitlines()):
                if "Controller" not in line:
                    continue
                parts = line.split()
                mac = next((p for p in parts if len(p) == 17 and p.count(":") == 5), "")
                adapters.append(
                    {
                        "id": f"hci{i}",
                        "mac": mac,
                        "default": "default" in line.lower(),
                    }
                )
            diag["adapters"] = adapters
        except Exception as e:
            diag["adapters"] = [{"error": str(e)}]

        try:
            diag["pulseaudio"] = get_server_name()
        except Exception:
            diag["pulseaudio"] = "not available"

        try:
            diag["sinks"] = [s["name"] for s in list_sinks() if "bluez" in s["name"].lower()]
        except Exception:
            diag["sinks"] = []

        device_diag = []
        for client in _clients:
            bt_mgr = getattr(client, "bt_manager", None)
            device_diag.append(
                {
                    "name": getattr(client, "player_name", "Unknown"),
                    "mac": bt_mgr.mac_address if bt_mgr else None,
                    "connected": client.status.get("bluetooth_connected", False),
                    "sink": getattr(client, "bluetooth_sink_name", None),
                    "last_error": client.status.get("last_error"),
                }
            )
        diag["devices"] = device_diag

        # MA API integration status
        ma_url, ma_token = state.get_ma_api_credentials()
        ma_groups = state.get_ma_groups()
        diag["ma_integration"] = {
            "configured": bool(ma_url and ma_token),
            "connected": state.is_ma_connected(),
            "url": ma_url or "",
            "syncgroups": [
                {"id": g["id"], "name": g.get("name", ""), "members": len(g.get("members", []))} for g in ma_groups
            ],
            "nowplaying": state.get_ma_now_playing() if state.is_ma_connected() else {},
        }

        # PA sink-inputs with properties (for routing diagnostics)
        try:
            r = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            sink_inputs = []
            current: dict = {}
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("Sink Input #"):
                    if current:
                        sink_inputs.append(current)
                    current = {"id": line.split("#")[1]}
                elif ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip().lower().replace(" ", "_")
                    if key in ("sink", "state") or "application" in key or "media" in key:
                        current[key] = val.strip()
            if current:
                sink_inputs.append(current)
            diag["sink_inputs"] = sink_inputs
        except Exception as e:
            diag["sink_inputs"] = [{"error": str(e)}]

        # PortAudio devices available inside the container
        try:
            from sendspin.audio import query_devices

            diag["portaudio_devices"] = [
                {"index": d.index, "name": d.name, "is_default": d.is_default}
                for d in query_devices()
                if d.output_channels > 0
            ]
        except Exception as e:
            diag["portaudio_devices"] = [{"error": str(e)}]

        return jsonify(diag)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/version")
def api_version():
    """Return git version information."""
    cwd = os.path.dirname(os.path.abspath(__file__))
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=cwd,
        ).stdout.strip()
        git_desc = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=cwd,
        ).stdout.strip()
        git_date = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=cwd,
        ).stdout.strip()
        return jsonify(
            {
                "version": git_desc or VERSION,
                "git_sha": git_sha or "unknown",
                "built_at": (git_date.split(" ")[0] if git_date else BUILD_DATE),
            }
        )
    except Exception:
        return jsonify({"version": VERSION, "git_sha": "unknown", "built_at": BUILD_DATE})
