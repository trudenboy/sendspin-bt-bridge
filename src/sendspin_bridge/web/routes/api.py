"""
API Blueprint for sendspin-bt-bridge.

Core /api/* routes: restart, volume, mute, pause, and BT reconnect/pair/management.
Configuration, status, and diagnostics routes live in api_config.py and api_status.py.
"""

import asyncio
import concurrent.futures
import io
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
import wave

from flask import Blueprint, Response, jsonify, request

from sendspin_bridge.config import save_device_buffer_setting, save_device_static_delay, save_device_volume
from sendspin_bridge.services.audio.latency_calibration import build_calibration_pcm
from sendspin_bridge.services.audio.pulse import (
    get_sink_mute,
    set_sink_mute,
    set_sink_volume,
)
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot_pairs
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials, get_ma_group_for_player
from sendspin_bridge.web.routes.api_config import _detect_runtime

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)
_restart_override = None
_calibration_sessions: dict[str, dict] = {}
_calibration_lock = threading.Lock()
_CALIBRATION_ERROR_MESSAGES = {
    "silence": "No calibration sound was detected; check microphone permission and move closer",
    "weak_correlation": "The recordings did not match reliably; keep the phone still, move closer, and reduce noise",
    "insufficient_samples": "The microphone recording was too short; keep this page active and retry",
}


def _running_in_container() -> bool:
    """Return whether the current process has an external container supervisor."""
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as cgroup_file:
            cgroup = cgroup_file.read().lower()
    except OSError:
        return False
    return any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "libpod"))


@api_bp.route("/api/calibration/tone.wav", methods=["GET"])
def calibration_tone():
    """Return a deterministic click track for ordinary MA group playback."""
    sample_rate = 48000
    duration_seconds = 8
    frames = build_calibration_pcm(sample_rate=sample_rate, duration_seconds=duration_seconds)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return Response(output.getvalue(), mimetype="audio/wav", headers={"Cache-Control": "public, max-age=86400"})


@api_bp.route("/api/calibration/play", methods=["POST"])
def play_calibration_tone():
    """Play the calibration click track directly through one Bluetooth sink."""
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id") or "").strip()
    client = next(
        (
            item
            for item in get_device_registry_snapshot().active_clients
            if str(getattr(item, "player_id", "")) == player_id
        ),
        None,
    )
    if client is None:
        return jsonify({"success": False, "error": "Unknown player_id"}), 404
    loop = get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Runtime loop unavailable"}), 503
    try:
        future = asyncio.run_coroutine_threadsafe(client.play_calibration_tone(), loop)
        played = bool(future.result(timeout=12.0))
    except Exception:
        logger.exception("Calibration tone playback failed")
        return jsonify({"success": False, "error": "Calibration tone playback failed"}), 503
    if not played:
        return jsonify({"success": False, "error": "Bluetooth audio sink is unavailable"}), 409
    return jsonify({"success": True, "player_id": player_id})


@api_bp.route("/api/calibration/metronome", methods=["POST"])
def set_calibration_metronome():
    """Start or stop the phase-aligned continuous click track for one sink."""
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id") or "").strip()
    action = str(data.get("action") or "").strip().lower()
    if action not in {"start", "stop"}:
        return jsonify({"success": False, "error": "action must be start or stop"}), 400
    client = next(
        (
            item
            for item in get_device_registry_snapshot().active_clients
            if str(getattr(item, "player_id", "")) == player_id
        ),
        None,
    )
    if client is None:
        return jsonify({"success": False, "error": "Unknown player_id"}), 404
    loop = get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Runtime loop unavailable"}), 503
    try:
        if action == "start":
            future = asyncio.run_coroutine_threadsafe(client.start_calibration_metronome(), loop)
            started = bool(future.result(timeout=5.0))
            if not started:
                return jsonify({"success": False, "error": "Bluetooth audio sink is unavailable"}), 409
            active = True
        else:
            future = asyncio.run_coroutine_threadsafe(client.stop_calibration_metronome(), loop)
            future.result(timeout=5.0)
            active = False
    except Exception:
        logger.exception("Calibration metronome update failed")
        return jsonify({"success": False, "error": "Calibration metronome update failed"}), 503
    return jsonify({"success": True, "player_id": player_id, "active": active})


