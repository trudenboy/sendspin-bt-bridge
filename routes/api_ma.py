"""
Music Assistant API Blueprint for sendspin-bt-bridge.

All /api/ma/* and /api/debug/ma routes and the helper functions they depend on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import threading
import urllib.error as _ue
import urllib.parse as _up
import urllib.request as _ur
import uuid

from flask import Blueprint, Response, jsonify, request

import state
from config import (
    load_config,
    update_config,
)
from routes.api_config import _detect_runtime
from services.device_registry import get_device_registry_snapshot
from services.ha_addon import get_ma_addon_internal_ingress_url
from services.ma_artwork import has_valid_artwork_signature
from services.ma_monitor import solo_queue_candidates

logger = logging.getLogger(__name__)

ma_bp = Blueprint("api_ma", __name__)


# ---------------------------------------------------------------------------
# Helpers (only used by MA routes)
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
            "group_id": client.status.get("group_id") if hasattr(client, "status") else None,
        }
        for client in get_device_registry_snapshot().active_clients
    ]


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
        for client in get_device_registry_snapshot().active_clients:
            pid = str(getattr(client, "player_id", "") or "").strip()
            if not pid:
                continue
            status = getattr(client, "status", {}) or {}
            is_running = False
            try:
                is_running = bool(client.is_running())
            except Exception:
                is_running = False
            if not (is_running or status.get("server_connected")):
                continue
            active_clients.append((pid, status))

        if len(active_clients) == 1:
            inferred_player_id, _status = active_clients[0]
            inferred_solo_queue_ids = solo_queue_candidates(inferred_player_id)
            inferred_solo_queue_id = inferred_solo_queue_ids[0] if inferred_solo_queue_ids else ""
            for candidate in (raw_syncgroup_id, raw_group_id):
                if candidate.startswith("syncgroup_"):
                    ma_group = state.get_ma_group_by_id(candidate)
                    members = {str(m.get("id", "")) for m in (ma_group or {}).get("members", [])}
                    if any(queue_id in members for queue_id in inferred_solo_queue_ids):
                        return candidate, candidate
            return inferred_player_id, inferred_solo_queue_id

    if raw_player_id:
        ma_group = state.get_ma_group_for_player_id(raw_player_id)
        if ma_group and ma_group.get("id"):
            resolved = ma_group["id"]
            return resolved, resolved

        for candidate in (raw_syncgroup_id, raw_group_id):
            if not candidate:
                continue
            if candidate.startswith(("up", "media_player.", "ma_")):
                return raw_player_id, candidate
            if candidate.startswith("syncgroup_"):
                ma_group = state.get_ma_group_by_id(candidate)
                members = {str(m.get("id", "")) for m in (ma_group or {}).get("members", [])}
                if any(queue_id in members for queue_id in solo_queue_ids):
                    return candidate, candidate

        if solo_queue_id:
            return raw_player_id, solo_queue_id

    for candidate in (raw_syncgroup_id, raw_group_id):
        if not candidate:
            continue
        ma_group = state.get_ma_group_by_id(candidate)
        if ma_group and ma_group.get("id"):
            resolved = ma_group["id"]
            return resolved, resolved
        if candidate.startswith("syncgroup_"):
            return candidate, candidate
        if candidate.startswith(("up", "media_player.", "ma_")):
            return (raw_player_id or candidate), candidate

    if player_id:
        return raw_player_id, raw_player_id

    groups = state.get_ma_groups()
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


_MA_TOKEN_NAME = "Sendspin BT Bridge"


def _ws_connect(url: str, **kwargs):
    """Wrapper around websockets.sync.client.connect that handles proxy kwarg compatibility.

    Older websockets versions (<14) don't support the ``proxy`` parameter.
    """
    from websockets.sync.client import connect as ws_connect

    try:
        return ws_connect(url, proxy=None, **kwargs)
    except TypeError:
        return ws_connect(url, **kwargs)


def _resolve_ma_artwork_url(raw_url: str) -> tuple[str, bool]:
    """Resolve a raw artwork path/URL and report whether it targets the MA origin."""
    ma_url, _token = state.get_ma_api_credentials()
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


def _validate_ma_token(ma_url: str, token: str) -> bool:
    """Quick WS auth check — returns True if the token authenticates with MA."""
    try:
        from websockets.sync.client import connect as ws_connect  # noqa: F401 — availability check
    except ImportError:
        return False
    ws_url = ma_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    try:
        with _ws_connect(ws_url, close_timeout=5) as ws:
            ws.recv(timeout=5)  # server_info
            ws.send(json.dumps({"command": "auth", "args": {"token": token}, "message_id": 1}))
            resp = json.loads(ws.recv(timeout=5))
            return bool(resp.get("result", {}).get("authenticated"))
    except Exception as exc:
        logger.debug("MA token validation failed: %s", exc)
        return False


def _build_ma_integration_summary(discovered_url: str = "") -> dict[str, object]:
    """Return current bridge-side MA auth state for UI bootstrapping."""
    cfg = load_config()
    configured_url = str(cfg.get("MA_API_URL") or "").strip().rstrip("/")
    configured_token = str(cfg.get("MA_API_TOKEN") or "").strip()
    discovered_url = str(discovered_url or "").strip().rstrip("/")
    connected = state.is_ma_connected()
    token_valid = False
    if configured_url and configured_token:
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


def _exchange_for_long_lived_token(ma_url: str, session_token: str) -> str:
    """Exchange a short-lived MA session token for a long-lived API token.

    Connects to MA WS, authenticates, calls auth/token/create.
    Returns the long-lived token on success, or the original session_token as fallback.
    """
    try:
        from websockets.sync.client import connect as ws_connect  # noqa: F401 — availability check
    except ImportError:
        logger.warning("websockets.sync.client not available — using session token as-is")
        return session_token
    ws_url = ma_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    try:
        with _ws_connect(ws_url, close_timeout=5) as ws:
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


def _ma_http_login(ma_url: str, username: str, password: str) -> str:
    """Login to MA via HTTP and return a session token.

    Supports both stable MA (flat body) and beta 2.8+ (nested credentials).
    Raises RuntimeError on auth failure, ConnectionError on network issues.
    """
    login_url = ma_url.rstrip("/") + "/auth/login"

    def _post(body: dict) -> dict:
        data = json.dumps(body).encode()
        req = _ur.Request(login_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with _ur.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    # 1) Try stable format: {"username": ..., "password": ...}
    try:
        result = _post({"username": username, "password": password})
        if result.get("success"):
            token = result.get("access_token") or result.get("token")
            if token:
                logger.debug("MA login succeeded with stable (flat) format")
                return token
    except _ur.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        # 401 with "Invalid username or password" = real auth error (stable MA)
        if exc.code == 401:
            try:
                err_data = json.loads(body)
                err_msg = err_data.get("error", body)
            except (json.JSONDecodeError, ValueError):
                err_msg = body
            if "invalid" in err_msg.lower():
                raise RuntimeError(err_msg) from exc
            # Otherwise format may be wrong — fall through to new format
        elif exc.code >= 500:
            raise ConnectionError(f"MA server error: {exc.code}") from exc
    except Exception as exc:
        if not isinstance(exc, RuntimeError | ConnectionError):
            logger.debug("Stable-format login failed: %s", exc)

    # 2) Try beta format: {"credentials": {...}, "provider_id": "builtin"}
    try:
        result = _post(
            {
                "provider_id": "builtin",
                "credentials": {"username": username, "password": password},
            }
        )
        if result.get("success"):
            token = result.get("token") or result.get("access_token")
            if token:
                logger.debug("MA login succeeded with beta (nested credentials) format")
                return token
        err = result.get("error", "Login failed")
        raise RuntimeError(err)
    except _ur.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            err_data = json.loads(body)
            err_msg = err_data.get("error", body)
        except (json.JSONDecodeError, ValueError):
            err_msg = body
        raise RuntimeError(err_msg) from exc


async def _rediscover_after_login(
    ma_url: str,
    ma_token: str,
    bridge_players: list[dict],
) -> None:
    """Background task: rediscover MA groups after successful login."""
    try:
        from services.ma_client import discover_ma_groups

        id_map, all_groups = await discover_ma_groups(ma_url, ma_token, bridge_players)
        state.set_ma_groups(id_map, all_groups)
        logger.info("MA groups rediscovered after login: %d groups", len(all_groups))
    except Exception:
        logger.debug("MA group rediscovery after login failed", exc_info=True)


def _save_ma_token_and_rediscover(ma_url: str, ma_token: str, username: str = "", auth_provider: str = "") -> None:
    """Save MA token to config and trigger group rediscovery."""

    def _save(cfg: dict) -> None:
        cfg["MA_API_URL"] = ma_url
        cfg["MA_API_TOKEN"] = ma_token
        if username:
            cfg["MA_USERNAME"] = username
        if auth_provider:
            cfg["MA_AUTH_PROVIDER"] = auth_provider

    update_config(_save)
    state.set_ma_api_credentials(ma_url, ma_token)

    loop = state.get_main_loop()
    if loop:
        try:
            asyncio.run_coroutine_threadsafe(
                _rediscover_after_login(ma_url, ma_token, _bridge_players_snapshot()), loop
            )
        except Exception:
            pass


# ── MA ↔ HA OAuth helpers (shared by ha-login and ha-silent-auth) ─────────


def _get_ma_oauth_params(ma_url: str):
    """Resolve HA OAuth parameters from Music Assistant auth endpoints.

    Supports legacy JSON responses plus newer redirect-based `/auth/authorize`
    and JSON-RPC `auth/authorization_url` response shapes used by recent MA
    stable/beta builds.
    """

    def _parse_auth_url(auth_url: str):
        parsed = _up.urlparse(auth_url)
        params = _up.parse_qs(parsed.query)
        ha_base = f"{parsed.scheme}://{parsed.netloc}"
        client_id = params.get("client_id", [""])[0]
        redirect_uri = params.get("redirect_uri", [""])[0]
        oauth_state = params.get("state", [""])[0]
        return ha_base, client_id, redirect_uri, oauth_state

    def _extract_auth_url(payload: object) -> str:
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, dict):
            direct = payload.get("authorization_url")
            if isinstance(direct, str) and direct.strip():
                return direct.strip()
            nested = payload.get("result")
            if nested is not None:
                return _extract_auth_url(nested)
        return ""

    def _parse_response_auth_url(location: str = "", payload: object = None) -> tuple[str, str, str, str] | None:
        auth_url = _extract_auth_url(payload) or str(location or "").strip()
        if not auth_url:
            return None
        parsed = _parse_auth_url(auth_url)
        if all(parsed):
            return parsed
        return None

    return_url = ma_url or "/"

    # 1) Current/legacy MA: GET /auth/authorize?provider_id=homeassistant[&return_url=...]
    class _NoRedirectHandler(_ur.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        req = _ur.Request(
            f"{ma_url}/auth/authorize?provider_id=homeassistant&return_url={_up.quote(return_url, safe=':/?=&')}"
        )
        opener = _ur.build_opener(_NoRedirectHandler)
        with opener.open(req, timeout=10) as resp:
            body = resp.read()
            payload = None
            if body:
                with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
                    payload = json.loads(body.decode("utf-8"))
            parsed = _parse_response_auth_url(
                location=resp.headers.get("Location", "") or getattr(resp, "geturl", lambda: "")(),
                payload=payload,
            )
            if parsed:
                return parsed
    except _ue.HTTPError as exc:
        body = exc.read()
        payload = None
        if body:
            with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
                payload = json.loads(body.decode("utf-8"))
        parsed = _parse_response_auth_url(location=exc.headers.get("Location", ""), payload=payload)
        if parsed:
            return parsed
    except Exception as exc:
        logger.debug("MA /auth/authorize bootstrap failed: %s", exc)

    # 2) Current/legacy MA JSON-RPC: POST /api auth/authorization_url
    try:
        body = json.dumps(
            {
                "command": "auth/authorization_url",
                "args": {"provider_id": "homeassistant", "return_url": return_url},
                "message_id": "oauth",
            }
        ).encode()
        req = _ur.Request(f"{ma_url}/api", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with _ur.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            parsed = _parse_response_auth_url(payload=data)
            if parsed:
                return parsed
    except Exception as exc:
        logger.debug("MA JSON-RPC auth/authorization_url failed: %s", exc)

    logger.warning("MA OAuth params unavailable (HTTP authorize and JSON-RPC methods failed)")
    return None


def _ha_login_flow_start(ha_url: str, client_id: str, redirect_uri: str):
    """Start HA login_flow for the MA OAuth flow."""

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


def _get_ha_user_via_ws(ha_token: str):
    """Connect to HA WebSocket with an access token and return user info.

    Returns dict with keys: id, name, is_admin (or None on failure).
    Uses the internal Supervisor DNS name in addon mode.
    """
    try:
        from websockets.sync.client import connect as ws_connect  # noqa: F401 — availability check
    except ImportError:
        logger.warning("websockets.sync.client not available")
        return None

    ha_ws_url = "ws://homeassistant:8123/api/websocket"
    try:
        with _ws_connect(ha_ws_url, close_timeout=5) as ws:
            hello = json.loads(ws.recv(timeout=5))
            if hello.get("type") != "auth_required":
                logger.warning("HA WS unexpected hello: %s", hello.get("type"))
                return None

            ws.send(json.dumps({"type": "auth", "access_token": ha_token}))
            auth_resp = json.loads(ws.recv(timeout=5))
            if auth_resp.get("type") != "auth_ok":
                logger.warning("HA WS auth failed: %s", auth_resp.get("message", ""))
                return None

            ws.send(json.dumps({"id": 1, "type": "auth/current_user"}))
            user_resp = json.loads(ws.recv(timeout=5))
            result = user_resp.get("result", {})
            if not result.get("id"):
                logger.warning("HA WS auth/current_user returned no id")
                return None

            return {
                "id": result["id"],
                "name": result.get("name") or result.get("id"),
                "is_admin": result.get("is_admin", False),
            }
    except Exception as exc:
        logger.warning("HA WS user lookup failed: %s", exc)
        return None


def _find_ma_ingress_url():
    """Discover the MA addon's internal Ingress base URL."""
    return get_ma_addon_internal_ingress_url()


