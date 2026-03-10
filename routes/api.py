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
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request

import state
from config import (
    BUILD_DATE,
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    VERSION,
    config_lock,
    load_config,
    update_config,
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
from services.bluetooth import _AUDIO_UUIDS, list_bt_adapters
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
    get_ma_group_for_player,
    get_ma_now_playing_for_group,
    get_scan_job,
    is_scan_running,
    load_adapter_name_cache,
)
from state import clients as _clients
from state import (
    clients_lock as _clients_lock,
)

UTC = timezone.utc

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

# ---------------------------------------------------------------------------
# SSE connection limiting — prevent resource exhaustion
# ---------------------------------------------------------------------------

_sse_count = 0
_sse_lock = threading.Lock()
_MAX_SSE = 4
_SSE_MAX_LIFETIME = 1800  # 30 minutes

# Cached config flag — avoid reading config.json on every volume/mute request.
# Reloaded in api_config() (line ~1361) after config save; also valid on process
# restart since config.py is re-read.  Does NOT auto-reload on manual file edit.
_volume_via_ma: bool = True


def _reload_volume_via_ma() -> None:
    global _volume_via_ma
    _volume_via_ma = load_config().get("VOLUME_VIA_MA", True)


_reload_volume_via_ma()


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


def _enrich_status_with_ma(status: dict, client) -> None:
    """Add MA syncgroup name and now-playing metadata to a client status dict."""
    player_name = getattr(client, "player_name", None)
    if not player_name:
        return
    ma_group = get_ma_group_for_player(player_name)
    if ma_group and ma_group.get("name"):
        status["group_name"] = ma_group["name"]
    # Per-device MA now-playing: prefer name-matched syncgroup, then Sendspin-reported
    # group_id (which IS the MA syncgroup id), then solo player_id queue
    if ma_group:
        status["ma_now_playing"] = get_ma_now_playing_for_group(ma_group["id"])
    else:
        dev_group_id: str = status.get("group_id", "")
        pid: str = getattr(client, "player_id", "")
        status["ma_now_playing"] = (
            get_ma_now_playing_for_group(dev_group_id) or (get_ma_now_playing_for_group(pid) if pid else {}) or {}
        )


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
            uptime = datetime.now(tz=UTC) - status["uptime_start"]
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
        status["battery_level"] = getattr(bt_mgr, "battery_level", None) if bt_mgr else None

        _enrich_status_with_ma(status, client)

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
    with _clients_lock:
        snapshot = list(_clients)
    if not snapshot:
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
    return get_client_status_for(snapshot[0])


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
    except Exception:
        logger.exception("Restart failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


# ---------------------------------------------------------------------------
#  MA volume proxy helpers
# ---------------------------------------------------------------------------


def _set_volume_via_ma(targets, volume: int, *, is_group: bool = False) -> bool:
    """Proxy volume change through MA WebSocket API.

    For group requests (is_group=True), uses ``players/cmd/group_volume``
    once per unique sync group among the targets.
    For individual requests, uses ``players/cmd/volume_set`` (flat).

    Returns True if at least one target was set successfully via MA.
    """
    from services.ma_monitor import send_player_cmd

    loop = state.get_main_loop()
    if not loop:
        return False

    if is_group and targets:
        # Group volume: send one group_volume per unique sync group
        seen_groups: set[str] = set()
        for client in targets:
            gid = client.status.get("group_id")
            if not gid or gid in seen_groups:
                continue
            seen_groups.add(gid)
            pid = getattr(client, "player_id", None)
            if not pid:
                continue
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    send_player_cmd("players/cmd/group_volume", {"player_id": pid, "volume_level": volume}),
                    loop,
                )
                fut.result(timeout=5.0)
            except Exception:
                logger.debug("MA group_volume failed for group %s", gid, exc_info=True)
                return False
        return bool(seen_groups)

    # Individual / all: flat volume_set for each target
    for client in targets:
        pid = getattr(client, "player_id", None)
        if not pid:
            continue
        try:
            fut = asyncio.run_coroutine_threadsafe(
                send_player_cmd("players/cmd/volume_set", {"player_id": pid, "volume_level": volume}),
                loop,
            )
            if not fut.result(timeout=5.0):
                return False
        except Exception:
            logger.debug("MA volume_set failed for %s", pid, exc_info=True)
            return False
    return bool(targets)


def _set_mute_via_ma(targets, muted: bool) -> bool:
    """Proxy mute change through MA WebSocket API."""
    from services.ma_monitor import send_player_cmd

    loop = state.get_main_loop()
    if not loop:
        return False

    for client in targets:
        pid = getattr(client, "player_id", None)
        if not pid:
            continue
        try:
            fut = asyncio.run_coroutine_threadsafe(
                send_player_cmd("players/cmd/volume_mute", {"player_id": pid, "muted": muted}),
                loop,
            )
            if not fut.result(timeout=5.0):
                return False
        except Exception:
            logger.debug("MA volume_mute failed for %s", pid, exc_info=True)
            return False
    return bool(targets)