@api_bp.route("/api/calibration/sessions", methods=["POST"])
def create_calibration_session():
    """Create an in-memory relative microphone calibration session."""
    now = time.time()
    session_id = str(uuid.uuid4())
    with _calibration_lock:
        expired = [key for key, value in _calibration_sessions.items() if now - value["created_at"] > 600]
        for key in expired:
            del _calibration_sessions[key]
        _calibration_sessions[session_id] = {"created_at": now, "recordings": {}}
    return jsonify({"success": True, "session_id": session_id, "expires_in_seconds": 600}), 201


@api_bp.route("/api/calibration/sessions/<session_id>/audio", methods=["POST"])
def upload_calibration_audio(session_id: str):
    """Accept bounded Float32-like samples and return a relative estimate."""
    data = request.get_json(silent=True) or {}
    role = str(data.get("role") or "")
    samples = data.get("samples")
    raw_sample_rate = data.get("sample_rate")
    try:
        sample_rate = int(raw_sample_rate) if raw_sample_rate is not None else 0
    except (TypeError, ValueError):
        sample_rate = 0
    if role not in {"reference", "target"} or not isinstance(samples, list):
        return jsonify({"success": False, "error": "role and samples are required"}), 400
    max_samples = min(sample_rate * 10, 500_000)
    if sample_rate < 8000 or sample_rate > 192000 or len(samples) < 8 or len(samples) > max_samples:
        return jsonify({"success": False, "error": "Unsupported recording size or sample rate"}), 400
    try:
        normalized = [max(-1.0, min(1.0, float(value))) for value in samples]
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Samples must be numeric"}), 400
    peak = max(abs(value) for value in normalized)
    logger.info(
        "Calibration recording received: session=%s role=%s samples=%d rate=%d peak=%.4f",
        session_id[:8],
        role,
        len(normalized),
        sample_rate,
        peak,
    )
    with _calibration_lock:
        session = _calibration_sessions.get(session_id)
        if session is None or time.time() - session["created_at"] > 600:
            _calibration_sessions.pop(session_id, None)
            return jsonify({"success": False, "error": "Calibration session expired"}), 404
        session["recordings"][role] = (sample_rate, normalized)
        recordings = dict(session["recordings"])
    if set(recordings) != {"reference", "target"}:
        return jsonify({"success": True, "status": "waiting_for_other_recording"})
    if recordings["reference"][0] != recordings["target"][0]:
        return jsonify({"success": False, "error": "Recordings must use the same sample rate"}), 400
    from sendspin_bridge.services.audio.latency_calibration import estimate_relative_delay_ms

    estimate = estimate_relative_delay_ms(
        recordings["reference"][1], recordings["target"][1], sample_rate=recordings["reference"][0]
    )
    log_method = logger.info if estimate.valid else logger.warning
    log_method(
        "Calibration analysis completed: session=%s valid=%s delay_ms=%s confidence=%.4f reason=%s",
        session_id[:8],
        estimate.valid,
        estimate.delay_ms,
        estimate.confidence,
        estimate.reason or "ok",
    )
    payload = {"success": estimate.valid, "status": "complete", "estimate": estimate.to_dict()}
    if not estimate.valid:
        payload["error"] = _CALIBRATION_ERROR_MESSAGES.get(
            estimate.reason,
            "Calibration analysis could not produce a reliable result",
        )
    return jsonify(payload)


@api_bp.route("/api/calibration/sessions/<session_id>", methods=["DELETE"])
def delete_calibration_session(session_id: str):
    with _calibration_lock:
        _calibration_sessions.pop(session_id, None)
    return jsonify({"success": True})


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
        # Purge stale entries from disconnected devices
        stale = [k for k, t in _volume_timers.items() if not t.is_alive()]
        for k in stale:
            del _volume_timers[k]
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


