"""Auth blueprint for sendspin-bt-bridge.

Handles /login (GET/POST) and /logout.

Three authentication backends are supported (auto-detected at login time):
  - **HA addon mode** (SUPERVISOR_TOKEN env var present): validates via the
    Home Assistant Core login_flow using the user's HA credentials.
    Supports 2FA/MFA.  This is the *only* method offered in addon mode.
  - **Music Assistant** — validates credentials via the MA HTTP API when the
    bridge is connected to MA (MA_API_URL + MA_API_TOKEN in config).
    Works for standalone MA and MA running as an HA addon.
  - **Local password** — compares against a PBKDF2-SHA256 password hash
    stored in config.json as AUTH_PASSWORD_HASH.

In HA addon mode, auth is always enforced (no AUTH_ENABLED toggle).
In Docker/standalone mode, the AUTH_ENABLED flag controls auth enforcement.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.request as _ur
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from flask import Blueprint, Response, current_app, jsonify, redirect, render_template, request, session, url_for

from sendspin_bridge.config import check_password, load_config

if TYPE_CHECKING:
    from werkzeug.wrappers.response import Response as WerkzeugResponse

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# HA Core URL used for the full auth login-flow (supports 2FA).
# In HA addon environment 'homeassistant' resolves to HA Core.
_HA_CORE_URL = os.environ.get("HA_CORE_URL", "http://homeassistant:8123").rstrip("/")
# client_id must be an HTTP URL; HA accepts any valid URL as client_id.
_FLOW_CLIENT_ID = f"{_HA_CORE_URL}/"

_FLOW_ID_RE = re.compile(r"[0-9a-f-]{32,36}", re.IGNORECASE)

# ---------------------------------------------------------------------------
# CRITICAL: Authentication — brute-force protection. In-memory tracking resets
# on restart. Session-based client ID can be bypassed by clearing cookies.
# ---------------------------------------------------------------------------

_LOCKOUT_MAX_ATTEMPTS = 5
_LOCKOUT_WINDOW_SECS = 60
_LOCKOUT_DURATION_SECS = 300  # 5 minutes

#: Default proxy / supervisor network IPs the bridge implicitly trusts.
#:
#: * ``127.0.0.1`` / ``::1`` — local loopback (standalone Docker / LXC).
#: * ``172.30.32.1`` — the **HA Core container** on the Supervisor
#:   ``hassio`` Docker network.  Required so the HACS custom_component's
#:   one-shot Supervisor-pair flow (``POST /api/auth/ha-pair``) actually
#:   reaches us — the call originates from HA Core, not from Supervisor.
#: * ``172.30.32.2`` — the Supervisor container itself.
#:
#: Operators can extend this set via ``TRUSTED_PROXIES`` in the bridge
#: config when their deployment uses a different proxy chain.
_TRUSTED_PROXY_DEFAULTS = frozenset({"127.0.0.1", "::1", "172.30.32.1", "172.30.32.2"})

_failed: dict[str, tuple[int, float]] = {}  # client_id → (count, first_failure_ts)
_failed_lock = threading.Lock()


def _coerce_int_setting(value, default: int, min_value: int, max_value: int) -> int:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return default
    return min(max_value, max(min_value, coerced))


def _get_lockout_settings() -> tuple[bool, int, int, int]:
    config = load_config()
    enabled = bool(config.get("BRUTE_FORCE_PROTECTION", True))
    max_attempts = _coerce_int_setting(
        config.get("BRUTE_FORCE_MAX_ATTEMPTS", _LOCKOUT_MAX_ATTEMPTS),
        _LOCKOUT_MAX_ATTEMPTS,
        1,
        50,
    )
    window_secs = (
        _coerce_int_setting(
            config.get("BRUTE_FORCE_WINDOW_MINUTES", max(1, _LOCKOUT_WINDOW_SECS // 60)),
            max(1, _LOCKOUT_WINDOW_SECS // 60),
            1,
            1440,
        )
        * 60
    )
    duration_secs = (
        _coerce_int_setting(
            config.get("BRUTE_FORCE_LOCKOUT_MINUTES", max(1, _LOCKOUT_DURATION_SECS // 60)),
            max(1, _LOCKOUT_DURATION_SECS // 60),
            1,
            1440,
        )
        * 60
    )
    return enabled, max_attempts, window_secs, duration_secs


def _format_duration(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{seconds} seconds"


def _get_trusted_proxies() -> set[str]:
    """Return trusted proxy IPs used for validated forwarded client identity."""
    trusted = set(_TRUSTED_PROXY_DEFAULTS)
    config = load_config()
    extra = config.get("TRUSTED_PROXIES") or []
    if isinstance(extra, list):
        trusted.update(value.strip() for value in extra if isinstance(value, str) and value.strip())
    return trusted


def _get_forwarded_client_ip() -> str:
    """Return the rightmost untrusted hop from X-Forwarded-For.

    Only the proxies we trust (see ``_get_trusted_proxies``) are allowed to
    append their own addresses to XFF.  The real client is therefore the
    *rightmost* hop that is NOT a trusted proxy.  Using the leftmost hop is
    spoofable by any client that sets an XFF header themselves.
    """
    trusted = _get_trusted_proxies()
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        hops = [p.strip() for p in forwarded_for.split(",") if p.strip()]
        for hop in reversed(hops):
            if hop not in trusted:
                return hop
    return request.headers.get("X-Real-IP", "").strip()


def _get_rate_limit_client_id() -> str:
    """Return the best-available brute-force bucket key for this request."""
    peer = (request.remote_addr or "").strip()
    if peer and peer in _get_trusted_proxies():
        forwarded_ip = _get_forwarded_client_ip()
        if forwarded_ip:
            return forwarded_ip
        username = request.form.get("username", "").strip().casefold()
        if username:
            return f"proxy-login:{username}"
        session_client_id = session.get("_lockout_client_id")
        if not isinstance(session_client_id, str) or not session_client_id:
            session_client_id = secrets.token_hex(16)
            session["_lockout_client_id"] = session_client_id
        return f"proxy-session:{session_client_id}"
    return peer or "unknown"


def _check_rate_limit(client_id: str) -> bool:
    """Return True if the client identifier is currently locked out."""
    enabled, max_attempts, _, duration_secs = _get_lockout_settings()
    if not enabled:
        return False
    now = time.monotonic()
    with _failed_lock:
        entry = _failed.get(client_id)
        if not entry:
            return False
        count, first_ts = entry
        # Reset window if enough time has passed since first failure
        if now - first_ts > duration_secs:
            del _failed[client_id]
            return False
        return count >= max_attempts


def _record_failure(client_id: str) -> None:
    enabled, _, window_secs, _ = _get_lockout_settings()
    if not enabled:
        return
    now = time.monotonic()
    with _failed_lock:
        entry = _failed.get(client_id)
        if entry:
            count, first_ts = entry
            if now - first_ts > window_secs:
                _failed[client_id] = (1, now)
            else:
                _failed[client_id] = (count + 1, first_ts)
        else:
            _failed[client_id] = (1, now)
        # Periodic sweep: remove expired entries when dict grows large
        if len(_failed) > 200:
            _, _, sweep_window, duration = _get_lockout_settings()
            max_age = max(sweep_window, duration)
            expired = [k for k, (_, ts) in _failed.items() if now - ts > max_age]
            for k in expired:
                del _failed[k]
            # Hard cap as safety net
            if len(_failed) > 1000:
                oldest = min(_failed, key=lambda k: _failed[k][1])
                del _failed[oldest]


def _clear_failures(client_id: str) -> None:
    with _failed_lock:
        _failed.pop(client_id, None)


def _generate_csrf_token() -> str:
    """Generate or retrieve CSRF token for the current session."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def _validate_csrf_token() -> bool:
    """Validate CSRF token from form, JSON body, or X-CSRF-Token header.

    JSON-content-type endpoints (e.g. ``POST /api/auth/tokens``) cannot
    POST a form body, so they pass the same token through either the
    JSON envelope or an explicit ``X-CSRF-Token`` header.

    When the global auth gate is OFF, no session exists, so no CSRF
    token can be issued — treat the request as valid.  This mirrors
    ``_require_authenticated_session`` and lets the Settings → Home
    Assistant tab issue tokens on Docker / standalone deployments
    without auth.  Reads the gate via ``current_app.config`` (mirrors
    the pattern in ``routes/views.py`` / ``routes/api_status.py``)
    instead of importing ``web_interface`` directly.
    """
    if not current_app.config.get("AUTH_ENABLED", False):
        return True
    session_token = session.get("csrf_token", "")
    if not session_token:
        return False
    presented = request.form.get("csrf_token", "")
    if not presented:
        try:
            payload = request.get_json(silent=True) or {}
            if isinstance(payload, dict):
                presented = str(payload.get("csrf_token") or "")
        except Exception:
            presented = ""
    if not presented:
        presented = request.headers.get("X-CSRF-Token", "")
    if not presented:
        return False
    return hmac.compare_digest(presented, session_token)


