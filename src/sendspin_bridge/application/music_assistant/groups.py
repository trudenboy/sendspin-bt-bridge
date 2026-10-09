"""Finding Music Assistant, and its sync groups."""

from __future__ import annotations

import asyncio
import json
import logging

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.jobs import Job, JobContext, jobs
from sendspin_bridge.application.music_assistant.common import (
    await_loop_result,
    bridge_players_snapshot,
    build_ma_integration_summary,
    debug_clients_snapshot,
    ma_host_from_sendspin_clients,
)
from sendspin_bridge.application.runtime import bridge_loop
from sendspin_bridge.config import load_config
from sendspin_bridge.services.ha.ha_addon import get_ma_addon_discovery_candidates
from sendspin_bridge.services.music_assistant.ma_monitor import reload_monitor_credentials
from sendspin_bridge.services.music_assistant.ma_runtime_state import (
    get_ma_api_credentials,
    get_ma_groups,
    get_ma_now_playing_cache_snapshot,
    set_ma_api_credentials,
    set_ma_groups,
)

logger = logging.getLogger(__name__)


def _is_addon() -> bool:
    from sendspin_bridge.application.config import _detect_runtime

    return _detect_runtime() == "ha_addon"


def _annotate(server: dict | None, *, source: str, summary: str) -> dict | None:
    if not isinstance(server, dict):
        return None
    return {**server, "discovery_source": source, "discovery_summary": summary}


def _discover(loop, is_addon: bool) -> dict:
    """Candidates in order: HA add-on, saved URL, SENDSPIN_SERVER, the live Sendspin peer, then mDNS."""
    from sendspin_bridge.services.music_assistant.ma_discovery import discover_ma_servers, validate_ma_url

    def _validate(url: str):
        return await_loop_result(loop, validate_ma_url(url), timeout=5.0, description=f"validate {url}")

    def _done(servers: list[dict]) -> dict:
        discovered_url = str(servers[0].get("url") or "") if servers and isinstance(servers[0], dict) else ""
        return {"is_addon": is_addon, "servers": servers, "integration": build_ma_integration_summary(discovered_url)}

    for candidate in get_ma_addon_discovery_candidates() if is_addon else []:
        url = str(candidate.get("url") or "").strip()
        info = _validate(url) if url else None
        if info:
            source = str(candidate.get("source") or "ha_addon_candidate")
            summary = str(candidate.get("summary") or "Music Assistant candidate discovered from Home Assistant.")
            return _done([_annotate(info, source=source, summary=summary) or info])

    ma_url, _ = get_ma_api_credentials()
    if ma_url:
        info = _validate(ma_url)
        if info:
            return _done(
                [
                    _annotate(
                        info,
                        source="saved_config",
                        summary="Music Assistant was loaded from the saved bridge configuration.",
                    )
                    or info
                ]
            )
    if is_addon:
        return _done([])

    host = (load_config().get("SENDSPIN_SERVER") or "").strip()
    if host and host.lower() not in ("auto", "discover", ""):
        info = _validate(f"http://{host}:8095")
        if info:
            return _done(
                [
                    _annotate(
                        info,
                        source="sendspin_server_host",
                        summary="Music Assistant was inferred from the configured Sendspin server host.",
                    )
                    or info
                ]
            )
    peer = ma_host_from_sendspin_clients()
    if peer:
        info = _validate(f"http://{peer}:8095")
        if info:
            return _done(
                [
                    _annotate(
                        info,
                        source="connected_runtime_host",
                        summary="Music Assistant was inferred from the current runtime connection host.",
                    )
                    or info
                ]
            )
    servers = await_loop_result(loop, discover_ma_servers(timeout=5.0), timeout=10.0, description="mDNS discover")
    if servers is None:
        raise UseCaseError(502, "discovery_failed", "Discovery failed")
    return _done(
        [
            _annotate(s, source="mdns", summary="Music Assistant was discovered via mDNS on the local network.") or s
            for s in servers
        ]
    )


def start_discovery() -> Job:
    """Find Music Assistant servers this bridge can use."""
    loop = bridge_loop()
    is_addon = _is_addon()
    return jobs.start(
        "music_assistant.discovery", lambda _ctx: _discover(loop, is_addon), progress={"is_addon": is_addon}
    )


def _configured_credentials() -> tuple[str, str]:
    cfg = load_config()
    ma_url = str(cfg.get("MA_API_URL") or "").strip()
    ma_token = str(cfg.get("MA_API_TOKEN") or "").strip()
    if not ma_url or not ma_token:
        raise UseCaseError(409, "ma_not_configured", "MA_API_URL or MA_API_TOKEN not configured")
    return ma_url, ma_token


def start_group_refresh() -> Job:
    """Re-read Music Assistant's sync groups and which bridge players belong to them."""
    loop = bridge_loop()
    ma_url, ma_token = _configured_credentials()
    players = bridge_players_snapshot()

    def _work(_ctx: JobContext):
        from sendspin_bridge.services.music_assistant.ma_client import discover_ma_groups

        result = await_loop_result(
            loop, discover_ma_groups(ma_url, ma_token, players), timeout=15.0, description="MA rediscover"
        )
        if result is None:
            raise UseCaseError(502, "ma_unreachable", "MA rediscover failed")
        name_map, all_groups = result
        set_ma_api_credentials(ma_url, ma_token)
        set_ma_groups(name_map, all_groups)
        return {
            "syncgroups": len(all_groups),
            "mapped_players": len(name_map),
            "groups": [{"id": g["id"], "name": g["name"]} for g in all_groups],
        }

    return jobs.start("music_assistant.groups", _work)


def reload() -> dict:
    """Reconnect the Music Assistant monitor with the saved credentials and refresh groups."""
    loop = bridge_loop()
    ma_url, ma_token = _configured_credentials()
    set_ma_api_credentials(ma_url, ma_token)
    monitor_reloaded = reload_monitor_credentials(loop, ma_url, ma_token)
    job = start_group_refresh()
    return {"monitor_reloaded": monitor_reloaded, "job": job}


def groups() -> list[dict]:
    """Music Assistant sync groups with their members (empty before discovery)."""
    return get_ma_groups()


def debug_snapshot() -> dict:
    """Now-playing cache, groups, per-client ids and Music Assistant's live queue ids."""
    ma_url, ma_token = get_ma_api_credentials()
    live_queue_ids: list[str] = []
    if ma_url and ma_token:
        try:
            from websockets.asyncio.client import connect as _ws_connect

            async def _fetch():
                ws_url = ma_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
                async with _ws_connect(
                    ws_url, additional_headers={"Authorization": f"Bearer {ma_token}"}, proxy=None
                ) as ws:
                    await ws.send(json.dumps({"command": "player_queues/all", "args": {}, "message_id": 99}))
                    for _ in range(10):
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if str(msg.get("message_id")) == "99":
                            return [q.get("queue_id", "") for q in (msg.get("result") or [])]
                return []

            live_queue_ids = await_loop_result(bridge_loop(), _fetch(), timeout=10, description="MA queues") or []
        except Exception as exc:
            live_queue_ids = [f"error: {exc}"]
    return {
        "cache_keys": list(get_ma_now_playing_cache_snapshot().keys()),
        "groups": get_ma_groups(),
        "clients": debug_clients_snapshot(),
        "live_queue_ids": live_queue_ids,
    }