@api_bp.route("/api/latency", methods=["POST"])
def set_latency_setting():
    """Hot-apply a confirmed per-device latency setting."""
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id") or "").strip()
    field = str(data.get("field") or "static_delay_ms")
    if field not in {"static_delay_ms", "required_lead_time_ms", "min_buffer_ms"}:
        return jsonify({"success": False, "error": "Unsupported latency field"}), 400
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid latency value"}), 400
    max_value = 5000 if field == "static_delay_ms" else 30000
    if not 0 <= value <= max_value:
        return jsonify({"success": False, "error": f"{field} must be between 0 and {max_value}"}), 400

    client = next(
        (
            item
            for item in get_device_registry_snapshot().active_clients
            if str(getattr(item, "player_id", "")) == player_id
        ),
        None,
    )
    if client is None:
        return jsonify({"success": False, "error": "Unknown player_id"}), 404
    expected_revision = data.get("recommendation_revision")
    if expected_revision is not None and expected_revision != client.status.get("latency_suggestion_revision"):
        return jsonify({"success": False, "error": "Latency recommendation changed; refresh and retry"}), 409
    loop = get_main_loop()
    if loop is None:
        return jsonify({"success": False, "error": "Runtime loop unavailable"}), 503
    try:
        future = asyncio.run_coroutine_threadsafe(client.apply_hot_config({field: value}), loop)
        applied = future.result(timeout=2.0)
    except Exception:
        logger.exception("Latency hot-apply failed")
        return jsonify({"success": False, "error": "Latency hot-apply failed"}), 503
    if field not in applied:
        return jsonify({"success": False, "error": "Latency value was not applied"}), 503

    mac = getattr(getattr(client, "bt_manager", None), "mac_address", None)
    if field == "static_delay_ms" and mac:
        source = str(data.get("source") or "manual")
        save_device_static_delay(
            mac,
            round(value),
            source=source,
            codec=client.status.get("bt_codec_name") or client.status.get("audio_format"),
        )
        client._update_status(
            {
                "static_delay_source": source,
                "static_delay_codec": client.status.get("bt_codec_name") or client.status.get("audio_format"),
            }
        )
    elif mac:
        save_device_buffer_setting(mac, field, round(value))
    return jsonify({"success": True, "player_id": player_id, "field": field, "value": round(value)})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@api_bp.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart the bridge under systemd, HA, Docker, or a direct Python launch."""
    if _restart_override is not None:
        override_response = _restart_override()
        if override_response is not None:
            return override_response
    runtime = _detect_runtime()
    if runtime == "docker" and not _running_in_container():
        runtime = "standalone"
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
                        except (ProcessLookupError, PermissionError):
                            os.kill(os.getpid(), signal.SIGTERM)
                else:
                    os.kill(os.getpid(), signal.SIGTERM)

            threading.Thread(target=_do_ha_restart, daemon=True).start()
        elif runtime == "docker":

            def _do_docker():
                time.sleep(0.5)
                try:
                    os.kill(1, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    os.kill(os.getpid(), signal.SIGTERM)

            threading.Thread(target=_do_docker, daemon=True).start()
        else:

            def _do_standalone():
                time.sleep(0.5)
                try:
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "sendspin_bridge.services.lifecycle.standalone_restart",
                            str(os.getpid()),
                        ],
                        cwd=os.getcwd(),
                        env=os.environ.copy(),
                        stdin=subprocess.DEVNULL,
                        close_fds=True,
                        start_new_session=True,
                    )
                except Exception:
                    logger.exception("Could not launch standalone restart helper")
                    return
                os.kill(os.getpid(), signal.SIGTERM)

            threading.Thread(target=_do_standalone, daemon=True).start()

        return jsonify({"success": True, "runtime": runtime})
    except Exception:
        logger.exception("Restart failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@api_bp.route("/api/volume", methods=["POST"])
def set_volume():
    """Set player volume via direct pactl.

    Sendspin's ``PulseVolumeController`` (in ``services/pa_volume_controller``)
    subscribes to PA sink change events and proactively notifies MA of any
    externally-applied state change, so MA's own UI stays in sync without
    the bridge needing to proxy through ``players/cmd/volume_set`` first.
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

        snapshot = get_device_registry_snapshot().active_clients
        target_pairs = _select_target_pairs(
            snapshot,
            group_id=group_id,
            player_names=player_names,
            player_name=player_name,
        )
        targets = [client for client, _device in target_pairs]

        # --- Direct pactl path (only path now) ---
        def _set_one(client):
            if not client.bluetooth_sink_name:
                return None
            ok = set_sink_volume(client.bluetooth_sink_name, volume)
            if ok:
                client._update_status({"volume": volume})
                loop = get_main_loop()
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
    """Toggle or set mute via direct pactl.

    Sendspin's ``PulseVolumeController`` subscribes to PA sink change events
    and proactively pushes externally-applied mute changes to MA, so the
    bridge no longer needs an MA WebSocket proxy here.
    """
    try:
        data = request.get_json() or {}
        player_names = data.get("player_names")
        if player_names is not None and not isinstance(player_names, list):
            return jsonify({"error": "player_names must be a list"}), 400
        player_name = data.get("player_name")
        mute_value = data.get("mute")

        snapshot = get_device_registry_snapshot().active_clients
        target_pairs = _select_target_pairs(snapshot, player_names=player_names, player_name=player_name)
        if player_names is None and not player_name:
            target_pairs = target_pairs[:1]
        targets = [client for client, _device in target_pairs]
        target_snapshot_map = {id(client): device for client, device in target_pairs}

        # --- Direct pactl path (only path now) ---
        results = []
        loop = get_main_loop()
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
        muted = bool(results[0].get("muted", False)) if results else False
        return jsonify({"success": True, "muted": muted, "results": results})
    except Exception:
        logger.exception("Mute update failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@api_bp.route("/api/unmute_sink", methods=["POST"])
def unmute_sink():
    """Force-unmute the PulseAudio sink for a device, bypassing MA routing.

    Used as a recovery action when the PA sink is muted at system level
    (e.g. after a crash or restart) while the application-level mute is off.
    """
    try:
        data = request.get_json() or {}
        player_name = data.get("player_name")
        if not player_name:
            return jsonify({"error": "player_name is required"}), 400

        snapshot = get_device_registry_snapshot().active_clients
        target_pairs = _select_target_pairs(snapshot, player_name=player_name)
        if not target_pairs:
            return jsonify({"success": False, "error": "Device not found"}), 404

        client, _device = target_pairs[0]
        if not client.bluetooth_sink_name:
            return jsonify({"success": False, "error": "No audio sink configured"}), 400

        ok = set_sink_mute(client.bluetooth_sink_name, False)
        if not ok:
            return jsonify({"success": False, "error": "Failed to unmute sink"}), 500

        muted = get_sink_mute(client.bluetooth_sink_name)
        client._update_status({"sink_muted": bool(muted) if muted is not None else False})

        loop = get_main_loop()
        if loop:
            _submit_loop_coroutine(
                loop,
                client._send_subprocess_command({"cmd": "set_mute", "muted": False}),
                description=f"unmute_sink for {client.player_name}",
            )

        logger.info("Sink unmuted via recovery action for %s", player_name)
        return jsonify({"success": True, "sink_muted": False})
    except Exception:
        logger.exception("Unmute sink failed")
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
    if action not in ("pause", "play"):
        return jsonify({"success": False, "error": "Invalid action"}), 400
    loop = get_main_loop()
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
        ma_url, ma_token = get_ma_api_credentials()
        seen_ma_syncgroups: set = set()
        seen_session_groups: set = set()

        for client, device in snapshot_pairs:
            if not client.is_running():
                continue

            # Try MA syncgroup play first (preserves group sync)
            if ma_url and ma_token:
                ma_group = get_ma_group_for_player(getattr(client, "player_id", ""))
                if ma_group:
                    sid = ma_group["id"]
                    if sid not in seen_ma_syncgroups:
                        seen_ma_syncgroups.add(sid)
                        try:
                            from sendspin_bridge.services.music_assistant.ma_client import ma_group_play

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
    if action not in ("pause", "play"):
        return jsonify({"success": False, "error": "Invalid action"}), 400
    if not group_id:
        return jsonify({"success": False, "error": "group_id is required"}), 400

    loop = get_main_loop()
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
        ma_url, ma_token = get_ma_api_credentials()
        if ma_url and ma_token:
            ma_group = get_ma_group_for_player(getattr(target, "player_id", ""))
            if ma_group:
                try:
                    from sendspin_bridge.services.music_assistant.ma_client import ma_group_play

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
    if action not in ("pause", "play"):
        return jsonify({"success": False, "error": "Invalid action"}), 400
    snapshot = get_device_registry_snapshot().active_clients
    target = next((c for c in snapshot if getattr(c, "player_name", None) == player_name), None)
    if not target or not target.is_running():
        return jsonify({"success": False, "error": "Player not found or not running"}), 404
    loop = get_main_loop()
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
