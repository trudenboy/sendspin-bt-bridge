"""Signing the bridge in to Music Assistant: password, HA OAuth (with MFA), HA add-on silent auth."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import socket
import urllib.error as _ue
import urllib.parse as _up
import urllib.request as _ur

from sendspin_bridge.application.errors import UseCaseError, config_write_error
from sendspin_bridge.application.music_assistant.common import (
    bridge_players_snapshot as _bridge_players_snapshot,
)
from sendspin_bridge.application.music_assistant.common import (
    ma_host_from_sendspin_clients as _ma_host_from_sendspin_clients,
)
from sendspin_bridge.config import load_config, update_config
from sendspin_bridge.security.redaction import redact
from sendspin_bridge.services.ha.ha_addon import KNOWN_MA_ADDON_SLUGS, get_ma_addon_internal_ingress_url
from sendspin_bridge.services.infrastructure.url_safety import (
    SafeHTTPConnection,
    SafeHTTPSConnection,
    is_safe_external_url,
    safe_build_opener,
    safe_urlopen,
)
from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop
from sendspin_bridge.services.music_assistant.ma_monitor import reload_monitor_credentials
from sendspin_bridge.services.music_assistant.ma_runtime_state import (
    get_ma_api_credentials,
    set_ma_api_credentials,
    set_ma_groups,
)

logger = logging.getLogger(__name__)


def _log_unexpected_result(what: str, payload: object) -> None:
    """Log a response the code did not expect, without its credentials.

    These branches fire exactly when a token arrives in an unanticipated
    shape, so the payload is the most likely place for one to be sitting.
    """
    logger.warning("%s returned an unexpected result: %s", what, redact(str(payload)))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MA_TOKEN_NAME_PREFIX = "Sendspin BT Bridge"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_instance_hostname() -> str:
    """Return a stable hostname label for this physical bridge instance."""
    try:
        hostname = socket.gethostname().strip()
    except Exception:
        hostname = ""
    return hostname or "unknown-host"


def _ma_token_name() -> str:
    """Return the long-lived MA token label for this bridge instance."""
    return f"{_MA_TOKEN_NAME_PREFIX} ({_current_instance_hostname()})"


def _ma_token_matches_current_instance(cfg: dict, ma_url: str, ma_token: str) -> bool:
    """Return True when a saved token belongs to this instance and MA URL.

    Empty hostname metadata is treated as a legacy token that can still be
    reused and backfilled on the next successful save.
    """
    configured_url = str(cfg.get("MA_API_URL") or "").strip().rstrip("/")
    configured_token = str(cfg.get("MA_API_TOKEN") or "").strip()
    if configured_url and configured_url != ma_url.rstrip("/"):
        return False
    if configured_token and configured_token != ma_token:
        return False
    saved_hostname = str(cfg.get("MA_TOKEN_INSTANCE_HOSTNAME") or "").strip()
    return not saved_hostname or saved_hostname == _current_instance_hostname()


def _ws_connect(url: str, **kwargs):
    """Wrapper around websockets.sync.client.connect that handles proxy kwarg compatibility.

    Older websockets versions (<14) don't support the ``proxy`` parameter.
    """
    from websockets.sync.client import connect as ws_connect

    try:
        return ws_connect(url, proxy=None, **kwargs)
    except TypeError:
        return ws_connect(url, **kwargs)


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
                        "args": {"name": _ma_token_name()},
                        "message_id": 2,
                    }
                )
            )
            create_resp = json.loads(ws.recv(timeout=10))
            long_lived = create_resp.get("result")
            if long_lived and isinstance(long_lived, str):
                logger.info("Created long-lived MA API token '%s'", _ma_token_name())
                return long_lived
            _log_unexpected_result("auth/token/create", create_resp)
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
        with safe_urlopen(req, timeout=15) as resp:
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
        from sendspin_bridge.services.music_assistant.ma_client import discover_ma_groups

        id_map, all_groups = await discover_ma_groups(ma_url, ma_token, bridge_players)
        set_ma_groups(id_map, all_groups)
        logger.info("MA groups rediscovered after login: %d groups", len(all_groups))
    except Exception:
        logger.debug("MA group rediscovery after login failed", exc_info=True)


def _save_ma_token_and_rediscover(ma_url: str, ma_token: str, username: str = "", auth_provider: str = "") -> None:
    """Save MA token to config and trigger group rediscovery.

    A failed config-dir write (issue #190 — bind-mount target not owned by
    the bridge UID) raises ``config_write_error``, so every sign-in path
    answers with the same chown remediation.  Non-OS exceptions still
    propagate so real bugs aren't masked.
    """
    token_label = _ma_token_name()
    token_hostname = _current_instance_hostname()

    def _save(cfg: dict) -> None:
        cfg["MA_API_URL"] = ma_url
        cfg["MA_API_TOKEN"] = ma_token
        cfg["MA_TOKEN_INSTANCE_HOSTNAME"] = token_hostname
        cfg["MA_TOKEN_LABEL"] = token_label
        if username:
            cfg["MA_USERNAME"] = username
        if auth_provider:
            cfg["MA_AUTH_PROVIDER"] = auth_provider

    try:
        update_config(_save)
    except OSError as exc:
        raise config_write_error(exc, context="Cannot save MA token") from exc

    set_ma_api_credentials(ma_url, ma_token)

    loop = get_main_loop()
    if loop:
        reload_monitor_credentials(loop, ma_url, ma_token)
        try:
            asyncio.run_coroutine_threadsafe(
                _rediscover_after_login(ma_url, ma_token, _bridge_players_snapshot()), loop
            )
        except Exception:
            pass


# ── MA ↔ HA OAuth helpers (shared by ha-login and ha-silent-auth) ─────────


def _get_ma_oauth_bootstrap(ma_url: str) -> tuple[tuple[str, str, str, str] | None, str]:
    """Resolve HA OAuth parameters from Music Assistant auth endpoints.

    Returns a ``(oauth_params, error_message)`` tuple so callers can surface a
    precise MA-side failure instead of falling back to a generic unsupported
    message when the server exposes a reason.
    """

    def _parse_auth_url(auth_url: str):
        parsed = _up.urlparse(auth_url)
        params = _up.parse_qs(parsed.query)
        ha_base = f"{parsed.scheme}://{parsed.netloc}"
        # MA-reported ha_base is attacker-influenced (the MA server could
        # return a URL pointing at an internal target).  Gate it through
        # the same SSRF check that user-supplied URLs go through.
        if not is_safe_external_url(ha_base):
            logger.warning("MA reported HA OAuth base that failed SSRF check: %s", ha_base)
            return "", "", "", ""
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

    def _extract_error(payload: object, fallback: str = "") -> str:
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, dict):
            for key in ("error", "message", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                nested = _extract_error(value)
                if nested:
                    return nested
            result_payload = payload.get("result")
            if result_payload is not None:
                return _extract_error(result_payload)
        return str(fallback or "").strip()

    def _parse_response_auth_url(location: str = "", payload: object = None) -> tuple[str, str, str, str] | None:
        auth_url = _extract_auth_url(payload) or str(location or "").strip()
        if not auth_url:
            return None
        parsed = _parse_auth_url(auth_url)
        if all(parsed):
            return parsed
        return None

    def _body_to_payload(body: bytes) -> tuple[object | None, str]:
        if not body:
            return None, ""
        with contextlib.suppress(UnicodeDecodeError):
            text = body.decode("utf-8").strip()
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(text), text
            return None, text
        return None, ""

    def _surface_error(message: str) -> str:
        detail = str(message or "").strip()
        if not detail:
            return (
                "Music Assistant did not provide Home Assistant authentication details. "
                "If Home Assistant login is not configured in Music Assistant, switch to Music Assistant authentication."
            )
        return (
            f"Music Assistant Home Assistant auth is unavailable: {detail}. "
            "If Home Assistant login is not configured in Music Assistant, switch to Music Assistant authentication."
        )

    errors: list[str] = []
    return_url = ma_url or "/"

    # 1) Current/legacy MA: GET /auth/authorize?provider_id=homeassistant[&return_url=...]
    class _NoRedirectHandler(_ur.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        req = _ur.Request(
            f"{ma_url}/auth/authorize?provider_id=homeassistant&return_url={_up.quote(return_url, safe=':/?=&')}"
        )
        opener = safe_build_opener(_NoRedirectHandler)
        with opener.open(req, timeout=10) as resp:
            body = resp.read()
            payload, text = _body_to_payload(body)
            parsed = _parse_response_auth_url(
                location=resp.headers.get("Location", "") or getattr(resp, "geturl", lambda: "")(),
                payload=payload,
            )
            if parsed:
                return parsed, ""
            err = _extract_error(payload, text)
            if err:
                errors.append(err)
    except _ue.HTTPError as exc:
        body = exc.read()
        payload, text = _body_to_payload(body)
        parsed = _parse_response_auth_url(location=exc.headers.get("Location", ""), payload=payload)
        if parsed:
            return parsed, ""
        err = _extract_error(payload, text or exc.reason)
        if err:
            errors.append(err)
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
        with safe_urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            parsed = _parse_response_auth_url(payload=data)
            if parsed:
                return parsed, ""
            err = _extract_error(data)
            if err:
                errors.append(err)
    except _ue.HTTPError as exc:
        body = exc.read()
        payload, text = _body_to_payload(body)
        err = _extract_error(payload, text or exc.reason)
        if err:
            errors.append(err)
    except Exception as exc:
        logger.debug("MA JSON-RPC auth/authorization_url failed: %s", exc)

    error_message = _surface_error(next((msg for msg in errors if msg), ""))
    logger.warning("MA OAuth params unavailable (HTTP authorize and JSON-RPC methods failed): %s", error_message)
    return None, error_message


def _get_ma_oauth_params(ma_url: str):
    """Backward-compatible wrapper returning only parsed OAuth parameters."""
    oauth_info, _error = _get_ma_oauth_bootstrap(ma_url)
    return oauth_info


def _get_ma_server_info(ma_url: str) -> dict[str, object]:
    """Return parsed ``/info`` payload from MA, or an empty dict on failure."""
    try:
        with safe_urlopen(f"{ma_url.rstrip('/')}/info", timeout=10) as resp:
            data = json.loads(resp.read())
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("MA /info lookup failed for %s: %s", ma_url, exc)
        return {}


def _ma_reports_homeassistant_addon(ma_url: str) -> bool:
    """Return True when the MA server reports HA add-on mode."""
    return bool(_get_ma_server_info(ma_url).get("homeassistant_addon"))


def _derive_ha_urls_from_ma(ma_url: str) -> list[str]:
    """Derive likely HA Core base URLs from an MA add-on URL.

    Each candidate is filtered through ``is_safe_external_url`` so we never
    hand back a URL that would resolve to a forbidden private/loopback
    address (e.g., when the caller supplied an arbitrary MA URL).
    """
    parsed = _up.urlparse(str(ma_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return []

    candidates: list[str] = []

    def _add(url: str) -> None:
        normalized = str(url or "").rstrip("/")
        if not normalized or normalized in candidates:
            return
        if not is_safe_external_url(normalized):
            return
        candidates.append(normalized)

    if parsed.scheme == "https":
        _add(f"https://{parsed.hostname}")
    _add(f"{parsed.scheme}://{parsed.hostname}:8123")
    return candidates


def _exchange_ha_auth_code(ha_url: str, code: str, client_id: str) -> dict[str, object] | None:
    """Exchange a Home Assistant OAuth authorization code for tokens."""
    body = _up.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
        }
    ).encode()
    req = _ur.Request(
        f"{ha_url.rstrip('/')}/auth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with safe_urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read())
        return payload if isinstance(payload, dict) else None
    except Exception as exc:
        logger.warning("HA auth/token exchange failed: %s", exc)
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
        with safe_urlopen(req, timeout=10) as resp:
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
        with safe_urlopen(req, timeout=10) as resp:
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
    import urllib.parse as _up

    try:
        cb_url = (
            f"{ma_url}/auth/callback?code={_up.quote(code)}&state={_up.quote(oauth_state)}&provider_id=homeassistant"
        )
        if not is_safe_external_url(cb_url):
            logger.warning("Refusing MA callback exchange: disallowed URL %s", cb_url)
            return None
        parsed_cb = _up.urlparse(cb_url)
        hostname = parsed_cb.hostname or "localhost"
        conn: SafeHTTPConnection | SafeHTTPSConnection
        if parsed_cb.scheme == "https":
            conn = SafeHTTPSConnection(hostname, parsed_cb.port or 443, timeout=15)
        else:
            conn = SafeHTTPConnection(hostname, parsed_cb.port or 80, timeout=15)
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


def _get_ha_user_via_ws(ha_token: str, ha_url: str | None = None):
    """Connect to HA WebSocket with an access token and return user info.

    Returns dict with keys: id, name, is_admin (or None on failure).
    Uses the internal Supervisor DNS name in addon mode unless a remote HA URL
    is supplied explicitly.
    """
    try:
        from websockets.sync.client import connect as ws_connect  # noqa: F401 — availability check
    except ImportError:
        logger.warning("websockets.sync.client not available")
        return None

    if ha_url:
        parsed = _up.urlparse(ha_url.rstrip("/"))
        if parsed.scheme == "https":
            ha_ws_url = f"wss://{parsed.netloc}/api/websocket"
        else:
            ha_ws_url = f"ws://{parsed.netloc}/api/websocket"
    else:
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


def _get_ha_supervisor_addon_info_via_ws(ha_token: str, slug: str, ha_url: str | None = None):
    """Fetch HA Supervisor addon info over the HA WebSocket API."""
    try:
        from websockets.sync.client import connect as ws_connect  # noqa: F401 — availability check
    except ImportError:
        logger.warning("websockets.sync.client not available")
        return None

    if ha_url:
        parsed = _up.urlparse(ha_url.rstrip("/"))
        if parsed.scheme == "https":
            ha_ws_url = f"wss://{parsed.netloc}/api/websocket"
        else:
            ha_ws_url = f"ws://{parsed.netloc}/api/websocket"
    else:
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

            ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "supervisor/api",
                        "endpoint": f"/addons/{slug}/info",
                        "method": "get",
                    }
                )
            )
            addon_resp = json.loads(ws.recv(timeout=10))
            if addon_resp.get("type") != "result":
                logger.debug("HA supervisor/api for %s returned unexpected packet: %r", slug, addon_resp)
                return None
            if addon_resp.get("success") is False:
                logger.debug("HA supervisor/api lookup failed for %s: %r", slug, addon_resp.get("error"))
                return None
            result = addon_resp.get("result")
            if isinstance(result, dict) and result.get("slug"):
                return result
            logger.debug("HA supervisor/api for %s returned non-addon result: %r", slug, result)
            return None
    except Exception as exc:
        logger.debug("HA supervisor/api lookup failed for %s over WS: %s", slug, exc)
        return None


def _create_ha_ingress_session_via_ws(ha_token: str, ha_url: str | None = None) -> str | None:
    """Create an HA ingress session over the HA WebSocket API."""
    try:
        from websockets.sync.client import connect as ws_connect  # noqa: F401 — availability check
    except ImportError:
        logger.warning("websockets.sync.client not available")
        return None

    if ha_url:
        parsed = _up.urlparse(ha_url.rstrip("/"))
        if parsed.scheme == "https":
            ha_ws_url = f"wss://{parsed.netloc}/api/websocket"
        else:
            ha_ws_url = f"ws://{parsed.netloc}/api/websocket"
    else:
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

            ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "type": "supervisor/api",
                        "endpoint": "/ingress/session",
                        "method": "post",
                    }
                )
            )
            session_resp = json.loads(ws.recv(timeout=10))
            if session_resp.get("type") != "result":
                logger.debug("HA ingress session returned unexpected packet: %r", session_resp)
                return None
            if session_resp.get("success") is False:
                logger.debug("HA ingress session creation failed: %r", session_resp.get("error"))
                return None
            result = session_resp.get("result")
            if isinstance(result, dict):
                session = str(result.get("session") or "").strip()
                if session:
                    return session
            logger.debug("HA ingress session creation returned unexpected result: %r", result)
            return None
    except Exception as exc:
        logger.debug("HA ingress session creation failed over WS: %s", exc)
        return None


def _find_ma_ingress_url():
    """Discover the MA addon's internal Ingress base URL."""
    return get_ma_addon_internal_ingress_url()


