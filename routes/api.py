"""
API Blueprint for sendspin-bt-bridge.

Core /api/* routes: restart, volume, mute, pause, and BT reconnect/pair/management.
Configuration, status, and diagnostics routes live in api_config.py and api_status.py.
"""

import asyncio
import concurrent.futures
import logging
import os
import signal
import subprocess
import threading
import time

from flask import Blueprint, jsonify, request

import state
from config import save_device_volume
from routes.api_config import _detect_runtime, get_mute_via_ma, get_volume_via_ma
from services.device_registry import get_device_registry_snapshot
from services.pulse import (
    get_sink_mute,
    set_sink_mute,
    set_sink_volume,
)
from services.status_snapshot import build_device_snapshot_pairs

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)


# ---------------------------------------------------------------------------
# Volume persistence debounce — decouple immediate pactl call from slow disk write
# ---------------------------------------------------------------------------

_volume_timers: dict[str, threading.Timer] = {}
_volume_timers_lock = threading.Lock()


def _submit_loop_coroutine(loop, coro, *, description: str) -> bool:
    """Schedule work on the main loop without blocking the request thread."""
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        if asyncio.iscoroutine(coro):
            coro.close()
        logger.debug("Could not schedule %s: %s", description, exc)
        return False

    add_done_callback = getattr(future, "add_done_callback", None)
    if callable(add_done_callback):

        def _log_completion(done_future) -> None:
            result_getter = getattr(done_future, "result", None)
            if not callable(result_getter):
                return
            try:
                result_getter()
            except Exception as exc:
                logger.debug("%s failed asynchronously: %s", description, exc)

        add_done_callback(_log_completion)
    return True


def _persist_volume(mac: str, volume: int) -> None:
    """Write volume to config.json (called via debounce timer, not inline)."""
    with _volume_timers_lock:
        _volume_timers.pop(mac, None)
    save_device_volume(mac, volume)


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


def _select_target_pairs(clients, *, group_id=None, player_names=None, player_name=None):
    """Return `(client, snapshot)` pairs matching the request target selectors."""
    target_pairs = build_device_snapshot_pairs(clients)
    if group_id is not None:
        return [(client, device) for client, device in target_pairs if device.extra.get("group_id") == group_id]
    if player_names is not None:
        return [
            (client, device) for client, device in target_pairs if getattr(client, "player_name", None) in player_names
        ]
    if player_name:
        return [
            (client, device) for client, device in target_pairs if getattr(client, "player_name", None) == player_name
        ]
    return target_pairs


def _ensure_target_pairs(targets):
    """Normalize legacy client lists and snapshot-pair lists to `(client, snapshot)` pairs."""
    if not targets:
        return []
    first = targets[0]
    if isinstance(first, tuple) and len(first) == 2:
        return list(targets)
    target_pairs = build_device_snapshot_pairs(list(targets))
    for client, device in target_pairs:
        status_get = getattr(getattr(client, "status", None), "get", None)
        if not callable(status_get):
            continue
        for key in ("group_id", "group_name", "muted"):
            if device.extra.get(key) is None:
                value = status_get(key)
                if value is not None:
                    device.extra[key] = value
    return target_pairs


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


def _set_volume_via_ma(target_pairs, volume: int, *, is_group: bool = False) -> bool:
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
    target_pairs = _ensure_target_pairs(target_pairs)

    if is_group and target_pairs:
        # Group volume: send one group_volume per unique sync group
        seen_groups: set[str] = set()
        for client, device in target_pairs:
            gid = device.extra.get("group_id")
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
    for client, _device in target_pairs:
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
    return bool(target_pairs)


