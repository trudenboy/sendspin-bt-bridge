"""Helpers shared by the Music Assistant use cases."""

from __future__ import annotations

import asyncio
import logging

from sendspin_bridge.config import load_config
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot_pairs
from sendspin_bridge.services.music_assistant.ma_runtime_state import is_ma_connected

logger = logging.getLogger(__name__)


def ma_host_from_sendspin_clients():
    """Extract MA server host from connected sendspin clients.

    Checks server_host first (explicit config), then falls back to the
    resolved address from the live sendspin WebSocket connection.
    Returns host string or None.
    """
    snapshot = get_device_registry_snapshot().active_clients
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


def bridge_players_snapshot() -> list[dict[str, str]]:
    """Return active bridge players in MA discovery payload shape."""
    return [
        {
            "player_id": str(getattr(client, "player_id", "") or ""),
            "player_name": str(getattr(client, "player_name", "") or ""),
        }
        for client in get_device_registry_snapshot().active_clients
        if getattr(client, "player_id", None)
    ]


def debug_clients_snapshot() -> list[dict[str, str | None]]:
    """Return active client info for MA debugging surfaces."""
    return [
        {
            "player_name": getattr(client, "player_name", None),
            "player_id": getattr(client, "player_id", None),
            "group_id": device.extra.get("group_id"),
        }
        for client, device in build_device_snapshot_pairs(get_device_registry_snapshot().active_clients)
    ]


def await_loop_result(loop, coro, *, timeout: float, description: str):
    """Run a coroutine on the main loop and wait in a background thread."""
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    except Exception:
        logger.debug("%s failed", description, exc_info=True)
        return None


def build_ma_integration_summary(discovered_url: str = "") -> dict[str, object]:
    """Return current bridge-side MA auth state for UI bootstrapping."""
    cfg = load_config()
    configured_url = str(cfg.get("MA_API_URL") or "").strip().rstrip("/")
    configured_token = str(cfg.get("MA_API_TOKEN") or "").strip()
    discovered_url = str(discovered_url or "").strip().rstrip("/")
    connected = is_ma_connected()
    token_valid = False
    if configured_url and configured_token:
        from sendspin_bridge.application.music_assistant.auth import validate_ma_token as _validate_ma_token

        token_valid = _validate_ma_token(configured_url, configured_token)
    return {
        "configured": bool(configured_url and configured_token),
        "configured_url": configured_url,
        "url_configured": bool(configured_url),
        "token_configured": bool(configured_token),
        "token_valid": token_valid,
        "connected": connected,
        "matches_discovered_server": bool(discovered_url and configured_url and discovered_url == configured_url),
        "username": str(cfg.get("MA_USERNAME") or "").strip(),
        "auth_provider": str(cfg.get("MA_AUTH_PROVIDER") or "").strip(),
    }