def _latin1_safe(value: str) -> str:
    """Percent-encode non-latin1 characters so the value is safe for HTTP headers.

    ``urllib.request.Request`` encodes header values as latin-1 (ISO 8859-1).
    Non-ASCII characters outside that range (e.g. CJK) cause
    ``UnicodeEncodeError``.  RFC 8187 recommends percent-encoding such values.
    """
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return _up.quote(value, safe="")


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
        "X-Remote-User-Name": _latin1_safe(ha_username),
        "X-Remote-User-Display-Name": _latin1_safe(ha_display_name or ha_username),
    }
    payload = json.dumps(
        {
            "command": "auth/token/create",
            "args": {"name": _ma_token_name()},
            "message_id": "1",
        }
    ).encode()

    try:
        req = _ur.Request(url, data=payload, headers=headers, method="POST")
        with safe_urlopen(req, timeout=10) as resp:
            raw = resp.read().decode()
            # The success response body *is* the long-lived MA token (bare
            # string or ``{"result": "<token>"}``) — never log it.
            logger.debug("MA Ingress response received (%d bytes)", len(raw))
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

        _log_unexpected_result("MA Ingress token/create", data)
        return None
    except Exception as exc:
        logger.warning("MA Ingress JSONRPC failed (%s): %s", url, exc)
        return None


