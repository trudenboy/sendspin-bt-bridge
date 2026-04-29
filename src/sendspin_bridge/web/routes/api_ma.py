"""
Music Assistant API Blueprint for sendspin-bt-bridge.

All /api/ma/* and /api/debug/ma routes and the helper functions they depend on.
Sub-modules (ma_auth, ma_playback, ma_groups) register their routes on this
blueprint via bottom-of-file imports.
"""

from __future__ import annotations

import asyncio
import logging

from flask import Blueprint

from sendspin_bridge.config import load_config
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot_pairs
from sendspin_bridge.services.music_assistant.ma_runtime_state import is_ma_connected

logger = logging.getLogger(__name__)

ma_bp = Blueprint("api_ma", __name__)


# ---------------------------------------------------------------------------
# Shared helpers (used by multiple sub-modules)
# ---------------------------------------------------------------------------


def _ma_host_from_sendspin_clients():
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


def _bridge_players_snapshot() -> list[dict[str, str]]:
    """Return active bridge players in MA discovery payload shape."""
    return [
        {
            "player_id": str(getattr(client, "player_id", "") or ""),
            "player_name": str(getattr(client, "player_name", "") or ""),
        }
        for client in get_device_registry_snapshot().active_clients
        if getattr(client, "player_id", None)
    ]


def _debug_clients_snapshot() -> list[dict[str, str | None]]:
    """Return active client info for MA debugging surfaces."""
    return [
        {
            "player_name": getattr(client, "player_name", None),
            "player_id": getattr(client, "player_id", None),
            "group_id": device.extra.get("group_id"),
        }
        for client, device in build_device_snapshot_pairs(get_device_registry_snapshot().active_clients)
    ]


def _await_loop_result(loop, coro, *, timeout: float, description: str):
    """Run a coroutine on the main loop and wait in a background thread."""
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    except Exception:
        logger.debug("%s failed", description, exc_info=True)
        return None


def _build_ma_integration_summary(discovered_url: str = "") -> dict[str, object]:
    """Return current bridge-side MA auth state for UI bootstrapping."""
    cfg = load_config()
    configured_url = str(cfg.get("MA_API_URL") or "").strip().rstrip("/")
    configured_token = str(cfg.get("MA_API_TOKEN") or "").strip()
    discovered_url = str(discovered_url or "").strip().rstrip("/")
    connected = is_ma_connected()
    token_valid = False
    if configured_url and configured_token:
        from sendspin_bridge.web.routes.ma_auth import _validate_ma_token

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


# ---------------------------------------------------------------------------
# Register sub-module routes on this blueprint.
# Imported at module bottom to avoid circular dependencies.
# ---------------------------------------------------------------------------
import sendspin_bridge.web.routes.ma_auth  # noqa: E402
import sendspin_bridge.web.routes.ma_groups  # noqa: E402
import sendspin_bridge.web.routes.ma_playback  # noqa: E402, F401

# Re-export moved functions for backward compatibility with tests and
# any external code that imports them from routes.api_ma.
# Uses __getattr__ to avoid circular import issues at module load time.
_REEXPORTS: dict[str, str] = {
    "_validate_ma_token": "sendspin_bridge.web.routes.ma_auth",
    "_exchange_for_long_lived_token": "sendspin_bridge.web.routes.ma_auth",
    "_create_ha_ingress_session_via_ws": "sendspin_bridge.web.routes.ma_auth",
    "_create_ma_token_via_ha_proxy": "sendspin_bridge.web.routes.ma_auth",
    "_create_ma_token_via_ingress": "sendspin_bridge.web.routes.ma_auth",
    "_get_ha_supervisor_addon_info_via_ws": "sendspin_bridge.web.routes.ma_auth",
    "_get_ha_user_via_ws": "sendspin_bridge.web.routes.ma_auth",
    "_get_ma_oauth_params": "sendspin_bridge.web.routes.ma_auth",
    "_get_ma_oauth_bootstrap": "sendspin_bridge.web.routes.ma_auth",
    "_ma_reports_homeassistant_addon": "sendspin_bridge.web.routes.ma_auth",
    "_save_ma_token_and_rediscover": "sendspin_bridge.web.routes.ma_auth",
    "_resolve_target_queue": "sendspin_bridge.web.routes.ma_playback",
    "_build_ma_prediction_patch": "sendspin_bridge.web.routes.ma_playback",
    "_schedule_ma_rediscover_job": "sendspin_bridge.web.routes.ma_groups",
    "_run_ma_discover_job": "sendspin_bridge.web.routes.ma_groups",
    "_run_ma_rediscover_job": "sendspin_bridge.web.routes.ma_groups",
}


def __getattr__(name: str):
    if name in _REEXPORTS:
        import importlib

        mod = importlib.import_module(_REEXPORTS[name])
        val = getattr(mod, name)
        globals()[name] = val  # cache for subsequent accesses
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