def _set_mute_via_ma(target_pairs, muted: bool) -> bool:
    """Proxy mute change through MA WebSocket API."""
    from services.ma_monitor import send_player_cmd

    loop = state.get_main_loop()
    if not loop:
        return False
    target_pairs = _ensure_target_pairs(target_pairs)

    for client, _device in target_pairs:
        pid = getattr(client, "player_id", None)
        if not pid:
            continue
        try:
            fut = asyncio.run_coroutine_threadsafe(
                send_player_cmd("players/cmd/volume_mute", {"player_id": pid, "muted": muted}),
                loop,
            )
            if not fut.result(timeout=2.0):
                return False
        except Exception:
            logger.debug("MA volume_mute failed for %s", pid, exc_info=True)
            return False
    return bool(target_pairs)


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

        snapshot = get_device_registry_snapshot().active_clients
        target_pairs = _select_target_pairs(
            snapshot,
            group_id=group_id,
            player_names=player_names,
            player_name=player_name,
        )
        targets = [client for client, _device in target_pairs]

        # --- MA path: proxy through MA API when connected ---
        if not force_local and get_volume_via_ma() and state.is_ma_connected() and targets:
            ma_ok = _set_volume_via_ma(target_pairs, volume, is_group=is_group)
            if ma_ok:
                # Do NOT update local status — bridge_daemon will receive the
                # VolumeChanged echo from MA via sendspin protocol, apply pactl,
                # and report the actual volume through subprocess stdout.
                #
                # However, devices NOT in a MA sync group won't receive the
                # echo.  Apply volume locally for those orphan devices.
                if is_group:
                    orphans = [client for client, device in target_pairs if not device.extra.get("group_id")]
                    for client in orphans:
                        if client.bluetooth_sink_name:
                            ok = set_sink_volume(client.bluetooth_sink_name, volume)
                            if ok:
                                client._update_status({"volume": volume})
                                _loop = state.get_main_loop()
                                if _loop:
                                    _submit_loop_coroutine(
                                        _loop,
                                        client._send_subprocess_command({"cmd": "set_volume", "value": volume}),
                                        description=f"set_volume for {client.player_name}",
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
                    _submit_loop_coroutine(
                        loop,
                        client._send_subprocess_command({"cmd": "set_volume", "value": volume}),
                        description=f"set_volume for {client.player_name}",
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

        snapshot = get_device_registry_snapshot().active_clients
        target_pairs = _select_target_pairs(snapshot, player_names=player_names, player_name=player_name)
        if player_names is None and not player_name:
            target_pairs = target_pairs[:1]
        targets = [client for client, _device in target_pairs]
        target_snapshot_map = {id(client): device for client, device in target_pairs}

        # --- MA path ---
        if not force_local and get_mute_via_ma() and state.is_ma_connected() and targets:
            # Resolve desired mute state
            current_muted = bool(target_pairs[0][1].extra.get("muted", False)) if target_pairs else False
            desired = bool(mute_value) if mute_value is not None else not current_muted
            if _set_mute_via_ma(target_pairs, desired):
                # Also apply to PulseAudio sink so audio actually mutes/unmutes
                for client in targets:
                    if client.bluetooth_sink_name:
                        set_sink_mute(client.bluetooth_sink_name, desired)
                    client._update_status({"muted": desired})
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
                        snapshot_device = target_snapshot_map.get(id(client))
                        current_muted = bool(snapshot_device.extra.get("muted", False)) if snapshot_device else False
                        muted = bool(mute_value) if mute_value is not None else not current_muted
                    client._update_status({"muted": muted})
                    if loop:
                        _submit_loop_coroutine(
                            loop,
                            client._send_subprocess_command({"cmd": "set_mute", "muted": muted}),
                            description=f"set_mute for {client.player_name}",
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

    snapshot_pairs = build_device_snapshot_pairs(get_device_registry_snapshot().active_clients)

    if action == "pause":
        # One pause command per unique Sendspin session group (MA propagates to all members)
        seen_groups: set = set()
        for client, device in snapshot_pairs:
            if not client.is_running():
                continue
            gid = device.extra.get("group_id")
            if gid:
                if gid in seen_groups:
                    continue
                seen_groups.add(gid)
            try:
                if _submit_loop_coroutine(
                    loop,
                    client._send_subprocess_command({"cmd": "pause"}),
                    description=f"pause for {client.player_name}",
                ):
                    count += 1
            except Exception as exc:
                logger.debug("Could not queue pause for %s: %s", client.player_name, exc)

    else:  # play / unpause
        ma_url, ma_token = state.get_ma_api_credentials()
        seen_ma_syncgroups: set = set()
        seen_session_groups: set = set()

        for client, device in snapshot_pairs:
            if not client.is_running():
                continue

            # Try MA syncgroup play first (preserves group sync)
            if ma_url and ma_token:
                ma_group = state.get_ma_group_for_player(getattr(client, "player_id", ""))
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
            gid = device.extra.get("group_id")
            if gid:
                if gid in seen_session_groups:
                    continue
                seen_session_groups.add(gid)
            try:
                if _submit_loop_coroutine(
                    loop,
                    client._send_subprocess_command({"cmd": "play"}),
                    description=f"play for {client.player_name}",
                ):
                    count += 1
            except Exception as exc:
                logger.debug("Could not queue play for %s: %s", client.player_name, exc)

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
    snapshot_pairs = build_device_snapshot_pairs(get_device_registry_snapshot().active_clients)
    target_pair = next(
        (
            (client, device)
            for client, device in snapshot_pairs
            if client.is_running() and device.extra.get("group_id") == group_id
        ),
        None,
    )
    if not target_pair:
        return jsonify({"success": False, "error": "Group not found or no running members"}), 404
    target, target_device = target_pair

    # For play: prefer MA API so the persistent syncgroup resumes all members in sync
    if action == "play":
        ma_url, ma_token = state.get_ma_api_credentials()
        if ma_url and ma_token:
            ma_group = state.get_ma_group_for_player(getattr(target, "player_id", ""))
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
        scheduled = _submit_loop_coroutine(
            loop,
            target._send_subprocess_command({"cmd": action}),
            description=f"{action} for group {group_id}",
        )
        if not scheduled:
            return jsonify({"success": False, "error": "Could not schedule command"}), 503
        group_name = target_device.extra.get("group_name")
        return jsonify({"success": True, "action": action, "group_id": group_id, "group_name": group_name})
    except Exception:
        logger.exception("Group pause/play failed")
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
    snapshot = get_device_registry_snapshot().active_clients
    target = next((c for c in snapshot if getattr(c, "player_name", None) == player_name), None)
    if not target or not target.is_running():
        return jsonify({"success": False, "error": "Player not found or not running"}), 404
    loop = state.get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    try:
        scheduled = _submit_loop_coroutine(
            loop,
            target._send_subprocess_command({"cmd": action}),
            description=f"{action} for {player_name}",
        )
        if not scheduled:
            return jsonify({"success": False, "error": "Could not schedule command"}), 503
        return jsonify({"success": True, "action": action, "count": 1})
    except Exception:
        logger.exception("Pause/play command failed")
        return jsonify({"success": False, "error": "Internal error"}), 500