def _find_ma_ingress_url_via_ha(ha_url: str, ha_token: str) -> str:
    """Resolve the MA add-on ingress URL via Home Assistant's hassio proxy."""
    headers = {"Authorization": f"Bearer {ha_token}"}
    for slug in KNOWN_MA_ADDON_SLUGS:
        data = _get_ha_supervisor_addon_info_via_ws(ha_token, slug, ha_url=ha_url)
        try:
            if not isinstance(data, dict):
                req = _ur.Request(f"{ha_url.rstrip('/')}/api/hassio/addons/{slug}/info", headers=headers, method="GET")
                with safe_urlopen(req, timeout=10) as resp:
                    payload = json.loads(resp.read())
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict) and isinstance(payload, dict) and payload.get("slug"):
                    data = payload
                if not isinstance(data, dict):
                    logger.debug("HA hassio addon lookup for %s returned non-addon payload: %r", slug, payload)
                    continue
            if data.get("state") != "started":
                continue
            ingress_path = str(data.get("ingress_url") or data.get("ingress_entry") or data.get("webui") or "").rstrip(
                "/"
            )
            if ingress_path:
                if ingress_path.startswith("http://") or ingress_path.startswith("https://"):
                    return ingress_path
                return f"{ha_url.rstrip('/')}{ingress_path}"
        except Exception as exc:
            logger.debug("HA hassio addon lookup failed for %s: %s", slug, exc)
    return ""


