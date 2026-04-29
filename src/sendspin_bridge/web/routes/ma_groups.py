"""MA discovery and groups routes and helpers.

Split from routes/api_ma.py — all /api/ma/discover*, /api/ma/groups,
/api/ma/rediscover*, /api/ma/reload, and /api/debug/ma routes live here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid

from flask import jsonify

from sendspin_bridge.config import load_config
from sendspin_bridge.services.ha.ha_addon import get_ma_addon_discovery_candidates
from sendspin_bridge.services.lifecycle.async_job_state import create_async_job, finish_async_job, get_async_job
from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop
from sendspin_bridge.services.music_assistant.ma_monitor import reload_monitor_credentials
from sendspin_bridge.services.music_assistant.ma_runtime_state import (
    get_ma_api_credentials,
    get_ma_groups,
    get_ma_now_playing_cache_snapshot,
    set_ma_api_credentials,
    set_ma_groups,
)
from sendspin_bridge.web.routes.api_config import _detect_runtime
from sendspin_bridge.web.routes.api_ma import (
    _await_loop_result,
    _bridge_players_snapshot,
    _build_ma_integration_summary,
    _debug_clients_snapshot,
    _ma_host_from_sendspin_clients,
    ma_bp,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schedule_ma_rediscover_job(loop, ma_url: str, ma_token: str) -> str:
    """Start async MA group rediscovery using the current bridge player snapshot."""
    job_id = str(uuid.uuid4())
    create_async_job(job_id, "ma-rediscover")
    threading.Thread(
        target=_run_ma_rediscover_job,
        args=(job_id, loop, ma_url, ma_token, _bridge_players_snapshot()),
        daemon=True,
        name=f"ma-rediscover-{job_id[:8]}",
    ).start()
    return job_id


def _run_ma_discover_job(job_id: str, loop, is_addon: bool) -> None:
    """Resolve Music Assistant discovery in a background thread and store the result."""
    from sendspin_bridge.services.music_assistant.ma_discovery import discover_ma_servers, validate_ma_url

    def _annotate_server(server: dict[str, object] | None, *, source: str, summary: str) -> dict[str, object] | None:
        if not isinstance(server, dict):
            return None
        annotated = dict(server)
        annotated["discovery_source"] = source
        annotated["discovery_summary"] = summary
        return annotated

    def _finish_success(servers: list[dict]) -> None:
        discovered_url = ""
        if servers and isinstance(servers[0], dict):
            discovered_url = str(servers[0].get("url") or "")
        finish_async_job(
            job_id,
            {
                "success": True,
                "is_addon": is_addon,
                "servers": servers,
                "integration": _build_ma_integration_summary(discovered_url),
            },
        )

    addon_candidates = get_ma_addon_discovery_candidates() if is_addon else []
    for candidate in addon_candidates:
        candidate_url = str(candidate.get("url") or "").strip()
        if not candidate_url:
            continue
        info = _await_loop_result(
            loop, validate_ma_url(candidate_url), timeout=5.0, description=f"validate {candidate_url}"
        )
        if info:
            _finish_success(
                [
                    _annotate_server(
                        info,
                        source=str(candidate.get("source") or "ha_addon_candidate"),
                        summary=str(
                            candidate.get("summary") or "Music Assistant candidate discovered from Home Assistant."
                        ),
                    )
                    or info
                ]
            )
            return

    ma_url, _ = get_ma_api_credentials()
    if ma_url:
        info = _await_loop_result(loop, validate_ma_url(ma_url), timeout=5.0, description=f"validate {ma_url}")
        if info:
            _finish_success(
                [
                    _annotate_server(
                        info,
                        source="saved_config",
                        summary="Music Assistant was loaded from the saved bridge configuration.",
                    )
                    or info
                ]
            )
            return

    if is_addon:
        _finish_success([])
        return

    cfg = load_config()
    sendspin_host = (cfg.get("SENDSPIN_SERVER") or "").strip()
    if sendspin_host and sendspin_host.lower() not in ("auto", "discover", ""):
        candidate_url = f"http://{sendspin_host}:8095"
        info = _await_loop_result(
            loop, validate_ma_url(candidate_url), timeout=5.0, description=f"validate {candidate_url}"
        )
        if info:
            _finish_success(
                [
                    _annotate_server(
                        info,
                        source="sendspin_server_host",
                        summary="Music Assistant was inferred from the configured Sendspin server host.",
                    )
                    or info
                ]
            )
            return

    sendspin_ma_host = _ma_host_from_sendspin_clients()
    if sendspin_ma_host:
        candidate_url = f"http://{sendspin_ma_host}:8095"
        info = _await_loop_result(
            loop, validate_ma_url(candidate_url), timeout=5.0, description=f"validate {candidate_url}"
        )
        if info:
            _finish_success(
                [
                    _annotate_server(
                        info,
                        source="connected_runtime_host",
                        summary="Music Assistant was inferred from the current runtime connection host.",
                    )
                    or info
                ]
            )
            return

    try:
        servers = _await_loop_result(loop, discover_ma_servers(timeout=5.0), timeout=10.0, description="mDNS discover")
        if servers is None:
            raise RuntimeError("Discovery failed")
        servers = [
            _annotate_server(
                server,
                source="mdns",
                summary="Music Assistant was discovered via mDNS on the local network.",
            )
            or server
            for server in servers
        ]
        _finish_success(servers)
    except Exception:
        logger.exception("MA mDNS discovery failed")
        finish_async_job(job_id, {"success": False, "is_addon": is_addon, "error": "Discovery failed"})


def _run_ma_rediscover_job(job_id: str, loop, ma_url: str, ma_token: str, player_info: list[dict[str, str]]) -> None:
    """Refresh MA groups in a background thread and store the result."""
    try:
        from sendspin_bridge.services.music_assistant.ma_client import discover_ma_groups

        result = _await_loop_result(
            loop,
            discover_ma_groups(ma_url, ma_token, player_info),
            timeout=15.0,
            description="MA rediscover",
        )
        if result is None:
            raise RuntimeError("MA rediscover failed")
        name_map, all_groups = result
        set_ma_api_credentials(ma_url, ma_token)
        set_ma_groups(name_map, all_groups)
        finish_async_job(
            job_id,
            {
                "success": True,
                "syncgroups": len(all_groups),
                "mapped_players": len(name_map),
                "groups": [{"id": g["id"], "name": g["name"]} for g in all_groups],
            },
        )
    except Exception:
        logger.exception("MA rediscover failed")
        finish_async_job(job_id, {"success": False, "error": "Internal error"})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@ma_bp.route("/api/ma/discover", methods=["GET"])
def api_ma_discover():
    """Discover Music Assistant servers.

    HA addon mode: 1) homeassistant.local:8095 (Supervisor DNS), 2) saved MA_API_URL.
    Other modes: 1) saved MA_API_URL, 2) SENDSPIN_SERVER host, 3) sendspin
    client connection host, 4) mDNS scan.

    Always returns ``is_addon`` flag so frontend can adjust UI.
    """
    is_addon = _detect_runtime() == "ha_addon"
    loop = get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    job_id = str(uuid.uuid4())
    create_async_job(job_id, "ma-discover")
    threading.Thread(
        target=_run_ma_discover_job,
        args=(job_id, loop, is_addon),
        daemon=True,
        name=f"ma-discover-{job_id[:8]}",
    ).start()
    return jsonify({"job_id": job_id, "status": "running", "is_addon": is_addon}), 202


@ma_bp.route("/api/ma/discover/result/<job_id>", methods=["GET"])
def api_ma_discover_result(job_id: str):
    """Poll for async Music Assistant discovery results."""
    job = get_async_job(job_id)
    if job is None or job.get("job_type") != "ma-discover":
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") == "running":
        return jsonify({"status": "running", "is_addon": job.get("is_addon", False)})
    return jsonify(job)


@ma_bp.route("/api/ma/groups", methods=["GET"])
def api_ma_groups():
    """Return all MA syncgroup players discovered from the MA API.

    Each group includes id, name, and members with id/name/state/volume/available.
    Returns empty list if MA API is not configured or discovery has not run yet.
    """
    return jsonify(get_ma_groups())


@ma_bp.route("/api/ma/rediscover", methods=["POST"])
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

    loop = get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    job_id = _schedule_ma_rediscover_job(loop, ma_url, ma_token)
    return jsonify({"success": True, "job_id": job_id, "status": "running"}), 202


@ma_bp.route("/api/ma/rediscover/result/<job_id>", methods=["GET"])
def api_ma_rediscover_result(job_id: str):
    """Poll for async MA rediscover results."""
    job = get_async_job(job_id)
    if job is None or job.get("job_type") != "ma-rediscover":
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") == "running":
        return jsonify({"status": "running"})
    return jsonify(job)


@ma_bp.route("/api/ma/reload", methods=["POST"])
def api_ma_reload():
    """Reload MA runtime pieces without restarting the full bridge service."""
    cfg = load_config()
    ma_url = str(cfg.get("MA_API_URL") or "").strip()
    ma_token = str(cfg.get("MA_API_TOKEN") or "").strip()
    if not ma_url or not ma_token:
        return jsonify({"success": False, "error": "MA_API_URL or MA_API_TOKEN not configured"}), 400

    loop = get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    set_ma_api_credentials(ma_url, ma_token)
    monitor_reloaded = reload_monitor_credentials(loop, ma_url, ma_token)
    job_id = _schedule_ma_rediscover_job(loop, ma_url, ma_token)
    return (
        jsonify(
            {
                "success": True,
                "job_id": job_id,
                "status": "running",
                "monitor_reloaded": monitor_reloaded,
            }
        ),
        202,
    )


@ma_bp.route("/api/debug/ma")
def api_debug_ma():
    """Debug: dump MA now-playing cache, groups, per-client player_ids, and live queues."""
    cache = get_ma_now_playing_cache_snapshot()
    groups = get_ma_groups()
    clients_info = _debug_clients_snapshot()

    # Fetch live queue ids from MA WebSocket
    ma_url, ma_token = get_ma_api_credentials()
    live_queue_ids: list[str] = []
    if ma_url and ma_token:
        try:
            import websockets

            try:
                from websockets.asyncio.client import connect as _ws_connect
            except ImportError:
                _ws_connect = websockets.connect  # type: ignore[attr-defined]

            _ws_kw: dict = {"proxy": None} if int(websockets.__version__.split(".")[0]) >= 15 else {}

            async def _fetch():
                ws_url = ma_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
                async with _ws_connect(
                    ws_url, additional_headers={"Authorization": f"Bearer {ma_token}"}, **_ws_kw
                ) as ws:
                    await ws.send(json.dumps({"command": "player_queues/all", "args": {}, "message_id": 99}))
                    for _ in range(10):
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if str(msg.get("message_id")) == "99":
                            return [q.get("queue_id", "") for q in (msg.get("result") or [])]
                return []

            loop = get_main_loop()
            if loop:
                fut = asyncio.run_coroutine_threadsafe(_fetch(), loop)
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