def _is_ha_addon() -> bool:
    """True when running as a Home Assistant addon (SUPERVISOR_TOKEN is set)."""
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def _detect_auth_methods() -> list[str]:
    """Return list of available auth methods based on current config/runtime.

    Possible values: ``"ha_via_ma"``, ``"ma"``, ``"ha"``, ``"password"``.

    In HA addon mode only ``"ha"`` is returned (HA Core login_flow with 2FA).
    In standalone/Docker mode all available methods are auto-detected, with
    ``"password"`` always included as the mandatory fallback.
    """
    # HA addon mode — only HA Core auth (supports 2FA)
    if _is_ha_addon():
        return ["ha"]

    methods: list[str] = []
    config = load_config()

    # MA auth available when bridge is connected (has URL + token)
    ma_url = config.get("MA_API_URL", "")
    ma_token = config.get("MA_API_TOKEN", "")
    if ma_url and ma_token:
        if config.get("MA_AUTH_PROVIDER") == "ha":
            methods.append("ha_via_ma")
        else:
            methods.append("ma")

    # Local password — always present as mandatory fallback
    methods.append("password")

    return methods


def _get_ha_core_url_from_ma() -> str | None:
    """Derive HA Core URL from the MA API URL.

    MA as HA addon runs on the same host, port 8123 is HA Core.
    """
    config = load_config()
    ma_url = config.get("MA_API_URL", "")
    if not ma_url:
        return None
    parsed = urlparse(ma_url)
    return f"{parsed.scheme}://{parsed.hostname}:8123"