# Home Assistant Core as add-ons reach it on the Supervisor network.
_HA_CORE_URL = "http://homeassistant:8123"


def _create_ma_token_via_ha_proxy(ha_url: str, ha_token: str) -> str | None:
    """Create a long-lived MA token through the HA ingress proxy."""
    base_url = _find_ma_ingress_url_via_ha(ha_url, ha_token)
    if not base_url:
        logger.warning("MA ingress URL unavailable via Home Assistant proxy")
        return None
    ingress_session = _create_ha_ingress_session_via_ws(ha_token, ha_url=ha_url)
    if not ingress_session:
        logger.warning("HA ingress session unavailable for MA token creation")
        return None

    payload = json.dumps(
        {
            "command": "auth/token/create",
            "args": {"name": _ma_token_name()},
            "message_id": "1",
        }
    ).encode()
    req = _ur.Request(
        f"{base_url}/api",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"ingress_session={ingress_session}",
        },
        method="POST",
    )
    try:
        with safe_urlopen(req, timeout=15) as resp:
            raw = resp.read().decode()
            data = json.loads(raw)
    except Exception as exc:
        logger.warning("MA token creation via HA ingress failed: %s", exc)
        return None

    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        if data.get("error"):
            logger.warning("MA ingress proxy token/create error: %s", data.get("error"))
            return None
        token = data.get("result")
        if isinstance(token, str) and token:
            return token
    _log_unexpected_result("MA ingress proxy token/create", data)
    return None