@api_bp.route("/api/volume", methods=["POST"])
def set_volume():
    """Set player volume. Accepts player_name (single), player_names (list), or neither (all).

    When MA is connected (and ``force_local`` is not set), routes volume changes
    through the MA WebSocket API so that MA's own UI stays in sync.  Group
    requests (``group: true``) use the delta-approach ``players/cmd/group_volume``.
    Falls back to direct pactl on MA failure.
    """
    try:
        data = request.get_json() or {}
        try:
            volume = max(0, min(100, int(data.get("volume", 100))))
        except (ValueError, TypeError):
            return jsonify({"success": False, "error": "Invalid volume value"}), 400
        player_names = data.get("player_names")
        if player_names is not None and not isinstance(player_names, list):
            return jsonify({"error": "player_names must be a list"}), 400
        player_name = data.get("player_name")
        group_id = data.get("group_id")
        is_group = data.get("group", False)
        force_local = data.get("force_local", False)

        with _clients_lock:
            snapshot = list(_clients)
        if group_id is not None:
            targets = [c for c in snapshot if c.status.get("group_id") == group_id]
        elif player_names is not None:
            targets = [c for c in snapshot if getattr(c, "player_name", None) in player_names]
        elif player_name:
            targets = [c for c in snapshot if getattr(c, "player_name", None) == player_name]
        else:
            targets = snapshot

        # --- MA path: proxy through MA API when connected ---
        if not force_local and _volume_via_ma and state.is_ma_connected() and targets:
            ma_ok = _set_volume_via_ma(targets, volume, is_group=is_group)
            if ma_ok:
                # Do NOT update local status — bridge_daemon will receive the
                # VolumeChanged echo from MA via sendspin protocol, apply pactl,
                # and report the actual volume through subprocess stdout.
                #
                # However, devices NOT in a MA sync group won't receive the
                # echo.  Apply volume locally for those orphan devices.
                if is_group:
                    orphans = [c for c in targets if not c.status.get("group_id")]
                    for client in orphans:
                        if client.bluetooth_sink_name:
                            ok = set_sink_volume(client.bluetooth_sink_name, volume)
                            if ok:
                                client._update_status({"volume": volume})
                                _loop = state.get_main_loop()
                                if _loop:
                                    asyncio.run_coroutine_threadsafe(
                                        client._send_subprocess_command({"cmd": "set_volume", "value": volume}), _loop
                                    )
                                mac = getattr(getattr(client, "bt_manager", None), "mac_address", None)
                                if mac:
                                    _schedule_volume_persist(mac, volume)
                return jsonify({"success": True, "volume": volume, "via": "ma"})
            logger.debug("MA volume proxy failed, falling back to local pactl")

        # --- Local fallback: direct pactl ---
        def _set_one(client):
            if not client.bluetooth_sink_name:
                return None
            ok = set_sink_volume(client.bluetooth_sink_name, volume)
            if ok:
                client._update_status({"volume": volume})
                loop = state.get_main_loop()
                if loop:
                    asyncio.run_coroutine_threadsafe(
                        client._send_subprocess_command({"cmd": "set_volume", "value": volume}), loop
                    )
                mac = getattr(getattr(client, "bt_manager", None), "mac_address", None)
                if mac:
                    _schedule_volume_persist(mac, volume)
            return {"player": getattr(client, "player_name", "?"), "ok": ok}

        if len(targets) <= 3:
            results = [r for r in (_set_one(c) for c in targets) if r is not None]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(targets), 20)) as pool:
                results = [r for r in pool.map(_set_one, targets) if r is not None]
        if not results:
            return jsonify({"success": False, "error": "No clients available"}), 503
        return jsonify({"success": True, "volume": volume, "results": results})
    except Exception:
        logger.exception("Volume update failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@api_bp.route("/api/mute", methods=["POST"])
def set_mute():
    """Toggle or set mute.

    When MA is connected, proxies through ``players/cmd/volume_mute`` so that
    MA's UI stays in sync.  Falls back to direct pactl on failure.
    """
    try:
        data = request.get_json() or {}
        player_names = data.get("player_names")
        if player_names is not None and not isinstance(player_names, list):
            return jsonify({"error": "player_names must be a list"}), 400
        player_name = data.get("player_name")
        mute_value = data.get("mute")
        force_local = data.get("force_local", False)

        with _clients_lock:
            snapshot = list(_clients)
        if player_names is not None:
            targets = [c for c in snapshot if getattr(c, "player_name", None) in player_names]
        elif player_name:
            targets = [c for c in snapshot if getattr(c, "player_name", None) == player_name]
        else:
            targets = snapshot[:1]

        # --- MA path ---
        if not force_local and _volume_via_ma and state.is_ma_connected() and targets:
            # Resolve desired mute state
            desired = bool(mute_value) if mute_value is not None else not targets[0].status.get("muted", False)
            if _set_mute_via_ma(targets, desired):
                # Do NOT update local status — bridge_daemon receives the
                # mute echo from MA via sendspin protocol and updates status.
                return jsonify({"success": True, "muted": desired, "via": "ma"})
            logger.debug("MA mute proxy failed, falling back to local pactl")

        # --- Local fallback ---
        results = []
        loop = state.get_main_loop()
        for client in targets:
            if client.bluetooth_sink_name:
                ok = set_sink_mute(client.bluetooth_sink_name, mute_value)
                if ok:
                    muted = get_sink_mute(client.bluetooth_sink_name)
                    if muted is None:
                        muted = bool(mute_value) if mute_value is not None else not client.status.get("muted", False)
                    client._update_status({"muted": muted})
                    if loop:
                        asyncio.run_coroutine_threadsafe(
                            client._send_subprocess_command({"cmd": "set_mute", "muted": muted}), loop
                        )
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
    except Exception:
        logger.exception("Mute update failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@api_bp.route("/api/pause_all", methods=["POST"])
def pause_all():
    """Pause or play all running daemon subprocesses.

    Pause:  sends Sendspin session-group command once per unique group_id, or
            directly to solo (ungrouped) players. MA propagates pause to all
            group members via the existing WS connection.

    Play:   for players mapped to an MA persistent syncgroup, calls ma_group_play()
            (one call per unique MA syncgroup) so MA resumes all members in sync.
            Falls back to Sendspin session-group command when MA is not configured
            or the player has no mapped syncgroup. Solo players always use the
            direct subprocess command.
    """
    data = request.get_json() or {}
    action = data.get("action", "pause")
    loop = state.get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    count = 0

    with _clients_lock:
        snapshot = list(_clients)

    if action == "pause":
        # One pause command per unique Sendspin session group (MA propagates to all members)
        seen_groups: set = set()
        for client in snapshot:
            if not client.is_running():
                continue
            gid = client.status.get("group_id")
            if gid:
                if gid in seen_groups:
                    continue
                seen_groups.add(gid)
            try:
                fut = asyncio.run_coroutine_threadsafe(client._send_subprocess_command({"cmd": "pause"}), loop)
                fut.result(timeout=2.0)
                count += 1
            except Exception as exc:
                logger.debug("Could not send pause to %s: %s", client.player_name, exc)

    else:  # play / unpause
        ma_url, ma_token = state.get_ma_api_credentials()
        seen_ma_syncgroups: set = set()
        seen_session_groups: set = set()

        for client in snapshot:
            if not client.is_running():
                continue

            # Try MA syncgroup play first (preserves group sync)
            if ma_url and ma_token:
                ma_group = state.get_ma_group_for_player(client.player_name)
                if ma_group:
                    sid = ma_group["id"]
                    if sid not in seen_ma_syncgroups:
                        seen_ma_syncgroups.add(sid)
                        try:
                            from services.ma_client import ma_group_play

                            fut = asyncio.run_coroutine_threadsafe(ma_group_play(ma_url, ma_token, sid), loop)
                            if fut.result(timeout=10.0):
                                logger.info("pause_all play → MA syncgroup %s", sid)
                                count += 1
                                continue
                        except Exception as exc:
                            logger.warning("MA group play failed for %s, falling back: %s", sid, exc)
                    else:
                        continue  # already sent for this MA syncgroup

            # Fallback: Sendspin session-group command (one per session group or solo)
            gid = client.status.get("group_id")
            if gid:
                if gid in seen_session_groups:
                    continue
                seen_session_groups.add(gid)
            try:
                fut = asyncio.run_coroutine_threadsafe(client._send_subprocess_command({"cmd": "play"}), loop)
                fut.result(timeout=2.0)
                count += 1
            except Exception as exc:
                logger.debug("Could not send play to %s: %s", client.player_name, exc)

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
    with _clients_lock:
        snapshot = list(_clients)
    target = next(
        (c for c in snapshot if c.is_running() and c.status.get("group_id") == group_id),
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
    except Exception:
        logger.exception("Group pause/play failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


def _ma_host_from_sendspin_clients():
    """Extract MA server host from connected sendspin clients.

    Checks server_host first (explicit config), then falls back to the
    resolved address from the live sendspin WebSocket connection.
    Returns host string or None.
    """
    with _clients_lock:
        snapshot = list(_clients)
    for client in snapshot:
        host = getattr(client, "server_host", None)
        if host and host.lower() not in ("auto", "discover", ""):
            return host
    # Fallback: resolved address from active sendspin connection
    for client in snapshot:
        resolved = getattr(client, "connected_server_url", "") or ""
        # Format: "host:port" (e.g. "192.168.10.10:9000")
        if resolved and ":" in resolved:
            return resolved.rsplit(":", 1)[0]
    return None


@api_bp.route("/api/ma/discover", methods=["GET"])
def api_ma_discover():
    """Discover Music Assistant servers.

    HA addon mode: 1) homeassistant.local:8095 (Supervisor DNS), 2) saved MA_API_URL.
    Other modes: 1) saved MA_API_URL, 2) SENDSPIN_SERVER host, 3) sendspin
    client connection host, 4) mDNS scan.

    Always returns ``is_addon`` flag so frontend can adjust UI.
    """
    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    from services.ma_discovery import validate_ma_url

    is_addon = _detect_runtime() == "ha_addon"

    def _ok(servers):
        return jsonify({"success": True, "is_addon": is_addon, "servers": servers})

    # --- HA addon shortcut: MA is on the same HA host ---
    if is_addon:
        for candidate in ("http://localhost:8095", "http://homeassistant.local:8095"):
            try:
                fut = asyncio.run_coroutine_threadsafe(validate_ma_url(candidate), loop)
                info = fut.result(timeout=5.0)
                if info:
                    return _ok([info])
            except Exception:
                pass

    # 1) Try already-known MA_API_URL
    ma_url, _ = state.get_ma_api_credentials()
    if ma_url:
        try:
            fut = asyncio.run_coroutine_threadsafe(validate_ma_url(ma_url), loop)
            info = fut.result(timeout=5.0)
            if info:
                return _ok([info])
        except Exception:
            pass

    # In addon mode, no need for further heuristics — fall through to error
    if is_addon:
        return _ok([])

    # 2) Derive from SENDSPIN_SERVER (same host, default MA port 8095)
    cfg = load_config()
    sendspin_host = (cfg.get("SENDSPIN_SERVER") or "").strip()
    if sendspin_host and sendspin_host.lower() not in ("auto", "discover", ""):
        candidate = f"http://{sendspin_host}:8095"
        try:
            fut = asyncio.run_coroutine_threadsafe(validate_ma_url(candidate), loop)
            info = fut.result(timeout=5.0)
            if info:
                return _ok([info])
        except Exception:
            pass

    # 3) Check connected sendspin clients — MA lives on the same host
    sendspin_ma_host = _ma_host_from_sendspin_clients()
    if sendspin_ma_host:
        candidate = f"http://{sendspin_ma_host}:8095"
        try:
            fut = asyncio.run_coroutine_threadsafe(validate_ma_url(candidate), loop)
            info = fut.result(timeout=5.0)
            if info:
                return _ok([info])
        except Exception:
            pass

    # 4) Fallback: mDNS scan
    try:
        from services.ma_discovery import discover_ma_servers

        fut = asyncio.run_coroutine_threadsafe(discover_ma_servers(timeout=5.0), loop)
        servers = fut.result(timeout=10.0)
        return _ok(servers)
    except Exception:
        logger.exception("MA mDNS discovery failed")
        return jsonify({"success": False, "is_addon": is_addon, "error": "Discovery failed"}), 500


@api_bp.route("/api/ma/login", methods=["POST"])
def api_ma_login():
    """Login to Music Assistant with username/password and auto-create token.

    Request JSON: {"url": "http://...:8095", "username": "...", "password": "..."}
    If url is empty, tries mDNS discovery first.

    On success, saves MA_API_URL, MA_API_TOKEN, MA_USERNAME to config.json
    and returns server info.  Password is NOT stored.
    """
    data = request.get_json(silent=True) or {}
    ma_url = (data.get("url") or "").strip()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password are required"}), 400

    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    # Auto-discover MA URL if not provided
    if not ma_url:
        # Try deriving from already-known sources before mDNS
        from services.ma_discovery import validate_ma_url

        ma_url_candidate = ""

        # From existing MA config
        known_url, _ = state.get_ma_api_credentials()
        if known_url:
            ma_url_candidate = known_url

        # From SENDSPIN_SERVER config
        if not ma_url_candidate:
            cfg = load_config()
            sh = (cfg.get("SENDSPIN_SERVER") or "").strip()
            if sh and sh.lower() not in ("auto", "discover", ""):
                ma_url_candidate = f"http://{sh}:8095"

        # From connected sendspin clients (explicit or resolved address)
        if not ma_url_candidate:
            sendspin_ma_host = _ma_host_from_sendspin_clients()
            if sendspin_ma_host:
                ma_url_candidate = f"http://{sendspin_ma_host}:8095"

        if ma_url_candidate:
            fut = asyncio.run_coroutine_threadsafe(validate_ma_url(ma_url_candidate), loop)
            info = fut.result(timeout=5.0)
            if info:
                ma_url = info["url"]
                logger.info("Using known MA server: %s", ma_url)

        # Last resort: mDNS scan
        if not ma_url:
            try:
                from services.ma_discovery import discover_ma_servers

                fut = asyncio.run_coroutine_threadsafe(discover_ma_servers(timeout=5.0), loop)
                servers = fut.result(timeout=10.0)
                if servers:
                    ma_url = servers[0]["url"]
                    logger.info("Auto-discovered MA server: %s", ma_url)
                else:
                    return jsonify(
                        {"success": False, "error": "No MA server found on network. Enter URL manually."}
                    ), 404
            except Exception:
                logger.exception("MA discovery failed during login")
                return jsonify({"success": False, "error": "Discovery failed. Enter URL manually."}), 500

    # Normalize URL
    if "://" not in ma_url:
        ma_url = f"http://{ma_url}"

    # Login and create long-lived token
    try:
        from music_assistant_client import login_with_token

        fut = asyncio.run_coroutine_threadsafe(
            login_with_token(ma_url, username, password, token_name="Sendspin BT Bridge"),
            loop,
        )
        _user, token = fut.result(timeout=30.0)
    except Exception as exc:
        err_msg = str(exc)
        if "auth" in err_msg.lower() or "401" in err_msg or "credentials" in err_msg.lower():
            return jsonify({"success": False, "error": "Invalid username or password"}), 401
        logger.exception("MA login failed")
        return jsonify({"success": False, "error": f"Login failed: {err_msg}"}), 500

    # Save to config.json
    def _save_ma_creds(cfg: dict) -> None:
        cfg["MA_API_URL"] = ma_url
        cfg["MA_API_TOKEN"] = token
        cfg["MA_USERNAME"] = username

    update_config(_save_ma_creds)
    state.set_ma_api_credentials(ma_url, token)

    # Trigger MA group rediscovery in background
    try:
        with _clients_lock:
            snapshot = list(_clients)
        player_names = [c.player_name for c in snapshot]
        asyncio.run_coroutine_threadsafe(
            _rediscover_after_login(ma_url, token, player_names),
            loop,
        )
    except Exception:
        pass  # Non-critical — groups will be discovered on next poll

    return jsonify(
        {
            "success": True,
            "url": ma_url,
            "username": username,
            "message": "Connected to Music Assistant. Token saved.",
        }
    )


async def _rediscover_after_login(
    ma_url: str,
    ma_token: str,
    player_names: list[str],
) -> None:
    """Background task: rediscover MA groups after successful login."""
    try:
        from services.ma_client import discover_ma_groups

        name_map, all_groups = await discover_ma_groups(ma_url, ma_token, player_names)
        state.set_ma_groups(name_map, all_groups)
        logger.info("MA groups rediscovered after login: %d groups", len(all_groups))
    except Exception:
        logger.debug("MA group rediscovery after login failed", exc_info=True)


# ---------------------------------------------------------------------------
#  HA OAuth → MA token flow  (for MA running as HA addon)
# ---------------------------------------------------------------------------


@api_bp.route("/api/ma/ha-auth-page")
def api_ma_ha_auth_page():
    """Self-contained popup page for HA → MA OAuth login.

    Opens in a popup window from the main UI.  Handles credentials + MFA,
    then posts the result back to ``window.opener`` via ``postMessage``.
    """
    ma_url = request.args.get("ma_url", "")
    return Response(
        _HA_AUTH_PAGE_HTML.replace("__MA_URL__", ma_url),
        content_type="text/html; charset=utf-8",
    )


_HA_AUTH_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in with Home Assistant</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:#1c1c1e;color:#e5e5e7;display:flex;align-items:center;
     justify-content:center;min-height:100vh;padding:20px}
.card{background:#2c2c2e;border-radius:16px;padding:36px 32px;width:100%;
      max-width:360px;box-shadow:0 8px 32px rgba(0,0,0,.4)}
.logo{text-align:center;font-size:48px;margin-bottom:8px}
h2{text-align:center;font-size:18px;font-weight:600;margin-bottom:4px}
.sub{text-align:center;font-size:13px;color:#98989d;margin-bottom:24px}
label{display:block;font-size:13px;color:#98989d;margin-bottom:4px}
input{width:100%;padding:10px 12px;border:1px solid #48484a;border-radius:8px;
      background:#1c1c1e;color:#e5e5e7;font-size:15px;outline:none;
      margin-bottom:14px;transition:border-color .2s}
input:focus{border-color:#0a84ff}
input.mfa-code{text-align:center;font-size:24px;letter-spacing:8px;
               font-variant-numeric:tabular-nums}
.btn{width:100%;padding:12px;border:none;border-radius:10px;font-size:15px;
     font-weight:600;cursor:pointer;transition:opacity .2s}
.btn-primary{background:#0a84ff;color:#fff}
.btn-primary:hover{opacity:.85}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.msg{text-align:center;font-size:13px;margin-top:12px;min-height:18px}
.msg.error{color:#ff453a}.msg.ok{color:#30d158}
.step{display:none}.step.active{display:block}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #48484a;
         border-top-color:#0a84ff;border-radius:50%;animation:spin .6s linear infinite;
         vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}
.success-icon{text-align:center;font-size:56px;margin:16px 0}
</style>
</head>
<body>
<div class="card">
  <!-- Step 1: Credentials -->
  <div id="step-creds" class="step active">
    <div class="logo">🏠</div>
    <h2>Home Assistant</h2>
    <div class="sub">Sign in to connect Music Assistant</div>
    <form id="creds-form" onsubmit="submitCreds(event)">
      <label for="username">Username</label>
      <input type="text" id="username" autocomplete="username" autofocus required>
      <label for="password">Password</label>
      <input type="password" id="password" autocomplete="current-password" required>
      <button type="submit" class="btn btn-primary" id="creds-btn">Sign in</button>
    </form>
    <div id="creds-msg" class="msg"></div>
  </div>

  <!-- Step 2: MFA -->
  <div id="step-mfa" class="step">
    <div class="logo">🔐</div>
    <h2>Two-factor authentication</h2>
    <div class="sub" id="mfa-label">Enter code from your authenticator app</div>
    <form id="mfa-form" onsubmit="submitMfa(event)">
      <input type="text" id="mfa-code" class="mfa-code" inputmode="numeric"
             maxlength="6" autocomplete="one-time-code" autofocus required
             placeholder="------">
      <button type="submit" class="btn btn-primary" id="mfa-btn">Verify</button>
    </form>
    <div id="mfa-msg" class="msg"></div>
  </div>

  <!-- Step 3: Success -->
  <div id="step-done" class="step">
    <div class="success-icon">✅</div>
    <h2>Connected!</h2>
    <div class="sub">Music Assistant token saved.<br>This window will close automatically.</div>
  </div>
</div>

<script>
var API_BASE = window.location.origin;
var MA_URL = '__MA_URL__';
var haState = {};

function showStep(id) {
  document.querySelectorAll('.step').forEach(function(el) { el.classList.remove('active'); });
  document.getElementById('step-' + id).classList.add('active');
}

function setMsg(id, text, isError) {
  var el = document.getElementById(id);
  el.textContent = text;
  el.className = 'msg ' + (isError ? 'error' : '');
}

function setLoading(btnId, loading) {
  var btn = document.getElementById(btnId);
  if (loading) {
    btn.disabled = true;
    btn._origText = btn.textContent;
    btn.innerHTML = '<span class="spinner"></span>Signing in\u2026';
  } else {
    btn.disabled = false;
    btn.textContent = btn._origText || 'Sign in';
  }
}

async function submitCreds(e) {
  e.preventDefault();
  var user = document.getElementById('username').value.trim();
  var pass = document.getElementById('password').value;
  if (!user || !pass) return;
  setLoading('creds-btn', true);
  setMsg('creds-msg', '', false);
  try {
    var resp = await fetch(API_BASE + '/api/ma/ha-login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({step: 'init', username: user, password: pass, ma_url: MA_URL}),
    });
    var data = await resp.json();
    if (data.success && data.step === 'mfa') {
      haState = data;
      haState.username = user;
      var label = document.getElementById('mfa-label');
      label.textContent = 'Enter code from ' + (data.mfa_module_name || 'authenticator app');
      showStep('mfa');
      document.getElementById('mfa-code').focus();
    } else if (data.success && data.step === 'done') {
      onSuccess(data);
    } else {
      setMsg('creds-msg', data.error || 'Authentication failed', true);
    }
  } catch (err) {
    setMsg('creds-msg', 'Network error: ' + err.message, true);
  } finally {
    setLoading('creds-btn', false);
  }
}

async function submitMfa(e) {
  e.preventDefault();
  var code = document.getElementById('mfa-code').value.trim();
  if (!code) return;
  setLoading('mfa-btn', true);
  setMsg('mfa-msg', '', false);
  try {
    var resp = await fetch(API_BASE + '/api/ma/ha-login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        step: 'mfa', flow_id: haState.flow_id, ha_url: haState.ha_url,
        client_id: haState.client_id, state: haState.state,
        code: code, username: haState.username, ma_url: MA_URL,
      }),
    });
    var data = await resp.json();
    if (data.success && data.step === 'done') {
      onSuccess(data);
    } else {
      setMsg('mfa-msg', data.error || 'Invalid code', true);
      document.getElementById('mfa-code').value = '';
      document.getElementById('mfa-code').focus();
    }
  } catch (err) {
    setMsg('mfa-msg', 'Network error: ' + err.message, true);
  } finally {
    setLoading('mfa-btn', false);
  }
}

function onSuccess(data) {
  showStep('done');
  if (window.opener) {
    window.opener.postMessage({type: 'ma-ha-auth-done', success: true,
      url: data.url, username: data.username, message: data.message}, '*');
    setTimeout(function() { window.close(); }, 1500);
  }
}
</script>
</body>
</html>
"""


# ── MA ↔ HA OAuth helpers (shared by ha-login and ha-silent-auth) ─────────


def _get_ma_oauth_params(ma_url: str):
    """Call MA /auth/authorize → parse HA URL, client_id, redirect_uri, state."""
    import urllib.parse as _up
    import urllib.request as _ur

    try:
        resp = _ur.urlopen(f"{ma_url}/auth/authorize?provider_id=homeassistant", timeout=10)
        info = json.loads(resp.read())
        auth_url = info.get("authorization_url", "")
        if not auth_url:
            return None
        parsed = _up.urlparse(auth_url)
        params = _up.parse_qs(parsed.query)
        ha_base = f"{parsed.scheme}://{parsed.netloc}"
        client_id = params.get("client_id", [""])[0]
        redirect_uri = params.get("redirect_uri", [""])[0]
        oauth_state = params.get("state", [""])[0]
        return ha_base, client_id, redirect_uri, oauth_state
    except Exception as exc:
        logger.warning("MA /auth/authorize failed: %s", exc)
        return None


def _ha_login_flow_start(ha_url: str, client_id: str, redirect_uri: str):
    """Start HA login_flow for the MA OAuth flow."""
    import urllib.request as _ur

    try:
        body = json.dumps(
            {
                "client_id": client_id,
                "handler": ["homeassistant", None],
                "redirect_uri": redirect_uri,
            }
        ).encode()
        req = _ur.Request(
            f"{ha_url}/auth/login_flow",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.warning("HA login_flow start failed: %s", exc)
        return None


def _ha_login_flow_step(ha_url: str, flow_id: str, payload: dict, client_id: str):
    """Submit a step to HA login_flow."""
    import urllib.request as _ur
    from urllib.error import HTTPError

    try:
        body = json.dumps({"client_id": client_id, **payload}).encode()
        req = _ur.Request(
            f"{ha_url}/auth/login_flow/{flow_id}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        try:
            return json.loads(exc.read())
        except Exception:
            pass
        logger.warning("HA flow step HTTP %s", exc.code)
        return None
    except Exception as exc:
        logger.warning("HA flow step error: %s", exc)
        return None


def _ma_callback_exchange(ma_url: str, code: str, oauth_state: str):
    """Call MA /auth/callback to exchange HA code for MA token."""
    import http.client
    import urllib.parse as _up

    try:
        cb_url = (
            f"{ma_url}/auth/callback?code={_up.quote(code)}&state={_up.quote(oauth_state)}&provider_id=homeassistant"
        )
        parsed_cb = _up.urlparse(cb_url)
        hostname = parsed_cb.hostname or "localhost"
        conn = http.client.HTTPConnection(hostname, parsed_cb.port or 80, timeout=15)
        path = f"{parsed_cb.path}?{parsed_cb.query}"
        conn.request("GET", path)
        resp = conn.getresponse()

        if resp.status in (301, 302, 303, 307, 308):
            location = resp.getheader("Location", "")
            loc_parsed = _up.urlparse(location)
            loc_params = _up.parse_qs(loc_parsed.query)
            ma_token = loc_params.get("code", [""])[0]
            conn.close()
            if ma_token:
                return ma_token
            logger.warning("MA callback redirect missing code: %s", location)
            return None

        body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status == 200:
            m = re.search(r'[?&]code=([^&#"\'<>\s]+)', body)
            if m:
                return m.group(1)
        logger.warning("MA callback returned %s: %s", resp.status, body[:200])
        return None
    except Exception as exc:
        logger.exception("MA callback exchange failed: %s", exc)
        return None


_MA_TOKEN_NAME = "Sendspin BT Bridge"


def _validate_ma_token(ma_url: str, token: str) -> bool:
    """Quick WS auth check — returns True if the token authenticates with MA."""
    try:
        from websockets.sync.client import connect as ws_connect
    except ImportError:
        return False
    ws_url = ma_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    try:
        with ws_connect(ws_url, proxy=None, close_timeout=5) as ws:
            ws.recv(timeout=5)  # server_info
            ws.send(json.dumps({"command": "auth", "args": {"token": token}, "message_id": 1}))
            resp = json.loads(ws.recv(timeout=5))
            return bool(resp.get("result", {}).get("authenticated"))
    except Exception as exc:
        logger.debug("MA token validation failed: %s", exc)
        return False


def _exchange_for_long_lived_token(ma_url: str, session_token: str) -> str:
    """Exchange a short-lived MA session token for a long-lived API token.

    Connects to MA WS, authenticates, calls auth/token/create.
    Returns the long-lived token on success, or the original session_token as fallback.
    """
    try:
        from websockets.sync.client import connect as ws_connect
    except ImportError:
        logger.warning("websockets.sync.client not available — using session token as-is")
        return session_token
    ws_url = ma_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    try:
        with ws_connect(ws_url, proxy=None, close_timeout=5) as ws:
            ws.recv(timeout=5)  # server_info
            # Auth with session token
            ws.send(json.dumps({"command": "auth", "args": {"token": session_token}, "message_id": 1}))
            auth_resp = json.loads(ws.recv(timeout=5))
            if not auth_resp.get("result", {}).get("authenticated"):
                logger.warning("MA WS auth failed with session token — using it as-is")
                return session_token
            # Create long-lived token
            ws.send(
                json.dumps(
                    {
                        "command": "auth/token/create",
                        "args": {"name": _MA_TOKEN_NAME},
                        "message_id": 2,
                    }
                )
            )
            create_resp = json.loads(ws.recv(timeout=10))
            long_lived = create_resp.get("result")
            if long_lived and isinstance(long_lived, str):
                logger.info("Created long-lived MA API token '%s'", _MA_TOKEN_NAME)
                return long_lived
            logger.warning("auth/token/create returned unexpected result: %s", create_resp)
            return session_token
    except Exception as exc:
        logger.warning("Long-lived token exchange failed (%s) — using session token", exc)
        return session_token


def _save_ma_token_and_rediscover(ma_url: str, ma_token: str, username: str = "") -> None:
    """Save MA token to config and trigger group rediscovery."""

    def _save(cfg: dict) -> None:
        cfg["MA_API_URL"] = ma_url
        cfg["MA_API_TOKEN"] = ma_token
        if username:
            cfg["MA_USERNAME"] = username

    update_config(_save)
    state.set_ma_api_credentials(ma_url, ma_token)

    loop = state.get_main_loop()
    if loop:
        try:
            with _clients_lock:
                names = [c.player_name for c in _clients]
            asyncio.run_coroutine_threadsafe(_rediscover_after_login(ma_url, ma_token, names), loop)
        except Exception:
            pass


def _ha_authorize_with_token(ha_url: str, ha_token: str, client_id: str, redirect_uri: str, oauth_state: str):
    """POST to HA /auth/authorize with Bearer token to obtain an auth code.

    HA returns 302 to redirect_uri?code=AUTH_CODE — we extract the code
    from the Location header without following the redirect.
    """
    import http.client
    import urllib.parse as _up

    parsed = _up.urlparse(ha_url)
    hostname = parsed.hostname or "localhost"
    try:
        if parsed.scheme == "https":
            import ssl

            ctx = ssl.create_default_context()
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                hostname, parsed.port or 443, timeout=10, context=ctx
            )
        else:
            conn = http.client.HTTPConnection(hostname, parsed.port or 8123, timeout=10)

        body = _up.urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "state": oauth_state})
        conn.request(
            "POST",
            "/auth/authorize",
            body=body,
            headers={
                "Authorization": f"Bearer {ha_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        resp = conn.getresponse()
        resp.read()
        conn.close()

        if resp.status in (301, 302, 303, 307, 308):
            location = resp.getheader("Location", "")
            if location:
                params = _up.parse_qs(_up.urlparse(location).query)
                return params.get("code", [""])[0] or None
        logger.warning("HA authorize returned %s (expected 302)", resp.status)
        return None
    except Exception as exc:
        logger.warning("HA authorize request failed: %s", exc)
        return None


@api_bp.route("/api/ma/ha-silent-auth", methods=["POST"])
def api_ma_ha_silent_auth():
    """Silent auth: exchange HA access token for MA token (Ingress mode).

    When the bridge is accessed via HA Ingress, the browser shares HA's origin
    and has hassTokens in localStorage.  The frontend sends the HA access_token
    here; we use it to create an HA auth code and exchange it for an MA token.

    Idempotent: if an existing long-lived token is valid for the same MA URL,
    returns success without creating a new token.

    Request JSON: {"ha_token": "eyJ...", "ma_url": "http://...:8095"}
    """
    data = request.get_json(silent=True) or {}
    ha_token = (data.get("ha_token") or "").strip()
    ma_url = (data.get("ma_url") or "").strip().rstrip("/")

    if not ha_token or not ma_url:
        return jsonify({"success": False, "error": "Missing ha_token or ma_url"}), 400

    # Idempotency: reuse existing token if it still works for this MA instance
    existing_url, existing_token = state.get_ma_api_credentials()
    if existing_token and existing_url and existing_url.rstrip("/") == ma_url:
        if _validate_ma_token(ma_url, existing_token):
            logger.debug("Silent auth: existing MA token still valid — reusing")
            return jsonify(
                {
                    "success": True,
                    "url": ma_url,
                    "username": "",
                    "message": "Already connected to Music Assistant.",
                }
            )

    # 1. Get OAuth state from MA
    oauth = _get_ma_oauth_params(ma_url)
    if not oauth:
        return jsonify({"success": False, "error": "MA does not support HA authentication"}), 400
    ha_base, client_id, redirect_uri, oauth_state = oauth

    # In addon mode, prefer internal HA URL for reliability
    if _detect_runtime() == "ha_addon":
        ha_base = "http://homeassistant:8123"

    # 2. Create HA auth code using the user's access token
    ha_code = _ha_authorize_with_token(ha_base, ha_token, client_id, redirect_uri, oauth_state)
    if not ha_code:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "HA authorization failed — token may be expired",
                }
            ),
            401,
        )

    # 3. Exchange HA auth code for MA session token
    ma_session_token = _ma_callback_exchange(ma_url, ha_code, oauth_state)
    if not ma_session_token:
        return jsonify({"success": False, "error": "Failed to exchange HA code for MA token"}), 500

    # 4. Exchange session token for a long-lived API token
    ma_token = _exchange_for_long_lived_token(ma_url, ma_session_token)

    # 5. Save and rediscover
    _save_ma_token_and_rediscover(ma_url, ma_token)

    return jsonify(
        {
            "success": True,
            "url": ma_url,
            "username": "",
            "message": "Connected to Music Assistant via Home Assistant.",
        }
    )


@api_bp.route("/api/ma/ha-login", methods=["POST"])
def api_ma_ha_login():
    """Authenticate with MA via Home Assistant OAuth flow.

    Multi-step flow:
      Step 1 — init:  {"step": "init", "username": "...", "password": "...", "ma_url": "..."}
                       Returns {"success": true, "step": "done", ...} or
                               {"success": true, "step": "mfa", "flow_id": "...", ...}
      Step 2 — mfa:   {"step": "mfa", "flow_id": "...", "code": "123456",
                        "state": "...", "ma_url": "..."}
                       Returns {"success": true, "step": "done", ...}

    On success (step=done), the MA token is saved to config.json.
    """
    data = request.get_json(silent=True) or {}
    step = (data.get("step") or "init").strip()
    ma_url = (data.get("ma_url") or "").strip().rstrip("/")

    if not ma_url:
        return jsonify({"success": False, "error": "MA URL is required"}), 400

    # ── Step dispatcher ───────────────────────────────────────────────────

    if step == "init":
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        if not username or not password:
            return jsonify({"success": False, "error": "Username and password are required"}), 400

        # Get OAuth state from MA
        oauth_info = _get_ma_oauth_params(ma_url)
        if not oauth_info:
            return jsonify({"success": False, "error": "MA does not support HA authentication"}), 400
        ha_url, client_id, redirect_uri, oauth_state = oauth_info

        # Start HA login flow
        flow = _ha_login_flow_start(ha_url, client_id, redirect_uri)
        if not flow or not flow.get("flow_id"):
            return jsonify({"success": False, "error": "Could not start HA authentication"}), 502

        flow_id = flow["flow_id"]

        # Submit credentials
        result = _ha_login_flow_step(ha_url, flow_id, {"username": username, "password": password}, client_id)
        if not result:
            return jsonify({"success": False, "error": "Authentication service unavailable"}), 502

        if result.get("type") == "create_entry":
            # No MFA — got auth code directly
            ha_code = result.get("result", "")
            ma_session_token = _ma_callback_exchange(ma_url, ha_code, oauth_state)
            if not ma_session_token:
                return jsonify({"success": False, "error": "Failed to exchange HA code for MA token"}), 500

            ma_token = _exchange_for_long_lived_token(ma_url, ma_session_token)
            _save_ma_token_and_rediscover(ma_url, ma_token, username)

            return jsonify(
                {
                    "success": True,
                    "step": "done",
                    "url": ma_url,
                    "username": username,
                    "message": "Connected to Music Assistant via Home Assistant.",
                }
            )

        if result.get("type") == "form" and result.get("step_id") == "mfa":
            placeholders = result.get("description_placeholders") or {}
            return jsonify(
                {
                    "success": True,
                    "step": "mfa",
                    "flow_id": flow_id,
                    "ha_url": ha_url,
                    "client_id": client_id,
                    "state": oauth_state,
                    "mfa_module_id": placeholders.get("mfa_module_id", "totp"),
                    "mfa_module_name": placeholders.get("mfa_module_name", "Authenticator app"),
                }
            )

        # Credential error
        errors = result.get("errors", {})
        err_msg = "Invalid credentials" if errors.get("base") == "invalid_auth" else "Authentication failed"
        return jsonify({"success": False, "error": err_msg}), 401

    elif step == "mfa":
        flow_id = (data.get("flow_id") or "").strip()
        ha_url = (data.get("ha_url") or "").strip().rstrip("/")
        client_id = (data.get("client_id") or "").strip()
        oauth_state = (data.get("state") or "").strip()
        code = (data.get("code") or "").replace(" ", "").replace("-", "")
        username = (data.get("username") or "").strip()

        if not flow_id or not ha_url or not oauth_state or not code:
            return jsonify({"success": False, "error": "Missing required fields"}), 400

        result = _ha_login_flow_step(ha_url, flow_id, {"code": code}, client_id)
        if not result:
            return jsonify({"success": False, "error": "Authentication service unavailable"}), 502

        if result.get("type") == "create_entry":
            ha_code = result.get("result", "")
            ma_session_token = _ma_callback_exchange(ma_url, ha_code, oauth_state)
            if not ma_session_token:
                return jsonify({"success": False, "error": "Failed to exchange HA code for MA token"}), 500

            ma_token = _exchange_for_long_lived_token(ma_url, ma_session_token)
            _save_ma_token_and_rediscover(ma_url, ma_token, username)

            return jsonify(
                {
                    "success": True,
                    "step": "done",
                    "url": ma_url,
                    "username": username or "HA user",
                    "message": "Connected to Music Assistant via Home Assistant.",
                }
            )

        if result.get("type") == "abort":
            return jsonify({"success": False, "error": "Session expired — please start again"}), 400

        return jsonify({"success": False, "error": "Invalid authentication code"}), 401

    return jsonify({"success": False, "error": f"Unknown step: {step}"}), 400


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

        with _clients_lock:
            snapshot = list(_clients)
        player_names = [c.player_name for c in snapshot]
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
    except Exception:
        logger.exception("MA rediscover failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


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
    syncgroup_id = data.get("syncgroup_id")

    if action not in ("next", "previous", "shuffle", "repeat", "seek"):
        return jsonify({"success": False, "error": f"Unknown action: {action}"}), 400

    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    try:
        from services.ma_monitor import send_queue_cmd

        fut = asyncio.run_coroutine_threadsafe(send_queue_cmd(action, value, syncgroup_id), loop)
        ok = fut.result(timeout=10.0)
        return jsonify({"success": ok})
    except Exception:
        logger.exception("MA queue command '%s' failed", action)
        return jsonify({"success": False, "error": "Internal error"}), 500


@api_bp.route("/api/pause", methods=["POST"])
def pause_player():
    """Pause or play a single daemon subprocess via WS controller command.

    Sends IPC cmd to the target daemon which calls send_group_command() over
    the existing WS connection — MA is the playback initiator and can
    re-establish group sync.
    """
    data = request.get_json() or {}
    player_name = data.get("player_name", "")
    action = data.get("action", "pause")
    with _clients_lock:
        snapshot = list(_clients)
    target = next((c for c in snapshot if getattr(c, "player_name", None) == player_name), None)
    if not target or not target.is_running():
        return jsonify({"success": False, "error": "Player not found or not running"}), 404
    loop = state.get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    try:
        fut = asyncio.run_coroutine_threadsafe(target._send_subprocess_command({"cmd": action}), loop)
        fut.result(timeout=2.0)
        return jsonify({"success": True, "action": action, "count": 1})
    except Exception:
        logger.exception("Pause/play command failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


def api_bt_reconnect():
    """Force reconnect a BT device (connect without re-pairing)."""
    try:
        data = request.get_json() or {}
        player_name = data.get("player_name")
        with _clients_lock:
            snapshot = list(_clients)
        client = next(
            (c for c in snapshot if getattr(c, "player_name", None) == player_name),
            None,
        )
        if client is None and snapshot:
            client = snapshot[0]
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
    except Exception:
        logger.exception("BT reconnect failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@api_bp.route("/api/bt/pair", methods=["POST"])
def api_bt_pair():
    """Force re-pair a BT device. Device must be in pairing mode."""
    try:
        data = request.get_json() or {}
        player_name = data.get("player_name")
        with _clients_lock:
            snapshot = list(_clients)
        client = next(
            (c for c in snapshot if getattr(c, "player_name", None) == player_name),
            None,
        )
        if client is None and snapshot:
            client = snapshot[0]
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
    except Exception:
        logger.exception("BT pairing failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@api_bp.route("/api/bt/management", methods=["POST"])
def api_bt_management():
    """Release or reclaim the BT adapter for a player."""
    data = request.get_json() or {}
    player_name = data.get("player_name")
    enabled = data.get("enabled")
    if enabled is None:
        return jsonify({"success": False, "error": 'Missing "enabled" field'}), 400
    with _clients_lock:
        snapshot = list(_clients)
    client = next((c for c in snapshot if getattr(c, "player_name", None) == player_name), None)
    if not client and snapshot:
        client = snapshot[0]
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
    except Exception as exc:
        logger.debug("sync HA options after toggle failed: %s", exc)
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
        result = get_client_status_for(snapshot[0])
    else:
        first = get_client_status_for(snapshot[0])
        result = {**first, "devices": [get_client_status_for(c) for c in snapshot]}
    result["groups"] = _build_groups_summary(snapshot)
    return jsonify(result)


def _build_groups_summary(clients: list) -> list[dict]:
    """Build a list of group objects from the current client list.

    Players sharing the same non-None group_id are merged into one group entry.
    Solo players (group_id=None) each appear as their own single-member group.

    When MA API group data is available, entries that resolve to the same MA
    syncgroup are merged (Sendspin assigns unique UUIDs per session, so two
    local devices in the same MA syncgroup have different group_ids).  Each
    merged entry is then enriched with ``external_members`` (players from
    other bridges) and ``external_count``.
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
            "server_connected": bool(status.get("server_connected")),
            "bluetooth_connected": bool(status.get("bluetooth_connected")),
        }

        if key not in groups:
            groups[key] = {
                "group_id": gid,
                "group_name": status.get("group_name"),
                "members": [],
            }

        groups[key]["members"].append(member)

    # Merge entries that resolve to the same MA syncgroup.
    # Sendspin assigns a unique UUID per session, so two local devices in
    # the same MA syncgroup appear as separate entries — merge them here.
    ma_groups = state.get_ma_groups()
    entry_syncgroup: dict[int, str] = {}  # index in merged → ma_syncgroup_id
    if ma_groups:
        syncgroup_map: dict[str, dict] = {}  # ma_syncgroup_id → merged entry
        merged: list[dict] = []
        for entry in groups.values():
            ma_syncgroup_id = None
            if entry["group_id"]:
                for m in entry["members"]:
                    pname = m.get("player_name")
                    if not pname:
                        continue
                    ma_info = state.get_ma_group_for_player(pname)
                    if ma_info:
                        ma_syncgroup_id = ma_info["id"]
                        if not entry["group_name"] and ma_info.get("name"):
                            entry["group_name"] = ma_info["name"]
                        break
            if ma_syncgroup_id and ma_syncgroup_id in syncgroup_map:
                # Merge into existing entry
                target = syncgroup_map[ma_syncgroup_id]
                target["members"].extend(entry["members"])
                if not target["group_name"] and entry.get("group_name"):
                    target["group_name"] = entry["group_name"]
            else:
                if ma_syncgroup_id:
                    syncgroup_map[ma_syncgroup_id] = entry
                    entry_syncgroup[len(merged)] = ma_syncgroup_id
                merged.append(entry)
    else:
        merged = list(groups.values())

    result = []
    for idx, entry in enumerate(merged):
        members = entry["members"]
        volumes = [m["volume"] for m in members]
        entry["avg_volume"] = round(sum(volumes) / len(volumes)) if volumes else 100
        entry["playing"] = any(m["playing"] for m in members)
        entry["external_members"] = []
        entry["external_count"] = 0

        # Enrich with cross-bridge member info from MA API cache
        ma_sid = entry_syncgroup.get(idx)
        if ma_sid and ma_groups:
            ma_group = next((g for g in ma_groups if g["id"] == ma_sid), None)
            if ma_group:
                local_names = {(m["player_name"] or "").lower() for m in members}
                external = [
                    {"name": m["name"], "available": m.get("available", True)}
                    for m in ma_group.get("members", [])
                    if m.get("name", "").lower() not in local_names
                ]
                entry["external_members"] = external
                entry["external_count"] = len(external)

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
    /api/status every 2 seconds.  A heartbeat comment is sent every 15 s to
    keep the connection alive through proxies (including HA ingress).

    Uses ``threading.Condition.wait_for()`` to avoid the race between reading
    ``_status_version`` and blocking: the Condition lock ensures that any
    ``notify_status_changed()`` call either happens before we start waiting
    (so ``wait_for`` returns immediately) or wakes us up cleanly.
    """
    global _sse_count
    with _sse_lock:
        if _sse_count >= _MAX_SSE:
            return 'data: {"error": "too many listeners"}\n\n', 503, {"Content-Type": "text/event-stream"}
        _sse_count += 1

    def _generate():
        global _sse_count
        try:

            def _build_snapshot():
                with _clients_lock:
                    snapshot = list(_clients)
                if not snapshot:
                    return None
                if len(snapshot) == 1:
                    data = get_client_status_for(snapshot[0])
                else:
                    first = get_client_status_for(snapshot[0])
                    data = {**first, "devices": [get_client_status_for(c) for c in snapshot]}
                data["groups"] = _build_groups_summary(snapshot)
                return data

            # Send current status immediately so the client doesn't have to wait
            # for the first change event (important through HA ingress proxy).
            #
            # Leading 2 KB padding flushes proxy buffers (Nginx, HA Ingress,
            # Cloudflare) so they start streaming instead of buffering the
            # entire response.
            yield ": " + " " * 2048 + "\n\n"

            initial = _build_snapshot()
            if initial:
                yield f"data: {json.dumps(initial)}\n\n"

            last_version = state.get_status_version()
            started = time.monotonic()
            while True:
                if time.monotonic() - started >= _SSE_MAX_LIFETIME:
                    yield 'data: {"error": "session expired"}\n\n'
                    break

                changed, last_version = state.wait_for_status_change(last_version, timeout=15)

                if changed:
                    data = _build_snapshot()
                    if data:
                        yield f"data: {json.dumps(data)}\n\n"
                else:
                    # 15 s timeout — send a keepalive comment so proxies don't close
                    yield ": heartbeat\n\n"
        finally:
            with _sse_lock:
                _sse_count -= 1

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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
                "sendspin_port": int(config.get("SENDSPIN_PORT") or 9000),
                "bridge_name": config.get("BRIDGE_NAME", ""),
                "tz": config.get("TZ", ""),
                "pulse_latency_msec": int(config.get("PULSE_LATENCY_MSEC") or 200),
                "prefer_sbc_codec": bool(config.get("PREFER_SBC_CODEC", False)),
                "bt_check_interval": int(config.get("BT_CHECK_INTERVAL") or 10),
                "bt_max_reconnect_fails": int(config.get("BT_MAX_RECONNECT_FAILS") or 0),
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
            with config_lock, open(CONFIG_FILE) as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()

        # Never expose secrets to the browser
        config.pop("AUTH_PASSWORD_HASH", None)
        config.pop("SECRET_KEY", None)

        # Enrich BLUETOOTH_DEVICES with resolved listen_port / listen_host from running clients
        with _clients_lock:
            snapshot = list(_clients)
        client_map = {getattr(c, "player_name", None): c for c in snapshot}
        mac_map = {getattr(getattr(c, "bt_manager", None), "mac_address", None): c for c in snapshot}
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

    # Validate top-level port (empty string treated as unset)
    sp = config.get("SENDSPIN_PORT")
    if sp is not None and sp != "":
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
        "MA_USERNAME",
        "VOLUME_VIA_MA",
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
                # The form pre-fills MA_API_TOKEN with the stored value.
                # Empty string = user explicitly cleared it → do NOT restore.
                # (No implicit preserve needed — the field is always submitted.)
                # Preserve MA_USERNAME if not submitted
                if not config.get("MA_USERNAME") and existing.get("MA_USERNAME"):
                    config["MA_USERNAME"] = existing["MA_USERNAME"]
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
            except OSError as exc:
                logger.debug("cleanup temp config file failed: %s", exc)
            raise

    # Invalidate adapter name cache so next status poll picks up changes
    with _adapter_cache_lock:
        load_adapter_name_cache()

    _reload_volume_via_ma()
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

    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    if not password:
        return jsonify({"error": "password is required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    from config import hash_password as _hash_pw

    pw_hash = _hash_pw(password)

    try:
        update_config(lambda cfg: cfg.__setitem__("AUTH_PASSWORD_HASH", pw_hash))
    except Exception as exc:
        logger.debug("read config for auth update failed: %s", exc)

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
    try:
        update_config(lambda cfg: cfg.__setitem__("LOG_LEVEL", level))
    except Exception as exc:
        logger.debug("read config for log level update failed: %s", exc)

    # Propagate to all running subprocesses via stdin IPC
    loop = state.get_main_loop()
    if loop is not None:
        cmd = {"cmd": "set_log_level", "level": level}
        with _clients_lock:
            snapshot = list(_clients)
        for client in snapshot:
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
        macs = list_bt_adapters()
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


def _run_bluetoothctl_scan(adapter_macs: "list[str]") -> str:
    """Run a bluetoothctl scan session and return combined stdout."""
    post_scan_cmds: list[str] = []
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
    return result_stdout


def _parse_scan_output(stdout: str) -> "tuple[set[str], dict[str, str], dict[str, str], set[str]]":
    """Parse bluetoothctl scan output into (seen_macs, names, device_adapter, active_macs)."""
    seen: set[str] = set()
    names: dict[str, str] = {}
    device_adapter: dict[str, str] = {}
    active_macs: set[str] = set()
    current_show_adapter: str = ""
    for line in stdout.splitlines():
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
    return seen, names, device_adapter, active_macs


def _resolve_unnamed_devices(all_macs: "set[str]", names: "dict[str, str]") -> None:
    """Look up names for unnamed devices from the bluetoothctl device cache."""
    unnamed = {mac for mac in all_macs if mac not in names}
    if not unnamed:
        return
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


def _enrich_audio_device(mac: str, names: "dict[str, str]") -> "dict | None":
    """Return device info dict if the device is audio-capable, else None."""
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


_MAX_SCAN_RESULTS = 50


def _run_bt_scan(job_id: str) -> None:
    """Perform BT scan in a background thread and store result in state."""
    try:
        adapter_macs = list_bt_adapters()

        result_stdout = _run_bluetoothctl_scan(adapter_macs)
        seen, names, device_adapter, active_macs = _parse_scan_output(result_stdout)
        all_macs = seen | active_macs

        if len(all_macs) > _MAX_SCAN_RESULTS:
            logger.warning("BT scan found %d devices, capping to %d", len(all_macs), _MAX_SCAN_RESULTS)
            all_macs = set(list(all_macs)[:_MAX_SCAN_RESULTS])

        _resolve_unnamed_devices(all_macs, names)

        devices = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_enrich_audio_device, mac, names): mac for mac in all_macs}
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


@api_bp.route("/api/debug/ma")
def api_debug_ma():
    """Debug: dump MA now-playing cache, groups, per-client player_ids, and live queues."""
    import asyncio as _asyncio

    with state._ma_now_playing_lock:
        cache = dict(state._ma_now_playing)
    groups = state.get_ma_groups()
    clients_info = [
        {
            "player_name": getattr(c, "player_name", None),
            "player_id": getattr(c, "player_id", None),
            "group_id": c.status.get("group_id") if hasattr(c, "status") else None,
        }
        for c in _clients
    ]

    # Fetch live queue ids from MA WebSocket
    ma_url, ma_token = state.get_ma_api_credentials()
    live_queue_ids: list[str] = []
    if ma_url and ma_token:
        try:
            import websockets

            _ws_kw: dict = {"proxy": None} if int(websockets.__version__.split(".")[0]) >= 15 else {}

            async def _fetch():
                ws_url = ma_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
                async with websockets.connect(
                    ws_url, additional_headers={"Authorization": f"Bearer {ma_token}"}, **_ws_kw
                ) as ws:
                    import json as _json

                    await ws.send(_json.dumps({"command": "player_queues/all", "args": {}, "message_id": 99}))
                    for _ in range(10):
                        msg = _json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if str(msg.get("message_id")) == "99":
                            return [q.get("queue_id", "") for q in (msg.get("result") or [])]
                return []

            loop = state.get_main_loop()
            if loop:
                fut = _asyncio.run_coroutine_threadsafe(_fetch(), loop)
                live_queue_ids = fut.result(timeout=10)
        except Exception as e:
            live_queue_ids = [f"error: {e}"]

    return jsonify(
        {
            "cache_keys": list(cache.keys()),
            "groups": groups,
            "clients": clients_info,
            "live_queue_ids": live_queue_ids,
        }
    )


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
        with _clients_lock:
            snapshot = list(_clients)
        for client in snapshot:
            bt_mgr = getattr(client, "bt_manager", None)
            device_diag.append(
                {
                    "name": getattr(client, "player_name", "Unknown"),
                    "mac": bt_mgr.mac_address if bt_mgr else None,
                    "connected": client.status.get("bluetooth_connected", False),
                    "enabled": getattr(client, "bt_management_enabled", True),
                    "sink": getattr(client, "bluetooth_sink_name", None),
                    "last_error": client.status.get("last_error"),
                }
            )
        diag["devices"] = device_diag

        # MA API integration status
        ma_url, ma_token = state.get_ma_api_credentials()
        ma_groups = state.get_ma_groups()

        # Build a name→client lookup for matching MA members to bridge devices
        bridge_by_name = {getattr(c, "player_name", "").lower(): c for c in snapshot}

        enriched_groups = []
        for g in ma_groups:
            members_detail = []
            for m in g.get("members", []):
                mname = (m.get("name") or m.get("id", "")).lower()
                # Match to a local bridge client by name (case-insensitive substring)
                bridge_client = None
                for bname, bc in bridge_by_name.items():
                    if bname and (bname in mname or mname in bname):
                        bridge_client = bc
                        break
                member_info: dict = {
                    "id": m.get("id", ""),
                    "name": m.get("name", m.get("id", "")),
                    "state": m.get("state"),
                    "volume": m.get("volume"),
                    "available": m.get("available", True),
                    "is_bridge": bridge_client is not None,
                }
                if bridge_client:
                    member_info["enabled"] = getattr(bridge_client, "bt_management_enabled", True)
                    member_info["bt_connected"] = bridge_client.status.get("bluetooth_connected", False)
                    member_info["server_connected"] = bridge_client.status.get("server_connected", False)
                    member_info["playing"] = bridge_client.status.get("playing", False)
                    member_info["sink"] = getattr(bridge_client, "bluetooth_sink_name", None)
                    member_info["bt_mac"] = (
                        getattr(bridge_client.bt_manager, "mac_address", None) if bridge_client.bt_manager else None
                    )
                members_detail.append(member_info)

            np = state.get_ma_now_playing_for_group(g["id"])
            group_info: dict = {
                "id": g["id"],
                "name": g.get("name", ""),
                "members": members_detail,
            }
            if np:
                group_info["now_playing"] = {
                    "title": np.get("title"),
                    "artist": np.get("artist"),
                    "state": np.get("state"),
                }
            enriched_groups.append(group_info)

        diag["ma_integration"] = {
            "configured": bool(ma_url and ma_token),
            "connected": state.is_ma_connected(),
            "url": ma_url or "",
            "syncgroups": enriched_groups,
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
    except Exception:
        logger.exception("Diagnostics collection failed")
        return jsonify({"error": "Internal error"}), 500


@api_bp.route("/api/health")
def api_health():
    """Lightweight health check — no auth required, no sensitive data."""
    return jsonify({"ok": True})


@api_bp.route("/api/preflight")
def api_preflight():
    """Setup verification endpoint — no auth required, no sensitive data.

    Returns platform, audio, bluetooth, and D-Bus status for
    quick troubleshooting without exposing device details.
    """
    import platform as _platform

    # Platform
    arch = _platform.machine()

    # Audio
    audio_info: dict = {"system": "unknown", "socket": None, "sinks": 0}
    try:
        srv = get_server_name()
        if srv and "pipewire" in srv.lower():
            audio_info["system"] = "pipewire"
        elif srv:
            audio_info["system"] = "pulseaudio"
        pulse_sock = os.environ.get("PULSE_SERVER", "")
        if pulse_sock:
            audio_info["socket"] = pulse_sock
        sinks = list_sinks()
        audio_info["sinks"] = len(sinks) if sinks else 0
    except Exception:
        pass

    # Bluetooth
    bt_info: dict = {"controller": False, "adapter": None, "paired_devices": 0}
    try:
        result = subprocess.run(
            ["bluetoothctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "Controller" in result.stdout:
            bt_info["controller"] = True
            bt_info["adapter"] = result.stdout.split()[1] if result.stdout.split() else None
        paired = subprocess.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        bt_info["paired_devices"] = paired.stdout.strip().count("Device")
    except Exception:
        pass

    # D-Bus
    dbus_ok = os.path.exists("/var/run/dbus/system_bus_socket") or os.path.exists("/run/dbus/system_bus_socket")

    # Memory
    mem_mb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_mb = int(line.split()[1]) // 1024
                    break
    except Exception:
        pass

    return jsonify(
        {
            "ok": True,
            "platform": arch,
            "audio": audio_info,
            "bluetooth": bt_info,
            "dbus": dbus_ok,
            "memory_mb": mem_mb,
            "version": VERSION,
        }
    )


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