def _create_ma_token_via_ingress(ha_user_id: str, ha_username: str, ha_display_name: str = ""):
    """Create a long-lived MA token via MA's Ingress JSONRPC endpoint.

    MA's Ingress server auto-authenticates requests that carry
    X-Remote-User-ID / X-Remote-User-Name headers.  We POST a JSONRPC call
    to ``auth/token/create`` which works for any authenticated user.

    Returns the MA token string on success, or None on failure.
    """

    base_url = _find_ma_ingress_url()
    url = f"{base_url}/api"
    headers = {
        "Content-Type": "application/json",
        "X-Remote-User-ID": ha_user_id,
        "X-Remote-User-Name": ha_username,
        "X-Remote-User-Display-Name": ha_display_name or ha_username,
    }
    payload = json.dumps(
        {
            "command": "auth/token/create",
            "args": {"name": _MA_TOKEN_NAME},
            "message_id": "1",
        }
    ).encode()

    try:
        req = _ur.Request(url, data=payload, headers=headers, method="POST")
        with _ur.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            logger.debug("MA Ingress raw response: %s", raw[:200])
            data = json.loads(raw)

        # MA JSONRPC may return the result directly as a string (the token),
        # or as {"result": "token"}, or as {"error": {...}} on failure.
        if isinstance(data, str):
            logger.info("Created long-lived MA token via Ingress for user '%s'", ha_username)
            return data

        if isinstance(data, dict):
            if data.get("error"):
                logger.warning("MA Ingress token/create error: %s", data["error"])
                return None

            token = data.get("result")
            if token and isinstance(token, str):
                logger.info("Created long-lived MA token via Ingress for user '%s'", ha_username)
                return token

        logger.warning("MA Ingress token/create unexpected result: %s", data)
        return None
    except Exception as exc:
        logger.warning("MA Ingress JSONRPC failed (%s): %s", url, exc)
        return None


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
var MA_URL = __MA_URL__;
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
      url: data.url, username: data.username, message: data.message}, window.location.origin);
    setTimeout(function() { window.close(); }, 1500);
  }
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _await_loop_result(loop, coro, *, timeout: float, description: str):
    """Run a coroutine on the main loop and wait in a background thread."""
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)
    except Exception:
        logger.debug("%s failed", description, exc_info=True)
        return None