def _complete_ma_login_via_ha_token(
    ha_url: str, ha_token: str, ma_url: str
) -> tuple[dict[str, object] | None, str | None]:
    """Turn a Home Assistant access token into a validated MA long-lived token."""
    ha_user = _get_ha_user_via_ws(ha_token, ha_url=ha_url)
    if not ha_user:
        return None, "Could not verify Home Assistant user"

    ma_token = _create_ma_token_via_ha_proxy(ha_url, ha_token)
    if not ma_token:
        return None, "Could not create Music Assistant token via Home Assistant ingress"

    if not _validate_ma_token(ma_url, ma_token):
        return None, "Music Assistant token created but validation failed"

    result = dict(ha_user)
    result["ma_token"] = ma_token
    return result, None


def _complete_ma_login_via_ha_code(
    ha_url: str, code: str, client_id: str, ma_url: str
) -> tuple[dict[str, object] | None, str | None]:
    """Exchange an HA auth code and mint a validated MA token."""
    token_payload = _exchange_ha_auth_code(ha_url, code, client_id)
    if not token_payload:
        return None, "Could not exchange Home Assistant authorization code"

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return None, "Home Assistant did not return an access token"

    return _complete_ma_login_via_ha_token(ha_url, access_token, ma_url)


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
    <form id="creds-form">
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
    <form id="mfa-form">
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

<script nonce="__CSP_NONCE__">
// Relative to this page (…/music-assistant/session/ha-auth-page), so it works
// under the Home Assistant ingress prefix too.
var SIGN_IN_URL = 'ha';
var MA_URL = __MA_URL__;
var CSRF_TOKEN = __CSRF_TOKEN__;

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

async function signIn(body) {
  var headers = {'Content-Type': 'application/json'};
  if (CSRF_TOKEN) headers['X-CSRF-Token'] = CSRF_TOKEN;
  var resp = await fetch(SIGN_IN_URL, {
    method: 'POST', headers: headers, credentials: 'same-origin', body: JSON.stringify(body),
  });
  var data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || data.title || 'Authentication failed');
  return data;
}