def _ha_remote_flow_start(ha_url: str) -> dict | None:
    """Start HA Core login_flow on a remote HA instance."""
    client_id = f"{ha_url}/"
    try:
        body = json.dumps(
            {
                "client_id": client_id,
                "handler": ["homeassistant", None],
                "redirect_uri": client_id,
            }
        ).encode()
        req = _ur.Request(
            f"{ha_url}/auth/login_flow",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.debug("Remote HA login_flow started: flow_id=%s", result.get("flow_id"))
            return result
    except HTTPError as exc:
        logger.warning("Remote HA login_flow HTTP %s", exc.code)
        return {"_ha_error": True}
    except (URLError, OSError, ValueError) as exc:
        logger.warning("Remote HA login_flow unreachable: %s", exc)
        return None


def _ha_remote_flow_step(ha_url: str, flow_id: str, data: dict) -> dict | None:
    """Submit a step to a remote HA Core login_flow."""
    if not _FLOW_ID_RE.fullmatch(flow_id or ""):
        logger.warning("Invalid flow_id rejected: %s", flow_id)
        return None
    client_id = f"{ha_url}/"
    try:
        payload = {"client_id": client_id, **data}
        body = json.dumps(payload).encode()
        req = _ur.Request(
            f"{ha_url}/auth/login_flow/{flow_id}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.debug("Remote HA flow step result: %s", result)
            return result
    except HTTPError as exc:
        try:
            body = exc.read()
            result = json.loads(body)
            logger.warning("Remote HA flow step HTTP %s: %s", exc.code, result)
            return result
        except (json.JSONDecodeError, ValueError):
            logger.warning("Remote HA flow step HTTP %s (unparseable body)", exc.code)
            return None
    except (URLError, OSError, ValueError) as exc:
        logger.warning("Remote HA login flow step error: %s", exc)
        return None


def _ma_validate_credentials(username: str, password: str) -> tuple[bool, str]:
    """Validate credentials against connected Music Assistant server.

    Returns (success, error_message).
    """
    config = load_config()
    ma_url = config.get("MA_API_URL", "")
    if not ma_url:
        return False, "Music Assistant is not connected"

    from sendspin_bridge.web.routes.ma_auth import _ma_http_login

    try:
        _ma_http_login(ma_url, username, password)
        return True, ""
    except RuntimeError as exc:
        logger.warning("MA auth failed: %s", exc)
        return False, "Invalid credentials"
    except (ConnectionError, OSError) as exc:
        logger.warning("MA auth failed (network): %s", exc)
        return False, "Music Assistant server is unreachable"
    except Exception as exc:
        logger.warning("MA auth unexpected error: %s", exc)
        return False, "Authentication service error"


def _ha_flow_start() -> dict | None:
    """Start an HA Core auth login_flow.

    Returns:
        dict  — success (contains ``flow_id``)
        {"_ha_error": True} — HA Core is reachable but returned an HTTP error;
            caller MUST NOT fall back to Supervisor /auth (would bypass MFA).
        None  — network-level failure (connection refused, DNS, timeout);
            HA Core is unreachable, falling back to Supervisor /auth is safe.
    """
    try:
        body = json.dumps(
            {
                "client_id": _FLOW_CLIENT_ID,
                "handler": ["homeassistant", None],
                "redirect_uri": _FLOW_CLIENT_ID,
            }
        ).encode()
        req = _ur.Request(
            f"{_HA_CORE_URL}/auth/login_flow",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.debug("HA login_flow started: flow_id=%s", result.get("flow_id"))
            return result
    except HTTPError as exc:
        # HA Core is reachable but returned an error (e.g. 404/500).
        # Do NOT fall back to Supervisor /auth — that would bypass MFA.
        logger.warning("HA login_flow HTTP %s — service error, MFA bypass prevented", exc.code)
        return {"_ha_error": True}
    except (URLError, OSError, ValueError) as exc:
        # Network-level failure (DNS, connection refused, timeout).
        # HA Core is genuinely unreachable — Supervisor fallback is safe.
        logger.warning("HA login flow unreachable: %s", exc)
        return None


def _ha_flow_step(flow_id: str, data: dict) -> dict | None:
    """Submit a step to an HA Core auth login_flow."""
    if not _FLOW_ID_RE.fullmatch(flow_id or ""):
        logger.warning("Invalid flow_id rejected: %s", flow_id)
        return None
    try:
        payload = {"client_id": _FLOW_CLIENT_ID, **data}
        body = json.dumps(payload).encode()
        req = _ur.Request(
            f"{_HA_CORE_URL}/auth/login_flow/{flow_id}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            logger.debug("HA flow step result: %s", result)
            return result
    except HTTPError as exc:
        try:
            body = exc.read()
            result = json.loads(body)
            logger.warning("HA flow step HTTP %s: %s", exc.code, result)
            return result
        except (json.JSONDecodeError, ValueError):
            logger.warning("HA flow step HTTP %s (unparseable body)", exc.code)
            return None
    except (URLError, OSError, ValueError) as exc:
        logger.warning("HA login flow step error: %s", exc)
        return None


def _supervisor_auth(username: str, password: str) -> bool:
    """Validate credentials via Supervisor /auth (bypasses 2FA, fallback only)."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    try:
        body = json.dumps({"username": username, "password": password}).encode()
        req = _ur.Request(
            "http://supervisor/auth",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _ur.urlopen(req, timeout=10)
        return True
    except HTTPError:
        return False
    except (URLError, OSError) as exc:
        logger.warning("HA supervisor auth fallback error: %s", exc)
        return False


def _safe_next_url() -> str:
    """Return a validated local redirect target from the ``next`` query param."""
    target = request.args.get("next", "/")
    parsed = urlparse(target)
    # Only allow local paths (no scheme, no netloc, single leading /)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


def _handle_ma_login(
    client_ip: str,
) -> tuple[str | None, Response | WerkzeugResponse | str | None]:
    """Handle ``method == "ma"`` — Music Assistant credential validation."""
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    ok, err_msg = _ma_validate_credentials(username, password)
    if ok:
        _clear_failures(client_ip)
        session.clear()
        session["authenticated"] = True
        session["ha_user"] = username
        return None, redirect(_safe_next_url())
    _record_failure(client_ip)
    return err_msg or "Invalid credentials", None


def _handle_ha_via_ma_login(
    client_ip: str,
    ha_mode: bool,
    auth_methods: list[str],
) -> tuple[str | None, Response | WerkzeugResponse | str | None]:
    """Handle ``method == "ha_via_ma"`` — remote HA Core login_flow with 2FA."""
    ha_url = _get_ha_core_url_from_ma()
    if not ha_url:
        return "Music Assistant is not connected", None

    step = request.form.get("step", "credentials")

    if step == "mfa":
        flow_id = request.form.get("flow_id", "").strip()
        mfa_module_id = request.form.get("mfa_module_id", "totp")
        code = request.form.get("code", "").replace(" ", "").replace("-", "")
        if not flow_id or not _FLOW_ID_RE.fullmatch(flow_id):
            return None, render_template(
                "login.html",
                error="Session expired — please sign in again",
                ha_mode=ha_mode,
                auth_methods=auth_methods,
                csrf_token=_generate_csrf_token(),
            )
        if not code:
            return None, render_template(
                "login.html",
                error="Authentication code is required",
                ha_mode=ha_mode,
                auth_methods=auth_methods,
                mfa_step=True,
                flow_id=flow_id,
                mfa_module_id=mfa_module_id,
                csrf_token=_generate_csrf_token(),
            )
        result = _ha_remote_flow_step(ha_url, flow_id, {"code": code})
        if result and result.get("type") == "create_entry":
            _clear_failures(client_ip)
            ha_user = session.get("_ha_login_user", "")
            session.clear()
            session["authenticated"] = True
            session["ha_user"] = ha_user
            return None, redirect(_safe_next_url())
        _record_failure(client_ip)
        if result and result.get("type") == "abort":
            return None, render_template(
                "login.html",
                error="Session expired — please sign in again",
                ha_mode=ha_mode,
                auth_methods=auth_methods,
                csrf_token=_generate_csrf_token(),
            )
        return None, render_template(
            "login.html",
            error="Invalid authentication code",
            ha_mode=ha_mode,
            auth_methods=auth_methods,
            mfa_step=True,
            flow_id=flow_id,
            mfa_module_id=mfa_module_id,
            csrf_token=_generate_csrf_token(),
        )

    # Step 1: submit username + password
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    flow = _ha_remote_flow_start(ha_url)
    if flow is None or (flow.get("_ha_error") or not flow.get("flow_id")):
        return "Home Assistant authentication service unavailable", None

    flow_id = flow["flow_id"]
    result = _ha_remote_flow_step(ha_url, flow_id, {"username": username, "password": password})
    if result is None:
        return "Authentication service unavailable", None
    if result.get("type") == "create_entry":
        _clear_failures(client_ip)
        session.clear()
        session["authenticated"] = True
        session["ha_user"] = username
        return None, redirect(_safe_next_url())
    if result.get("type") == "form" and result.get("step_id") == "mfa":
        session["_ha_login_user"] = username
        placeholders = result.get("description_placeholders") or {}
        mfa_module_id = placeholders.get("mfa_module_id", "totp")
        return None, render_template(
            "login.html",
            ha_mode=ha_mode,
            auth_methods=auth_methods,
            mfa_step=True,
            flow_id=flow_id,
            mfa_module_id=mfa_module_id,
            mfa_module_name=placeholders.get("mfa_module_name", "Authenticator app"),
            mfa_method="ha_via_ma",
            csrf_token=_generate_csrf_token(),
        )

    _record_failure(client_ip)
    errors = result.get("errors", {})
    error = "Invalid credentials" if errors.get("base") == "invalid_auth" else "Authentication failed"
    return error, None


def _handle_ha_direct_login(
    client_ip: str,
    ha_mode: bool,
    auth_methods: list[str],
) -> tuple[str | None, Response | WerkzeugResponse | str | None]:
    """Handle ``method == "ha"`` — HA addon authentication with 2FA support."""
    step = request.form.get("step", "credentials")

    if step == "mfa":
        # Step 2: submit TOTP / MFA code
        flow_id = request.form.get("flow_id", "").strip()
        mfa_module_id = request.form.get("mfa_module_id", "totp")
        code = request.form.get("code", "").replace(" ", "").replace("-", "")
        if not flow_id or not _FLOW_ID_RE.fullmatch(flow_id):
            return None, render_template(
                "login.html",
                error="Session expired — please sign in again",
                ha_mode=ha_mode,
                auth_methods=auth_methods,
                csrf_token=_generate_csrf_token(),
            )
        if not code:
            return None, render_template(
                "login.html",
                error="Authentication code is required",
                ha_mode=ha_mode,
                auth_methods=auth_methods,
                mfa_step=True,
                flow_id=flow_id,
                mfa_module_id=mfa_module_id,
                csrf_token=_generate_csrf_token(),
            )
        result = _ha_flow_step(flow_id, {"code": code})
        if result and result.get("type") == "create_entry":
            _clear_failures(client_ip)
            ha_user = session.get("_ha_login_user", "")
            session.clear()
            session["authenticated"] = True
            session["ha_user"] = ha_user
            return None, redirect(_safe_next_url())
        _record_failure(client_ip)
        if result and result.get("type") == "abort":
            return None, render_template(
                "login.html",
                error="Session expired — please sign in again",
                ha_mode=ha_mode,
                auth_methods=auth_methods,
                csrf_token=_generate_csrf_token(),
            )
        return None, render_template(
            "login.html",
            error="Invalid authentication code",
            ha_mode=ha_mode,
            auth_methods=auth_methods,
            mfa_step=True,
            flow_id=flow_id,
            mfa_module_id=mfa_module_id,
            csrf_token=_generate_csrf_token(),
        )

    # Step 1: submit username + password
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    flow = _ha_flow_start()
    if flow is None:
        if os.environ.get("ALLOW_SUPERVISOR_FALLBACK") == "1":
            logger.warning(
                "HA login_flow unreachable — falling back to Supervisor auth "
                "(ALLOW_SUPERVISOR_FALLBACK=1). This path does NOT verify MFA."
            )
            if _supervisor_auth(username, password):
                _clear_failures(client_ip)
                bucket = session.get("_lockout_client_id")
                session.clear()
                if bucket:
                    session["_lockout_client_id"] = bucket
                session["authenticated"] = True
                session["ha_user"] = username
                # ``auth_method`` is overwritten by the outer ``login()`` with
                # the form-submitted method; use a distinct key so the
                # fallback marker survives.
                session["auth_fallback_used"] = True
                return None, redirect(_safe_next_url())
            _record_failure(client_ip)
            return "Invalid credentials", None
        logger.error("HA login_flow unreachable; refusing Supervisor fallback (MFA cannot be verified)")
        return "Authentication service unavailable", None
    if flow.get("_ha_error") or not flow.get("flow_id"):
        logger.error("HA login_flow service error (flow=%r)", flow)
        return "Authentication service unavailable", None

    flow_id = flow["flow_id"]
    result = _ha_flow_step(flow_id, {"username": username, "password": password})
    if result is None:
        return "Authentication service unavailable", None
    if result.get("type") == "create_entry":
        _clear_failures(client_ip)
        session.clear()
        session["authenticated"] = True
        session["ha_user"] = username
        return None, redirect(_safe_next_url())
    if result.get("type") == "form" and result.get("step_id") == "mfa":
        session["_ha_login_user"] = username
        placeholders = result.get("description_placeholders") or {}
        mfa_module_id = placeholders.get("mfa_module_id", "totp")
        return None, render_template(
            "login.html",
            ha_mode=ha_mode,
            auth_methods=auth_methods,
            mfa_step=True,
            flow_id=flow_id,
            mfa_module_id=mfa_module_id,
            mfa_module_name=placeholders.get("mfa_module_name", "Authenticator app"),
            csrf_token=_generate_csrf_token(),
        )

    _record_failure(client_ip)
    errors = result.get("errors", {})
    error = "Invalid credentials" if errors.get("base") == "invalid_auth" else "Authentication failed"
    return error, None


def _handle_local_password_login(
    client_ip: str,
) -> tuple[str | None, Response | WerkzeugResponse | str | None]:
    """Handle local password authentication via ``check_password``."""
    config = load_config()
    password = request.form.get("password", "")
    stored = config.get("AUTH_PASSWORD_HASH", "")
    if not stored:
        return "No password configured — set one via the Configuration panel", None
    if check_password(password, stored):
        _clear_failures(client_ip)
        session.clear()
        session["authenticated"] = True
        return None, redirect(_safe_next_url())
    _record_failure(client_ip)
    return "Invalid password", None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    ha_mode = _is_ha_addon()
    auth_methods = _detect_auth_methods()

    # Clear stale MFA username from abandoned login flows
    if request.method == "GET":
        session.pop("_ha_login_user", None)

    if request.method != "POST":
        return render_template(
            "login.html", error=None, ha_mode=ha_mode, auth_methods=auth_methods, csrf_token=_generate_csrf_token()
        )

    # CSRF validation before processing any credentials
    if not _validate_csrf_token():
        return render_template(
            "login.html",
            error="Invalid session. Please try again.",
            ha_mode=ha_mode,
            auth_methods=auth_methods,
            csrf_token=_generate_csrf_token(),
        ), 403

    client_id = _get_rate_limit_client_id()
    if _check_rate_limit(client_id):
        _, _, _, duration_secs = _get_lockout_settings()
        return render_template(
            "login.html",
            error=f"Too many failed attempts — try again in {_format_duration(duration_secs)}",
            ha_mode=ha_mode,
            auth_methods=auth_methods,
            csrf_token=_generate_csrf_token(),
        ), 429

    method = request.form.get("method", "").strip()
    error: str | None = None
    response = None

    if method == "ma":
        error, response = _handle_ma_login(client_id)
    elif method == "ha_via_ma":
        error, response = _handle_ha_via_ma_login(client_id, ha_mode, auth_methods)
    elif method == "ha" or (ha_mode and method not in ("ma", "password")):
        error, response = _handle_ha_direct_login(client_id, ha_mode, auth_methods)
    else:
        error, response = _handle_local_password_login(client_id)

    if response is not None:
        session["auth_method"] = method
        return response

    return render_template(
        "login.html",
        error=error,
        ha_mode=ha_mode,
        auth_methods=auth_methods,
        csrf_token=_generate_csrf_token(),
    )


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "GET":
        return (
            render_template(
                "logout_method.html",
                login_url=url_for("auth.login"),
            ),
            405,
        )
    if not _validate_csrf_token():
        return jsonify({"error": "Invalid CSRF token"}), 403
    bucket = session.get("_lockout_client_id")
    session.clear()
    if bucket:
        session["_lockout_client_id"] = bucket
    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Long-lived API bearer tokens (v2.65.0+)
# ---------------------------------------------------------------------------
#
# Tokens issued via these endpoints are used by the HA custom_component to
# authenticate REST/SSE calls without piggy-backing on a session cookie.
# Endpoints require an authenticated session (or, for the Supervisor pair
# variant, a valid SUPERVISOR_TOKEN that round-trips through Supervisor's
# /auth endpoint).


def _require_authenticated_session() -> tuple[Response, int] | None:
    """Reject API token endpoints unless the caller has a UI session.

    Used as the bearer for `/api/auth/tokens` issuance — we don't allow a
    token to mint another token, only the operator behind the web UI does.

    When the global auth gate is OFF (Docker / standalone mode without
    AUTH_ENABLED, no SUPERVISOR_TOKEN) sessions are never created in the
    first place; treat those callers as authorised because the rest of
    ``/api/*`` is already wide-open on this deployment.  Otherwise the
    Settings → Home Assistant tab would 401 on load and bounce the
    browser to ``/login`` even though auth is disabled (issue from
    v2.65.0-rc.2 deployment).  Reads the gate via ``current_app.config``
    (mirrors ``routes/views.py`` / ``routes/api_status.py``) so the
    blueprint stays decoupled from the ``web_interface`` module.
    """
    if session.get("authenticated"):
        return None
    if not current_app.config.get("AUTH_ENABLED", False):
        return None
    return jsonify({"error": "Unauthorized"}), 401


@auth_bp.route("/api/auth/tokens", methods=["GET"])
def api_auth_tokens_list():
    """List long-lived bearer tokens (no plaintext, no hashes)."""
    blocked = _require_authenticated_session()
    if blocked:
        return blocked
    from sendspin_bridge.services.diagnostics.auth_tokens import list_tokens

    return jsonify({"success": True, "tokens": [t.to_public_dict() for t in list_tokens()]})


@auth_bp.route("/api/auth/tokens", methods=["POST"])
def api_auth_tokens_create():
    """Mint a new bearer token; the plaintext is returned ONCE."""
    blocked = _require_authenticated_session()
    if blocked:
        return blocked
    if not _validate_csrf_token():
        return jsonify({"error": "Invalid CSRF token"}), 403
    data = request.get_json(silent=True) or {}
    label = str(data.get("label") or "ha-custom-component").strip()[:64]
    if not label:
        return jsonify({"success": False, "error": "label required"}), 400

    from sendspin_bridge.services.diagnostics.auth_tokens import issue_token

    plain, record = issue_token(label)
    return jsonify(
        {
            "success": True,
            "token": plain,  # the only time the plaintext is ever sent
            "record": record.to_public_dict(),
        }
    )


@auth_bp.route("/api/auth/tokens/<token_id>", methods=["DELETE"])
def api_auth_tokens_delete(token_id: str):
    """Revoke a bearer token by id."""
    blocked = _require_authenticated_session()
    if blocked:
        return blocked
    from sendspin_bridge.services.diagnostics.auth_tokens import revoke_token

    removed = revoke_token(token_id)
    if not removed:
        return jsonify({"success": False, "error": "Unknown token id"}), 404
    return jsonify({"success": True, "id": token_id})


@auth_bp.route("/api/auth/ha-pair", methods=["POST"])
def api_auth_ha_pair():
    """One-shot pairing flow for HA custom_component on HAOS.

    The custom_component invokes this endpoint *from the HA Supervisor
    network*; the Supervisor proxies our /api/* paths through its ingress
    so the request lands here with the caller's HA Core identity already
    present in headers (X-Remote-User-Display-Name).  We mint a bearer
    token labelled ``ha-custom-component`` and return it once.

    Threat model: an attacker who can reach this endpoint from outside
    the Supervisor network must NOT receive a token.  We trust the
    Supervisor proxy chain by checking the immediate peer is in
    ``_TRUSTED_PROXIES`` (same set used by ``_check_auth``) AND that the
    request carries an ``X-Ingress-Path`` header — only Supervisor injects
    that header.
    """
    peer = (request.remote_addr or "").strip()
    if peer not in _TRUSTED_PROXY_DEFAULTS:
        # Allow the operator-configurable proxy list as well so tests /
        # split deployments work.
        try:
            cfg = load_config()
            extra = cfg.get("TRUSTED_PROXIES") or []
            if peer not in {str(p).strip() for p in extra}:
                return jsonify({"success": False, "error": "Not allowed from this network"}), 403
        except Exception:
            return jsonify({"success": False, "error": "Not allowed from this network"}), 403

    if not request.headers.get("X-Ingress-Path"):
        return jsonify({"success": False, "error": "Supervisor ingress required"}), 403

    from sendspin_bridge.services.diagnostics.auth_tokens import issue_token

    plain, record = issue_token("ha-custom-component")
    return jsonify(
        {
            "success": True,
            "token": plain,
            "record": record.to_public_dict(),
        }
    )