def _run_ma_discover_job(job_id: str, loop, is_addon: bool) -> None:
    """Resolve Music Assistant discovery in a background thread and store the result."""
    from services.ma_discovery import discover_ma_servers, validate_ma_url

    def _finish_success(servers: list[dict]) -> None:
        discovered_url = ""
        if servers and isinstance(servers[0], dict):
            discovered_url = str(servers[0].get("url") or "")
        state.finish_async_job(
            job_id,
            {
                "success": True,
                "is_addon": is_addon,
                "servers": servers,
                "integration": _build_ma_integration_summary(discovered_url),
            },
        )

    for candidate in ("http://localhost:8095", "http://homeassistant.local:8095") if is_addon else ():
        info = _await_loop_result(loop, validate_ma_url(candidate), timeout=5.0, description=f"validate {candidate}")
        if info:
            _finish_success([info])
            return

    ma_url, _ = state.get_ma_api_credentials()
    if ma_url:
        info = _await_loop_result(loop, validate_ma_url(ma_url), timeout=5.0, description=f"validate {ma_url}")
        if info:
            _finish_success([info])
            return

    if is_addon:
        _finish_success([])
        return

    cfg = load_config()
    sendspin_host = (cfg.get("SENDSPIN_SERVER") or "").strip()
    if sendspin_host and sendspin_host.lower() not in ("auto", "discover", ""):
        candidate = f"http://{sendspin_host}:8095"
        info = _await_loop_result(loop, validate_ma_url(candidate), timeout=5.0, description=f"validate {candidate}")
        if info:
            _finish_success([info])
            return

    sendspin_ma_host = _ma_host_from_sendspin_clients()
    if sendspin_ma_host:
        candidate = f"http://{sendspin_ma_host}:8095"
        info = _await_loop_result(loop, validate_ma_url(candidate), timeout=5.0, description=f"validate {candidate}")
        if info:
            _finish_success([info])
            return

    try:
        servers = _await_loop_result(loop, discover_ma_servers(timeout=5.0), timeout=10.0, description="mDNS discover")
        if servers is None:
            raise RuntimeError("Discovery failed")
        _finish_success(servers)
    except Exception:
        logger.exception("MA mDNS discovery failed")
        state.finish_async_job(job_id, {"success": False, "is_addon": is_addon, "error": "Discovery failed"})


