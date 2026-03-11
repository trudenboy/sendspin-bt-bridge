"""Auth blueprint for sendspin-bt-bridge.

Handles /login (GET/POST) and /logout.

Three authentication backends are supported (auto-detected at login time):
  - **Music Assistant** — validates credentials via the MA HTTP API when the
    bridge is connected to MA (MA_API_URL + MA_API_TOKEN in config).
    Works for standalone MA and MA running as an HA addon.
  - **HA addon mode** (SUPERVISOR_TOKEN env var present): validates via the
    Home Assistant Supervisor auth API using the user's HA credentials.
    Supports 2FA/MFA via HA Core login_flow.
  - **Local password** — compares against a PBKDF2-SHA256 password hash
    stored in config.json as AUTH_PASSWORD_HASH.

When AUTH_ENABLED is False (default) all requests bypass this entirely;
the before_request hook in web_interface.py is the gatekeeper.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request as _ur
from urllib.error import HTTPError
from urllib.parse import urlparse

from flask import Blueprint, redirect, render_template, request, session, url_for

from config import check_password, load_config

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# HA Core URL used for the full auth login-flow (supports 2FA).
# In HA addon environment 'homeassistant' resolves to HA Core.
_HA_CORE_URL = os.environ.get("HA_CORE_URL", "http://homeassistant:8123").rstrip("/")
# client_id must be an HTTP URL; HA accepts any valid URL as client_id.
_FLOW_CLIENT_ID = f"{_HA_CORE_URL}/"

_FLOW_ID_RE = re.compile(r"[0-9a-f-]{32,36}", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Brute-force protection — in-memory, no external dependency
# ---------------------------------------------------------------------------

_LOCKOUT_MAX_ATTEMPTS = 5
_LOCKOUT_WINDOW_SECS = 60
_LOCKOUT_DURATION_SECS = 300  # 5 minutes

_failed: dict[str, tuple[int, float]] = {}  # ip → (count, first_failure_ts)
_failed_lock = threading.Lock()


def _check_rate_limit(ip: str) -> bool:
    """Return True if the IP is currently locked out."""
    now = time.monotonic()
    with _failed_lock:
        entry = _failed.get(ip)
        if not entry:
            return False
        count, first_ts = entry
        # Reset window if enough time has passed since first failure
        if now - first_ts > _LOCKOUT_DURATION_SECS:
            del _failed[ip]
            return False
        return count >= _LOCKOUT_MAX_ATTEMPTS


def _record_failure(ip: str) -> None:
    now = time.monotonic()
    with _failed_lock:
        entry = _failed.get(ip)
        if entry:
            count, first_ts = entry
            if now - first_ts > _LOCKOUT_WINDOW_SECS:
                _failed[ip] = (1, now)
            else:
                _failed[ip] = (count + 1, first_ts)
        else:
            _failed[ip] = (1, now)
        if len(_failed) > 1000:
            oldest = min(_failed, key=lambda k: _failed[k][1])
            del _failed[oldest]


def _clear_failures(ip: str) -> None:
    with _failed_lock:
        _failed.pop(ip, None)


def _is_ha_addon() -> bool:
    """True when running as a Home Assistant addon (SUPERVISOR_TOKEN is set)."""
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def _detect_auth_methods() -> list[str]:
    """Return list of available auth methods based on current config/runtime.

    Possible values: ``"ha_via_ma"``, ``"ma"``, ``"ha"``, ``"password"``.

    - ``"ha_via_ma"`` — MA connected and authenticated via HA; uses HA Core
      login_flow on the remote HA URL (supports 2FA).
    - ``"ma"`` — MA connected with builtin auth; simple username/password.
    - ``"ha"`` — running as HA addon (SUPERVISOR_TOKEN); uses local HA Core.
    - ``"password"`` — local PBKDF2 hash in config.json (always present).

    ``"password"`` is always included as the mandatory fallback.
    Other methods are optional and auto-detected from config/runtime.
    """
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

    # HA addon mode
    if _is_ha_addon():
        methods.append("ha")

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
    except Exception as exc:
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
        except Exception:
            logger.warning("Remote HA flow step HTTP %s (unparseable body)", exc.code)
            return None
    except Exception as exc:
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

    from routes.api_ma import _ma_http_login

    try:
        _ma_http_login(ma_url, username, password)
        return True, ""
    except RuntimeError as exc:
        return False, str(exc) if "invalid" in str(exc).lower() else "Invalid credentials"
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
    except Exception as exc:
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
        except Exception:
            logger.warning("HA flow step HTTP %s (unparseable body)", exc.code)
            return None
    except Exception as exc:
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
    except Exception as exc:
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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    ha_mode = _is_ha_addon()
    client_ip = request.remote_addr or "unknown"
    auth_methods = _detect_auth_methods()

    if request.method == "POST":
        if _check_rate_limit(client_ip):
            error = "Too many failed attempts — try again in 5 minutes"
            return render_template(
                "login.html",
                error=error,
                ha_mode=ha_mode,
                auth_methods=auth_methods,
            ), 429

        config = load_config()
        method = request.form.get("method", "").strip()

        # ── MA authentication ───────────────────────────────────────────
        if method == "ma":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            ok, err_msg = _ma_validate_credentials(username, password)
            if ok:
                _clear_failures(client_ip)
                session["authenticated"] = True
                return redirect(_safe_next_url())
            _record_failure(client_ip)
            error = err_msg or "Invalid credentials"

        # ── HA via MA (remote HA Core login_flow with 2FA) ──────────────
        elif method == "ha_via_ma":
            ha_url = _get_ha_core_url_from_ma()
            if not ha_url:
                error = "Music Assistant is not connected"
            else:
                step = request.form.get("step", "credentials")

                if step == "mfa":
                    flow_id = request.form.get("flow_id", "").strip()
                    mfa_module_id = request.form.get("mfa_module_id", "totp")
                    code = request.form.get("code", "").replace(" ", "").replace("-", "")
                    if not flow_id or not _FLOW_ID_RE.fullmatch(flow_id):
                        error = "Session expired — please sign in again"
                        return render_template(
                            "login.html",
                            error=error,
                            ha_mode=ha_mode,
                            auth_methods=auth_methods,
                        )
                    if not code:
                        error = "Authentication code is required"
                        return render_template(
                            "login.html",
                            error=error,
                            ha_mode=ha_mode,
                            auth_methods=auth_methods,
                            mfa_step=True,
                            flow_id=flow_id,
                            mfa_module_id=mfa_module_id,
                        )
                    result = _ha_remote_flow_step(ha_url, flow_id, {"code": code})
                    if result and result.get("type") == "create_entry":
                        _clear_failures(client_ip)
                        session["authenticated"] = True
                        return redirect(_safe_next_url())
                    _record_failure(client_ip)
                    if result and result.get("type") == "abort":
                        error = "Session expired — please sign in again"
                        return render_template(
                            "login.html",
                            error=error,
                            ha_mode=ha_mode,
                            auth_methods=auth_methods,
                        )
                    error = "Invalid authentication code"
                    return render_template(
                        "login.html",
                        error=error,
                        ha_mode=ha_mode,
                        auth_methods=auth_methods,
                        mfa_step=True,
                        flow_id=flow_id,
                        mfa_module_id=mfa_module_id,
                    )

                else:
                    username = request.form.get("username", "").strip()
                    password = request.form.get("password", "")

                    flow = _ha_remote_flow_start(ha_url)
                    if flow is None or (flow.get("_ha_error") or not flow.get("flow_id")):
                        error = "Home Assistant authentication service unavailable"
                    else:
                        flow_id = flow["flow_id"]
                        result = _ha_remote_flow_step(ha_url, flow_id, {"username": username, "password": password})
                        if result is None:
                            error = "Authentication service unavailable"
                        elif result.get("type") == "create_entry":
                            _clear_failures(client_ip)
                            session["authenticated"] = True
                            return redirect(_safe_next_url())
                        elif result.get("type") == "form" and result.get("step_id") == "mfa":
                            placeholders = result.get("description_placeholders") or {}
                            mfa_module_id = placeholders.get("mfa_module_id", "totp")
                            return render_template(
                                "login.html",
                                ha_mode=ha_mode,
                                auth_methods=auth_methods,
                                mfa_step=True,
                                flow_id=flow_id,
                                mfa_module_id=mfa_module_id,
                                mfa_module_name=placeholders.get("mfa_module_name", "Authenticator app"),
                                mfa_method="ha_via_ma",
                            )
                        else:
                            _record_failure(client_ip)
                            errors = result.get("errors", {})
                            error = (
                                "Invalid credentials"
                                if errors.get("base") == "invalid_auth"
                                else "Authentication failed"
                            )

        # ── HA addon authentication (with 2FA support) ──────────────────
        elif method == "ha" or (ha_mode and method not in ("ma", "password")):
            step = request.form.get("step", "credentials")

            if step == "mfa":
                # ── Step 2: submit TOTP / MFA code ──────────────────────
                flow_id = request.form.get("flow_id", "").strip()
                mfa_module_id = request.form.get("mfa_module_id", "totp")
                code = request.form.get("code", "").replace(" ", "").replace("-", "")
                if not flow_id or not _FLOW_ID_RE.fullmatch(flow_id):
                    error = "Session expired — please sign in again"
                    return render_template(
                        "login.html",
                        error=error,
                        ha_mode=ha_mode,
                        auth_methods=auth_methods,
                    )
                if not code:
                    error = "Authentication code is required"
                    return render_template(
                        "login.html",
                        error=error,
                        ha_mode=ha_mode,
                        auth_methods=auth_methods,
                        mfa_step=True,
                        flow_id=flow_id,
                        mfa_module_id=mfa_module_id,
                    )
                result = _ha_flow_step(flow_id, {"code": code})
                if result and result.get("type") == "create_entry":
                    _clear_failures(client_ip)
                    session["authenticated"] = True
                    return redirect(_safe_next_url())
                _record_failure(client_ip)
                if result and result.get("type") == "abort":
                    error = "Session expired — please sign in again"
                    return render_template(
                        "login.html",
                        error=error,
                        ha_mode=ha_mode,
                        auth_methods=auth_methods,
                    )
                error = "Invalid authentication code"
                return render_template(
                    "login.html",
                    error=error,
                    ha_mode=ha_mode,
                    auth_methods=auth_methods,
                    mfa_step=True,
                    flow_id=flow_id,
                    mfa_module_id=mfa_module_id,
                )

            else:
                # ── Step 1: submit username + password ──────────────────
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "")

                flow = _ha_flow_start()
                if flow is None:
                    logger.warning("HA login flow unavailable, falling back to Supervisor auth")
                    if _supervisor_auth(username, password):
                        _clear_failures(client_ip)
                        session["authenticated"] = True
                        return redirect(_safe_next_url())
                    _record_failure(client_ip)
                    error = "Invalid credentials"
                elif flow.get("_ha_error") or not flow.get("flow_id"):
                    logger.error("HA login_flow service error (flow=%r)", flow)
                    error = "Authentication service unavailable"
                else:
                    flow_id = flow["flow_id"]
                    result = _ha_flow_step(flow_id, {"username": username, "password": password})
                    if result is None:
                        error = "Authentication service unavailable"
                    elif result.get("type") == "create_entry":
                        _clear_failures(client_ip)
                        session["authenticated"] = True
                        return redirect(_safe_next_url())
                    elif result.get("type") == "form" and result.get("step_id") == "mfa":
                        placeholders = result.get("description_placeholders") or {}
                        mfa_module_id = placeholders.get("mfa_module_id", "totp")
                        return render_template(
                            "login.html",
                            ha_mode=ha_mode,
                            auth_methods=auth_methods,
                            mfa_step=True,
                            flow_id=flow_id,
                            mfa_module_id=mfa_module_id,
                            mfa_module_name=placeholders.get("mfa_module_name", "Authenticator app"),
                        )
                    else:
                        _record_failure(client_ip)
                        errors = result.get("errors", {})
                        error = (
                            "Invalid credentials" if errors.get("base") == "invalid_auth" else "Authentication failed"
                        )

        # ── Local password authentication ───────────────────────────────
        else:
            password = request.form.get("password", "")
            stored = config.get("AUTH_PASSWORD_HASH", "")
            if not stored:
                error = "No password configured — set one via the Configuration panel"
            elif check_password(password, stored):
                _clear_failures(client_ip)
                session["authenticated"] = True
                return redirect(_safe_next_url())
            else:
                _record_failure(client_ip)
                error = "Invalid password"

    return render_template(
        "login.html",
        error=error,
        ha_mode=ha_mode,
        auth_methods=auth_methods,
    )


@auth_bp.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("auth.login"))