async function submitCreds(e) {
  e.preventDefault();
  var user = document.getElementById('username').value.trim();
  var pass = document.getElementById('password').value;
  if (!user || !pass) return;
  setLoading('creds-btn', true);
  setMsg('creds-msg', '', false);
  try {
    var data = await signIn({step: 'init', username: user, password: pass, ma_url: MA_URL});
    if (data.step === 'mfa') {
      var label = document.getElementById('mfa-label');
      label.textContent = 'Enter code from ' + (data.mfa_module_name || 'authenticator app');
      showStep('mfa');
      document.getElementById('mfa-code').focus();
    } else {
      onSuccess(data);
    }
  } catch (err) {
    setMsg('creds-msg', err.message, true);
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
    onSuccess(await signIn({step: 'mfa', code: code, ma_url: MA_URL}));
  } catch (err) {
    setMsg('mfa-msg', err.message, true);
    document.getElementById('mfa-code').value = '';
    document.getElementById('mfa-code').focus();
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

document.getElementById('creds-form').addEventListener('submit', submitCreds);
document.getElementById('mfa-form').addEventListener('submit', submitMfa);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------

#: Public names for the helpers other modules call.
validate_ma_token = _validate_ma_token
ma_http_login = _ma_http_login


def _connected(ma_url: str, username: str, message: str) -> dict:
    return {"url": ma_url, "username": username, "message": message}


def _require_safe_url(ma_url: str) -> None:
    if not is_safe_external_url(ma_url):
        raise UseCaseError(400, "url_not_allowed", "Invalid or disallowed URL")


def _resolve_ma_url_for_login(loop) -> str:
    """No URL given: the configured one, SENDSPIN_SERVER, the live Sendspin peer, then mDNS."""
    from sendspin_bridge.services.music_assistant.ma_discovery import discover_ma_servers, validate_ma_url

    candidate = ""
    known_url, _ = get_ma_api_credentials()
    if known_url:
        candidate = known_url
    if not candidate:
        host = (load_config().get("SENDSPIN_SERVER") or "").strip()
        if host and host.lower() not in ("auto", "discover", ""):
            candidate = f"http://{host}:8095"
    if not candidate:
        peer = _ma_host_from_sendspin_clients()
        if peer:
            candidate = f"http://{peer}:8095"
    if candidate:
        info = asyncio.run_coroutine_threadsafe(validate_ma_url(candidate), loop).result(timeout=5.0)
        if info:
            logger.info("Using known MA server: %s", info["url"])
            return info["url"]
    try:
        servers = asyncio.run_coroutine_threadsafe(discover_ma_servers(timeout=5.0), loop).result(timeout=10.0)
    except Exception as exc:
        logger.exception("MA discovery failed during login")
        raise UseCaseError(500, "discovery_failed", "Discovery failed. Enter URL manually.") from exc
    if not servers:
        raise UseCaseError(404, "ma_not_found", "No MA server found on network. Enter URL manually.")
    logger.info("Auto-discovered MA server: %s", servers[0]["url"])
    return servers[0]["url"]


def sign_in_with_password(ma_url: str, username: str, password: str) -> dict:
    """Sign in with Music Assistant credentials and store a long-lived token.

    The password is not stored. Without *ma_url* the server is found.
    """
    username = (username or "").strip()
    if not username or not password:
        raise UseCaseError(400, "credentials_required", "Username and password are required")
    loop = get_main_loop()
    if not loop:
        raise UseCaseError(503, "not_ready", "Event loop not available")
    ma_url = (ma_url or "").strip() or _resolve_ma_url_for_login(loop)
    if "://" not in ma_url:
        ma_url = f"http://{ma_url}"
    _require_safe_url(ma_url)

    # Library first (fast path for stable MA) unless strict SSRF mode, then
    # direct HTTP, which also covers MA beta and pins the peer address.
    token = None
    lib_exc: Exception | None = None
    if os.environ.get("SENDSPIN_STRICT_SSRF", "").strip() != "1":
        try:
            from music_assistant_client import login_with_token

            fut = asyncio.run_coroutine_threadsafe(
                login_with_token(ma_url, username, password, token_name=_ma_token_name()), loop
            )
            _user, token = fut.result(timeout=30.0)
        except Exception as exc:
            lib_exc = exc
    if token is None:
        if lib_exc is not None:
            logger.info("Library login failed (%s), trying direct HTTP login", lib_exc)
        try:
            session_token = _ma_http_login(ma_url, username, password)
            token = _exchange_for_long_lived_token(ma_url, session_token)
        except RuntimeError as exc:
            text = str(exc).lower()
            if "invalid" in text or "password" in text:
                raise UseCaseError(401, "invalid_credentials", "Invalid username or password") from exc
            logger.warning("MA direct login failed: %s", exc)
            raise UseCaseError(401, "login_failed", "Login failed") from exc
        except ConnectionError as exc:
            logger.exception("MA server unreachable during login")
            raise UseCaseError(502, "ma_unreachable", "Music Assistant server is unreachable") from exc
        except Exception as exc:
            logger.exception("MA direct login failed")
            raise UseCaseError(500, "login_failed", "Login failed") from exc
    if not token:
        raise UseCaseError(500, "no_token", "Login succeeded but no token received")
    _save_ma_token_and_rediscover(ma_url, token, username, auth_provider="builtin")
    return _connected(ma_url, username, "Connected to Music Assistant. Token saved.")


def ha_auth_page(ma_url: str, nonce: str, csrf_token: str = "") -> str:
    """The popup page for the HA → MA OAuth sign-in (posts back via postMessage)."""
    if ma_url:
        _require_safe_url(ma_url)

    def _js(value: str) -> str:
        # json.dumps does not escape ``</``; without this a crafted URL closes
        # the inline <script> (reflected XSS).
        return json.dumps(value).replace("</", "<\\/")

    return (
        _HA_AUTH_PAGE_HTML.replace("__CSP_NONCE__", nonce)
        .replace("__MA_URL__", _js(ma_url))
        .replace("__CSRF_TOKEN__", _js(csrf_token))
    )


def ha_silent_auth(ha_token: str, ma_url: str) -> dict:
    """Add-on mode: turn the user's HA token into an MA token via MA's ingress."""
    ha_token = (ha_token or "").strip()
    ma_url = (ma_url or "").strip().rstrip("/")
    if not ha_token or not ma_url:
        raise UseCaseError(400, "missing_fields", "Missing ha_token or ma_url")
    _require_safe_url(ma_url)

    existing_url, existing_token = get_ma_api_credentials()
    if existing_token and existing_url and existing_url.rstrip("/") == ma_url:
        cfg = load_config()
        if _ma_token_matches_current_instance(cfg, ma_url, existing_token) and _validate_ma_token(
            ma_url, existing_token
        ):
            if not cfg.get("MA_TOKEN_INSTANCE_HOSTNAME") or not cfg.get("MA_TOKEN_LABEL"):
                _save_ma_token_and_rediscover(
                    ma_url,
                    existing_token,
                    str(cfg.get("MA_USERNAME") or ""),
                    auth_provider=str(cfg.get("MA_AUTH_PROVIDER") or ""),
                )
            logger.debug("Silent auth: existing MA token still valid — reusing")
            return _connected(ma_url, "", "Already connected to Music Assistant.")

    ha_user = _get_ha_user_via_ws(ha_token)
    if not ha_user:
        raise UseCaseError(401, "ha_token_invalid", "Could not verify HA user — token may be expired")
    ma_token = _create_ma_token_via_ingress(ha_user["id"], ha_user["name"], ha_user.get("name", ""))
    if not ma_token:
        # Music Assistant (server #6772) trusts the ingress user headers only
        # from the Supervisor, so the direct call above is refused there. Home
        # Assistant's ingress proxy makes the same request as this user, from
        # the Supervisor.
        ma_token = _create_ma_token_via_ha_proxy(_HA_CORE_URL, ha_token)
    if not ma_token:
        raise UseCaseError(
            502, "ma_ingress_failed", "Could not create MA token via Ingress — is Music Assistant running?"
        )
    if not _validate_ma_token(ma_url, ma_token):
        raise UseCaseError(500, "ma_token_invalid", "MA token created but validation failed")
    _save_ma_token_and_rediscover(ma_url, ma_token, ha_user.get("name", ""), auth_provider="ha")
    return _connected(ma_url, ha_user.get("name", ""), "Connected to Music Assistant via Home Assistant.")


def _flow_error(result: dict) -> UseCaseError:
    errors = result.get("errors") or {}
    if errors.get("base") == "invalid_auth":
        return UseCaseError(401, "invalid_credentials", "Invalid credentials")
    return UseCaseError(401, "authentication_failed", "Authentication failed")


def _mfa_challenge(result: dict, context: dict) -> dict:
    placeholders = result.get("description_placeholders") or {}
    return {
        "step": "mfa",
        "auth_mode": context["auth_mode"],
        "mfa_module_id": placeholders.get("mfa_module_id", "totp"),
        "mfa_module_name": placeholders.get("mfa_module_name", "Authenticator app"),
        "_context": context,
    }


def ha_login_start(ma_url: str, username: str, password: str) -> dict:
    """HA OAuth sign-in to MA, step 1.

    Returns ``{"step": "done", …}`` or ``{"step": "mfa", …, "_context": {…}}``.
    The caller keeps ``_context`` server-side (the session) for step 2 — the
    client only proves it knows the code, so it cannot steer the flow.
    """
    ma_url = (ma_url or "").strip().rstrip("/")
    if not ma_url:
        raise UseCaseError(400, "ma_url_required", "MA URL is required")
    _require_safe_url(ma_url)
    username = (username or "").strip()
    if not username or not password:
        raise UseCaseError(400, "credentials_required", "Username and password are required")

    oauth_info, oauth_error = _get_ma_oauth_bootstrap(ma_url)
    if oauth_info:
        ha_url, client_id, redirect_uri, oauth_state = oauth_info
        flow = _ha_login_flow_start(ha_url, client_id, redirect_uri)
        if not flow or not flow.get("flow_id"):
            raise UseCaseError(502, "ha_unavailable", "Could not start HA authentication")
        flow_id = flow["flow_id"]
        result = _ha_login_flow_step(ha_url, flow_id, {"username": username, "password": password}, client_id)
        if not result:
            raise UseCaseError(502, "ha_unavailable", "Authentication service unavailable")
        if result.get("type") == "create_entry":
            ma_session_token = _ma_callback_exchange(ma_url, result.get("result", ""), oauth_state)
            if not ma_session_token:
                raise UseCaseError(500, "code_exchange_failed", "Failed to exchange HA code for MA token")
            ma_token = _exchange_for_long_lived_token(ma_url, ma_session_token)
            _save_ma_token_and_rediscover(ma_url, ma_token, username, auth_provider="ha")
            return {"step": "done", **_connected(ma_url, username, "Connected to Music Assistant via Home Assistant.")}
        if result.get("type") == "form" and result.get("step_id") == "mfa":
            context = {
                "auth_mode": "ma_oauth",
                "flow_id": flow_id,
                "ha_url": ha_url,
                "client_id": client_id,
                "state": oauth_state,
                "ma_url": ma_url,
                "username": username,
            }
            return _mfa_challenge(result, context)
        raise _flow_error(result)

    if not _ma_reports_homeassistant_addon(ma_url):
        raise UseCaseError(400, "oauth_unavailable", oauth_error)
    ha_urls = _derive_ha_urls_from_ma(ma_url)
    if not ha_urls:
        raise UseCaseError(400, "oauth_unavailable", oauth_error)
    flow = None
    ha_url = client_id = ""
    for candidate in ha_urls:
        started = _ha_login_flow_start(candidate, f"{candidate}/", f"{candidate}/")
        if started and started.get("flow_id"):
            flow, ha_url, client_id = started, candidate, f"{candidate}/"
            break
    if not flow or not flow.get("flow_id"):
        raise UseCaseError(400, "oauth_unavailable", oauth_error)
    flow_id = flow["flow_id"]
    result = _ha_login_flow_step(ha_url, flow_id, {"username": username, "password": password}, client_id)
    if not result:
        raise UseCaseError(502, "ha_unavailable", "Authentication service unavailable")
    if result.get("type") == "create_entry":
        auth_result, auth_error = _complete_ma_login_via_ha_code(
            ha_url, str(result.get("result") or ""), client_id, ma_url
        )
        if not auth_result:
            raise UseCaseError(502, "authentication_failed", auth_error or "Authentication failed")
        ma_token = str(auth_result.get("ma_token") or "")
        ha_username = str(auth_result.get("name") or username)
        _save_ma_token_and_rediscover(ma_url, ma_token, ha_username, auth_provider="ha")
        return {"step": "done", **_connected(ma_url, ha_username, "Connected to Music Assistant via Home Assistant.")}
    if result.get("type") == "form" and result.get("step_id") == "mfa":
        context = {
            "auth_mode": "ha_direct",
            "flow_id": flow_id,
            "ha_url": ha_url,
            "client_id": client_id,
            "state": "",
            "ma_url": ma_url,
            "username": username,
        }
        return _mfa_challenge(result, context)
    raise _flow_error(result)


def ha_login_mfa(ma_url: str, code: str, context: dict | None) -> dict:
    """HA OAuth sign-in to MA, step 2, with the server-side *context* from step 1."""
    ctx = context or {}
    ma_url = (ma_url or "").strip().rstrip("/")
    flow_id = str(ctx.get("flow_id") or "").strip()
    ha_url = str(ctx.get("ha_url") or "").strip().rstrip("/")
    client_id = str(ctx.get("client_id") or "").strip()
    oauth_state = str(ctx.get("state") or "").strip()
    auth_mode = str(ctx.get("auth_mode") or "ma_oauth").strip()
    context_ma_url = str(ctx.get("ma_url") or "").strip().rstrip("/")
    username = str(ctx.get("username") or "").strip()
    code = (code or "").replace(" ", "").replace("-", "")
    if not code:
        raise UseCaseError(400, "code_required", "Missing authentication code")
    expired = UseCaseError(400, "flow_expired", "Session expired — please start again")
    if not flow_id or not ha_url or (auth_mode != "ha_direct" and not oauth_state):
        raise expired
    if context_ma_url and context_ma_url != ma_url:
        raise expired
    result = _ha_login_flow_step(ha_url, flow_id, {"code": code}, client_id)
    if not result:
        raise UseCaseError(502, "ha_unavailable", "Authentication service unavailable")
    if result.get("type") == "create_entry":
        ha_code = result.get("result", "")
        if auth_mode == "ha_direct":
            auth_result, auth_error = _complete_ma_login_via_ha_code(ha_url, ha_code, client_id, ma_url)
            if not auth_result:
                raise UseCaseError(502, "authentication_failed", auth_error or "Authentication failed")
            ma_token = str(auth_result.get("ma_token") or "")
            saved_username = str(auth_result.get("name") or username or "HA user")
        else:
            ma_session_token = _ma_callback_exchange(ma_url, ha_code, oauth_state)
            if not ma_session_token:
                raise UseCaseError(500, "code_exchange_failed", "Failed to exchange HA code for MA token")
            ma_token = _exchange_for_long_lived_token(ma_url, ma_session_token)
            saved_username = username or "HA user"
        _save_ma_token_and_rediscover(ma_url, ma_token, saved_username, auth_provider="ha")
        return {
            "step": "done",
            **_connected(ma_url, saved_username, "Connected to Music Assistant via Home Assistant."),
        }
    if result.get("type") == "abort":
        raise expired
    raise UseCaseError(401, "invalid_code", "Invalid authentication code")
