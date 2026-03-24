"""MA playback routes and helpers.

Split from routes/api_ma.py — all /api/ma/queue/*, /api/ma/nowplaying,
and /api/ma/artwork routes and their supporting helpers live here.
"""

from __future__ import annotations

import logging
import threading
import urllib.error as _ue
import urllib.parse as _up
import urllib.request as _ur
import uuid

from flask import Response, jsonify, request

from routes.api_ma import _await_loop_result, ma_bp
from services.async_job_state import create_async_job, finish_async_job, get_async_job
from services.bridge_runtime_state import get_main_loop
from services.device_registry import get_device_registry_snapshot
from services.ma_artwork import has_valid_artwork_signature
from services.ma_monitor import solo_queue_candidates
from services.ma_runtime_state import (
    apply_ma_now_playing_prediction,
    fail_ma_pending_op,
    get_ma_api_credentials,
    get_ma_group_by_id,
    get_ma_group_for_player_id,
    get_ma_groups,
    get_ma_now_playing,
    is_ma_connected,
)
from services.status_snapshot import build_device_snapshot_pairs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_target_queue(
    syncgroup_id: str | None,
    player_id: str | None = None,
    group_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve (state_key, target_queue_id) from request context.

    ``state_key`` is the key used in our shared now-playing cache/UI snapshots.
    ``target_queue_id`` is the actual MA queue/player identifier that queue
    commands must target.
    """
    raw_syncgroup_id = str(syncgroup_id or "").strip()
    raw_player_id = str(player_id or "").strip()
    raw_group_id = str(group_id or "").strip()
    solo_queue_ids = solo_queue_candidates(raw_player_id)
    solo_queue_id = solo_queue_ids[0] if solo_queue_ids else ""

    if not raw_player_id:
        for candidate in (raw_syncgroup_id, raw_group_id):
            if candidate.startswith(("up", "media_player.", "ma_")):
                return candidate, candidate

        active_clients = []
        for client, device in build_device_snapshot_pairs(get_device_registry_snapshot().active_clients):
            pid = str(getattr(client, "player_id", "") or "").strip()
            if not pid:
                continue
            is_running = False
            try:
                is_running = bool(client.is_running())
            except Exception:
                is_running = False
            if not (is_running or device.server_connected):
                continue
            active_clients.append((pid, device))

        if len(active_clients) == 1:
            inferred_player_id, _status = active_clients[0]
            inferred_solo_queue_ids = solo_queue_candidates(inferred_player_id)
            inferred_solo_queue_id = inferred_solo_queue_ids[0] if inferred_solo_queue_ids else ""
            for candidate in (raw_syncgroup_id, raw_group_id):
                if candidate.startswith("syncgroup_"):
                    ma_group = get_ma_group_by_id(candidate)
                    members = {str(m.get("id", "")) for m in (ma_group or {}).get("members", [])}
                    if any(queue_id in members for queue_id in inferred_solo_queue_ids):
                        return candidate, candidate
            return inferred_player_id, inferred_solo_queue_id

    if raw_player_id:
        ma_group = get_ma_group_for_player_id(raw_player_id)
        if ma_group and ma_group.get("id"):
            resolved = ma_group["id"]
            return resolved, resolved

        for candidate in (raw_syncgroup_id, raw_group_id):
            if not candidate:
                continue
            if candidate.startswith(("up", "media_player.", "ma_")):
                return raw_player_id, candidate
            if candidate.startswith("syncgroup_"):
                ma_group = get_ma_group_by_id(candidate)
                members = {str(m.get("id", "")) for m in (ma_group or {}).get("members", [])}
                if any(queue_id in members for queue_id in solo_queue_ids):
                    return candidate, candidate

        if solo_queue_id:
            return raw_player_id, solo_queue_id

    for candidate in (raw_syncgroup_id, raw_group_id):
        if not candidate:
            continue
        ma_group = get_ma_group_by_id(candidate)
        if ma_group and ma_group.get("id"):
            resolved = ma_group["id"]
            return resolved, resolved
        if candidate.startswith("syncgroup_"):
            return candidate, candidate
        if candidate.startswith(("up", "media_player.", "ma_")):
            return (raw_player_id or candidate), candidate

    if player_id:
        return raw_player_id, raw_player_id

    groups = get_ma_groups()
    if not groups:
        return None, None
    first_group = groups[0] if isinstance(groups[0], dict) else {}
    first_id = first_group.get("id")
    return first_id, first_id


def _build_ma_prediction_patch(action: str, value) -> dict:
    """Build a small predicted state patch for fast UI feedback."""
    if action == "shuffle":
        return {"shuffle": bool(value)}
    if action == "repeat":
        return {"repeat": str(value or "off")}
    if action == "seek":
        try:
            return {"elapsed": int(value)}
        except (TypeError, ValueError):
            return {}
    return {}


def _resolve_ma_artwork_url(raw_url: str) -> tuple[str, bool]:
    """Resolve a raw artwork path/URL and report whether it targets the MA origin."""
    ma_url, _token = get_ma_api_credentials()
    if not ma_url:
        raise ValueError("MA API URL is not configured")

    trimmed = raw_url.strip()
    parsed_raw = _up.urlparse(trimmed)
    base_parsed = _up.urlparse(ma_url)
    if parsed_raw.scheme and parsed_raw.scheme.lower() not in ("http", "https"):
        raise ValueError("Unsupported artwork URL scheme")

    if not parsed_raw.scheme:
        base = ma_url.rstrip("/") + "/"
        return _up.urljoin(base, trimmed), True

    resolved = trimmed
    parsed = _up.urlparse(resolved)
    is_ma_origin = (parsed.scheme.lower(), parsed.netloc.lower()) == (
        base_parsed.scheme.lower(),
        base_parsed.netloc.lower(),
    )
    return resolved, is_ma_origin


def _run_ma_queue_cmd_job(
    job_id: str,
    loop,
    *,
    action: str,
    value,
    target_queue_id: str,
    target_player_id: str | None,
    state_key: str,
    op_id: str,
) -> None:
    """Execute an MA queue command in the background and store its result."""
    try:
        from services.ma_monitor import request_queue_refresh, send_queue_cmd

        result = _await_loop_result(
            loop,
            send_queue_cmd(action, value, target_queue_id, player_id=target_player_id),
            timeout=5.0,
            description=f"MA queue cmd {action}",
        )
        if not result or not result.get("accepted"):
            error = (result or {}).get("error") or "MA command was not accepted"
            predicted = fail_ma_pending_op(state_key or target_queue_id, op_id, error)
            finish_async_job(
                job_id,
                {
                    "success": False,
                    "error": error,
                    "error_code": "command_rejected",
                    "op_id": op_id,
                    "syncgroup_id": state_key,
                    "queue_id": target_queue_id,
                    "ma_now_playing": predicted,
                },
            )
            return

        accepted_queue_id = str(result.get("queue_id") or target_queue_id)
        predicted = apply_ma_now_playing_prediction(
            state_key,
            {},
            op_id=op_id,
            action=action,
            value=value,
            accepted_at=result.get("accepted_at"),
            ack_latency_ms=result.get("ack_latency_ms"),
        )
        _await_loop_result(
            loop,
            request_queue_refresh(accepted_queue_id),
            timeout=1.0,
            description=f"MA queue refresh {accepted_queue_id}",
        )
        finish_async_job(
            job_id,
            {
                "success": True,
                "op_id": op_id,
                "syncgroup_id": state_key,
                "queue_id": accepted_queue_id,
                "accepted": True,
                "accepted_at": result.get("accepted_at"),
                "ack_latency_ms": result.get("ack_latency_ms"),
                "confirmed": False,
                "pending": True,
                "ma_now_playing": predicted,
            },
        )
    except Exception as exc:
        predicted = fail_ma_pending_op(state_key or target_queue_id, op_id, str(exc))
        logger.exception("MA queue command '%s' failed", action)
        finish_async_job(
            job_id,
            {
                "success": False,
                "error": "Internal error",
                "error_code": "internal_error",
                "op_id": op_id,
                "syncgroup_id": state_key,
                "queue_id": target_queue_id,
                "ma_now_playing": predicted,
            },
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@ma_bp.route("/api/ma/nowplaying", methods=["GET"])
def api_ma_nowplaying():
    """Return current MA now-playing metadata.

    Returns {"connected": false} when MA integration is not active.
    Fields when connected: state, track, artist, album, image_url,
    elapsed, elapsed_updated_at, duration, shuffle, repeat,
    queue_index, queue_total, syncgroup_id, and optional prev_/next_ track metadata.
    """
    if not is_ma_connected():
        return jsonify({"connected": False})
    return jsonify(get_ma_now_playing())


@ma_bp.route("/api/ma/artwork", methods=["GET"])
def api_ma_artwork():
    """Proxy MA artwork through the bridge so the UI can use same-origin image URLs."""
    raw_url = (request.args.get("url") or "").strip()
    signature = (request.args.get("sig") or "").strip()
    if not raw_url:
        return Response("Missing artwork URL", status=400)
    if not has_valid_artwork_signature(raw_url, signature):
        return Response("Invalid artwork signature", status=400)

    try:
        artwork_url, is_ma_origin = _resolve_ma_artwork_url(raw_url)
    except ValueError as exc:
        return Response(str(exc), status=400)

    # HMAC signature (checked above) prevents arbitrary-URL SSRF.
    # Only attach MA bearer token when the URL targets the MA server itself.
    _ma_url, ma_token = get_ma_api_credentials()
    req = _ur.Request(artwork_url, headers={"Accept": "image/*"})
    if is_ma_origin and ma_token:
        req.add_header("Authorization", f"Bearer {ma_token}")

    _ARTWORK_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

    try:
        with _ur.urlopen(req, timeout=15) as resp:
            cl = resp.headers.get("Content-Length")
            if cl and int(cl) > _ARTWORK_MAX_BYTES:
                return Response("Artwork too large", status=413)
            body = resp.read(_ARTWORK_MAX_BYTES + 1)
            if len(body) > _ARTWORK_MAX_BYTES:
                return Response("Artwork too large", status=413)
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            return Response(body, content_type=content_type, headers={"Cache-Control": "private, max-age=60"})
    except _ue.HTTPError as exc:
        logger.warning("MA artwork proxy HTTP %s for %s", exc.code, artwork_url)
        return Response("Artwork unavailable", status=exc.code)
    except Exception:
        logger.exception("MA artwork proxy failed for %s", artwork_url)
        return Response("Artwork unavailable", status=502)


@ma_bp.route("/api/ma/queue/cmd", methods=["POST"])
def api_ma_queue_cmd():
    """Send a playback control command to the active MA syncgroup queue.

    Body: {"action": "next"|"previous"|"shuffle"|"repeat"|"seek", "value": ...}
    - shuffle: value=true|false
    - repeat: value="off"|"all"|"one"
    - seek: value=<seconds int>
    """
    if not is_ma_connected():
        return jsonify({"success": False, "error": "MA not connected", "error_code": "ma_unavailable"}), 503

    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    value = data.get("value")
    state_key, target_queue_id = _resolve_target_queue(
        data.get("syncgroup_id"),
        data.get("player_id"),
        data.get("group_id"),
    )
    raw_player_id = str(data.get("player_id") or "").strip()
    target_player_id = raw_player_id or (
        target_queue_id if target_queue_id and not str(target_queue_id).startswith("up") else None
    )

    if action not in ("next", "previous", "shuffle", "repeat", "seek"):
        return jsonify({"success": False, "error": f"Unknown action: {action}", "error_code": "unknown_action"}), 400

    if not state_key or not target_queue_id:
        return jsonify({"success": False, "error": "No MA queue available", "error_code": "queue_unavailable"}), 503

    loop = get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available", "error_code": "loop_unavailable"}), 503

    op_id = uuid.uuid4().hex
    try:
        from services.ma_monitor import get_monitor

        monitor = get_monitor()
        if monitor is None or not monitor.is_connected():
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "MA monitor unavailable",
                        "error_code": "monitor_unavailable",
                        "syncgroup_id": state_key,
                        "queue_id": target_queue_id,
                    }
                ),
                503,
            )

        predicted = apply_ma_now_playing_prediction(
            state_key,
            _build_ma_prediction_patch(action, value),
            op_id=op_id,
            action=action,
            value=value,
        )
        job_id = str(uuid.uuid4())
        create_async_job(job_id, "ma-queue-cmd")
        threading.Thread(
            target=_run_ma_queue_cmd_job,
            args=(job_id, loop),
            kwargs={
                "action": action,
                "value": value,
                "target_queue_id": target_queue_id,
                "target_player_id": target_player_id,
                "state_key": state_key,
                "op_id": op_id,
            },
            daemon=True,
            name=f"ma-queue-{job_id[:8]}",
        ).start()
        return jsonify(
            {
                "success": True,
                "job_id": job_id,
                "op_id": op_id,
                "syncgroup_id": state_key,
                "queue_id": target_queue_id,
                "accepted": False,
                "accepted_at": None,
                "ack_latency_ms": None,
                "confirmed": False,
                "pending": True,
                "ma_now_playing": predicted,
            }
        ), 202
    except Exception as exc:
        fail_ma_pending_op(state_key or target_queue_id or "", op_id, str(exc))
        logger.exception("MA queue command '%s' failed", action)
        return jsonify(
            {"success": False, "error": "Internal error", "error_code": "internal_error", "op_id": op_id}
        ), 500


@ma_bp.route("/api/ma/queue/cmd/result/<job_id>", methods=["GET"])
def api_ma_queue_cmd_result(job_id: str):
    """Poll for async MA queue command results."""
    job = get_async_job(job_id)
    if job is None or job.get("job_type") != "ma-queue-cmd":
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") == "running":
        return jsonify({"status": "running"})
    return jsonify(job)