def _run_ma_rediscover_job(job_id: str, loop, ma_url: str, ma_token: str, player_info: list[dict[str, str]]) -> None:
    """Refresh MA groups in a background thread and store the result."""
    try:
        from services.ma_client import discover_ma_groups

        result = _await_loop_result(
            loop,
            discover_ma_groups(ma_url, ma_token, player_info),
            timeout=15.0,
            description="MA rediscover",
        )
        if result is None:
            raise RuntimeError("MA rediscover failed")
        name_map, all_groups = result
        state.set_ma_api_credentials(ma_url, ma_token)
        state.set_ma_groups(name_map, all_groups)
        state.finish_async_job(
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
        state.finish_async_job(job_id, {"success": False, "error": "Internal error"})


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
            predicted = state.fail_ma_pending_op(state_key or target_queue_id, op_id, error)
            state.finish_async_job(
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
        predicted = state.apply_ma_now_playing_prediction(
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
        state.finish_async_job(
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
        predicted = state.fail_ma_pending_op(state_key or target_queue_id, op_id, str(exc))
        logger.exception("MA queue command '%s' failed", action)
        state.finish_async_job(
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


@ma_bp.route("/api/ma/discover", methods=["GET"])
def api_ma_discover():
    """Discover Music Assistant servers.

    HA addon mode: 1) homeassistant.local:8095 (Supervisor DNS), 2) saved MA_API_URL.
    Other modes: 1) saved MA_API_URL, 2) SENDSPIN_SERVER host, 3) sendspin
    client connection host, 4) mDNS scan.

    Always returns ``is_addon`` flag so frontend can adjust UI.
    """
    is_addon = _detect_runtime() == "ha_addon"
    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    job_id = str(uuid.uuid4())
    state.create_async_job(job_id, "ma-discover")
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
    job = state.get_async_job(job_id)
    if job is None or job.get("job_type") != "ma-discover":
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") == "running":
        return jsonify({"status": "running", "is_addon": job.get("is_addon", False)})
    return jsonify(job)


@ma_bp.route("/api/ma/login", methods=["POST"])
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
    # Try the library first (works with stable MA), then fall back to direct HTTP
    # which supports both stable and beta MA formats.
    token = None
    try:
        from music_assistant_client import login_with_token

        fut = asyncio.run_coroutine_threadsafe(
            login_with_token(ma_url, username, password, token_name=_MA_TOKEN_NAME),
            loop,
        )
        _user, token = fut.result(timeout=30.0)
    except Exception as lib_exc:
        # Library login failed — always try direct HTTP fallback.
        # The library raises generic "Invalid username or password" for any 401,
        # including format mismatches with MA beta, so we can't distinguish
        # real auth errors from format issues at this level.
        logger.info("Library login failed (%s), trying direct HTTP login", lib_exc)
        try:
            session_token = _ma_http_login(ma_url, username, password)
            token = _exchange_for_long_lived_token(ma_url, session_token)
        except RuntimeError as exc:
            err_msg = str(exc)
            if "invalid" in err_msg.lower() or "password" in err_msg.lower():
                return jsonify({"success": False, "error": "Invalid username or password"}), 401
            logger.warning("MA direct login failed: %s", exc)
            return jsonify({"success": False, "error": "Login failed"}), 401
        except ConnectionError:
            logger.exception("MA server unreachable during login")
            return jsonify({"success": False, "error": "Music Assistant server is unreachable"}), 502
        except Exception:
            logger.exception("MA direct login failed")
            return jsonify({"success": False, "error": "Login failed"}), 500

    if not token:
        return jsonify({"success": False, "error": "Login succeeded but no token received"}), 500

    # Save to config.json
    def _save_ma_creds(cfg: dict) -> None:
        cfg["MA_API_URL"] = ma_url
        cfg["MA_API_TOKEN"] = token
        cfg["MA_USERNAME"] = username
        cfg["MA_AUTH_PROVIDER"] = "builtin"

    update_config(_save_ma_creds)
    state.set_ma_api_credentials(ma_url, token)

    # Trigger MA group rediscovery in background
    try:
        asyncio.run_coroutine_threadsafe(
            _rediscover_after_login(ma_url, token, _bridge_players_snapshot()),
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


# ---------------------------------------------------------------------------
#  HA OAuth → MA token flow  (for MA running as HA addon)
# ---------------------------------------------------------------------------


@ma_bp.route("/api/ma/ha-auth-page")
def api_ma_ha_auth_page():
    """Self-contained popup page for HA → MA OAuth login.

    Opens in a popup window from the main UI.  Handles credentials + MFA,
    then posts the result back to ``window.opener`` via ``postMessage``.
    """
    ma_url = request.args.get("ma_url", "")
    scheme = _up.urlparse(ma_url).scheme.lower() if ma_url else ""
    if scheme and scheme not in ("http", "https"):
        return Response("Invalid URL scheme", status=400)
    safe_ma_url = json.dumps(ma_url)  # JS-safe; includes surrounding quotes
    return Response(
        _HA_AUTH_PAGE_HTML.replace("__MA_URL__", safe_ma_url),
        content_type="text/html; charset=utf-8",
    )


@ma_bp.route("/api/ma/ha-silent-auth", methods=["POST"])
def api_ma_ha_silent_auth():
    """Silent auth: create MA token via Ingress JSONRPC (addon mode).

    Flow:
    1. Frontend sends HA access token (from hassTokens localStorage)
    2. Backend connects to HA WS → gets user_id, username
    3. Backend POSTs JSONRPC to MA Ingress (port 8094) with user headers
    4. MA auto-authenticates → auth/token/create → long-lived MA token
    5. Backend saves token and triggers group rediscovery

    Idempotent: if an existing long-lived token is valid, returns success.

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

    # 1. Get HA user info via WebSocket
    ha_user = _get_ha_user_via_ws(ha_token)
    if not ha_user:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Could not verify HA user — token may be expired",
                }
            ),
            401,
        )

    # 2. Create MA token via Ingress JSONRPC
    ma_token = _create_ma_token_via_ingress(ha_user["id"], ha_user["name"], ha_user.get("name", ""))
    if not ma_token:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Could not create MA token via Ingress — is Music Assistant running?",
                }
            ),
            502,
        )

    # 3. Validate the new token against MA's regular port
    if not _validate_ma_token(ma_url, ma_token):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "MA token created but validation failed",
                }
            ),
            500,
        )

    # 4. Save and rediscover
    _save_ma_token_and_rediscover(ma_url, ma_token, ha_user.get("name", ""), auth_provider="ha")

    return jsonify(
        {
            "success": True,
            "url": ma_url,
            "username": ha_user.get("name", ""),
            "message": "Connected to Music Assistant via Home Assistant.",
        }
    )


@ma_bp.route("/api/ma/ha-login", methods=["POST"])
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
            _save_ma_token_and_rediscover(ma_url, ma_token, username, auth_provider="ha")

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
            _save_ma_token_and_rediscover(ma_url, ma_token, username, auth_provider="ha")

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


@ma_bp.route("/api/ma/groups", methods=["GET"])
def api_ma_groups():
    """Return all MA syncgroup players discovered from the MA API.

    Each group includes id, name, and members with id/name/state/volume/available.
    Returns empty list if MA API is not configured or discovery has not run yet.
    """
    return jsonify(state.get_ma_groups())


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

    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503

    job_id = str(uuid.uuid4())
    state.create_async_job(job_id, "ma-rediscover")
    threading.Thread(
        target=_run_ma_rediscover_job,
        args=(job_id, loop, ma_url, ma_token, _bridge_players_snapshot()),
        daemon=True,
        name=f"ma-rediscover-{job_id[:8]}",
    ).start()
    return jsonify({"success": True, "job_id": job_id, "status": "running"}), 202


@ma_bp.route("/api/ma/rediscover/result/<job_id>", methods=["GET"])
def api_ma_rediscover_result(job_id: str):
    """Poll for async MA rediscover results."""
    job = state.get_async_job(job_id)
    if job is None or job.get("job_type") != "ma-rediscover":
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") == "running":
        return jsonify({"status": "running"})
    return jsonify(job)


@ma_bp.route("/api/ma/nowplaying", methods=["GET"])
def api_ma_nowplaying():
    """Return current MA now-playing metadata.

    Returns {"connected": false} when MA integration is not active.
    Fields when connected: state, track, artist, album, image_url,
    elapsed, elapsed_updated_at, duration, shuffle, repeat,
    queue_index, queue_total, syncgroup_id, and optional prev_/next_ track metadata.
    """
    if not state.is_ma_connected():
        return jsonify({"connected": False})
    return jsonify(state.get_ma_now_playing())


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

    _ma_url, ma_token = state.get_ma_api_credentials()
    req = _ur.Request(artwork_url, headers={"Accept": "image/*"})
    if is_ma_origin and ma_token:
        req.add_header("Authorization", f"Bearer {ma_token}")

    try:
        with _ur.urlopen(req, timeout=15) as resp:
            body = resp.read()
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
    if not state.is_ma_connected():
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

    loop = state.get_main_loop()
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

        predicted = state.apply_ma_now_playing_prediction(
            state_key,
            _build_ma_prediction_patch(action, value),
            op_id=op_id,
            action=action,
            value=value,
        )
        job_id = str(uuid.uuid4())
        state.create_async_job(job_id, "ma-queue-cmd")
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
        state.fail_ma_pending_op(state_key or target_queue_id or "", op_id, str(exc))
        logger.exception("MA queue command '%s' failed", action)
        return jsonify(
            {"success": False, "error": "Internal error", "error_code": "internal_error", "op_id": op_id}
        ), 500


@ma_bp.route("/api/ma/queue/cmd/result/<job_id>", methods=["GET"])
def api_ma_queue_cmd_result(job_id: str):
    """Poll for async MA queue command results."""
    job = state.get_async_job(job_id)
    if job is None or job.get("job_type") != "ma-queue-cmd":
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") == "running":
        return jsonify({"status": "running"})
    return jsonify(job)


@ma_bp.route("/api/debug/ma")
def api_debug_ma():
    """Debug: dump MA now-playing cache, groups, per-client player_ids, and live queues."""
    with state._ma_now_playing_lock:
        cache = dict(state._ma_now_playing)
    groups = state.get_ma_groups()
    clients_info = _debug_clients_snapshot()

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
                    await ws.send(json.dumps({"command": "player_queues/all", "args": {}, "message_id": 99}))
                    for _ in range(10):
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if str(msg.get("message_id")) == "99":
                            return [q.get("queue_id", "") for q in (msg.get("result") or [])]
                return []

            loop = state.get_main_loop()
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
