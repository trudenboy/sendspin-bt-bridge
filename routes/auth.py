"""Auth blueprint for sendspin-bt-bridge.

Handles /login (GET/POST) and /logout.

Two authentication backends are supported:
  - HA addon mode  (SUPERVISOR_TOKEN env var present): validates via the
    Home Assistant Supervisor auth API using the user's HA credentials.
  - Standalone mode: compares against a PBKDF2-SHA256 password hash stored
    in config.json as AUTH_PASSWORD_HASH.

When AUTH_ENABLED is False (default) all requests bypass this entirely;
the before_request hook in web_interface.py is the gatekeeper.
"""

import json
import logging
import os
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


def _clear_failures(ip: str) -> None:
    with _failed_lock:
        _failed.pop(ip, None)


def _is_ha_addon() -> bool:
    """True when running as a Home Assistant addon (SUPERVISOR_TOKEN is set)."""
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


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
    try:
        body = json.dumps(data).encode()
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

    if request.method == "POST":
        if _check_rate_limit(client_ip):
            error = "Too many failed attempts — try again in 5 minutes"
            return render_template("login.html", error=error, ha_mode=ha_mode), 429

        config = load_config()
        if ha_mode:
            step = request.form.get("step", "credentials")

            if step == "mfa":
                # ── Step 2: submit TOTP / MFA code ──────────────────────────
                flow_id = request.form.get("flow_id", "").strip()
                mfa_module_id = request.form.get("mfa_module_id", "totp")
                # Normalize: remove spaces/dashes (common in copy-pasted TOTP codes)
                code = request.form.get("code", "").replace(" ", "").replace("-", "")
                # Missing flow_id means the session/flow expired — restart login
                if not flow_id:
                    error = "Session expired — please sign in again"
                    return render_template("login.html", error=error, ha_mode=ha_mode)
                # Missing code — keep user on the MFA step so they can retry
                if not code:
                    error = "Authentication code is required"
                    return render_template(
                        "login.html",
                        error=error,
                        ha_mode=ha_mode,
                        mfa_step=True,
                        flow_id=flow_id,
                        mfa_module_id=mfa_module_id,
                    )
                result = _ha_flow_step(flow_id, {"code": code, "mfa_module_id": mfa_module_id})
                if result and result.get("type") == "create_entry":
                    _clear_failures(client_ip)
                    session["authenticated"] = True
                    return redirect(_safe_next_url())
                _record_failure(client_ip)
                if result and result.get("type") == "abort":
                    error = "Session expired — please sign in again"
                    return render_template("login.html", error=error, ha_mode=ha_mode)
                error = "Invalid authentication code"
                return render_template(
                    "login.html",
                    error=error,
                    ha_mode=ha_mode,
                    mfa_step=True,
                    flow_id=flow_id,
                    mfa_module_id=mfa_module_id,
                )

            else:
                # ── Step 1: submit username + password ───────────────────────
                username = request.form.get("username", "").strip()
                password = request.form.get("password", "")

                # Try full HA login_flow (supports 2FA)
                flow = _ha_flow_start()
                if flow is None:
                    # Network-level failure — HA Core unreachable, Supervisor fallback is safe
                    logger.warning("HA login flow unavailable, falling back to Supervisor auth")
                    if _supervisor_auth(username, password):
                        _clear_failures(client_ip)
                        session["authenticated"] = True
                        return redirect(_safe_next_url())
                    _record_failure(client_ip)
                    error = "Invalid credentials"
                elif flow.get("_ha_error") or not flow.get("flow_id"):
                    # HA Core is up but returned an error, or gave no flow_id.
                    # Do NOT fall back to Supervisor — that would bypass MFA.
                    logger.error("HA login_flow service error (flow=%r)", flow)
                    error = "Authentication service unavailable"
                else:
                    flow_id = flow["flow_id"]
                    result = _ha_flow_step(flow_id, {"username": username, "password": password})
                    if result is None:
                        error = "Authentication service unavailable"
                    elif result.get("type") == "create_entry":
                        # Credentials valid, no 2FA configured
                        _clear_failures(client_ip)
                        session["authenticated"] = True
                        return redirect(_safe_next_url())
                    elif result.get("type") == "form" and result.get("step_id") == "mfa":
                        # 2FA required — extract module info from description_placeholders
                        placeholders = result.get("description_placeholders") or {}
                        mfa_module_id = placeholders.get("mfa_module_id", "totp")
                        return render_template(
                            "login.html",
                            ha_mode=ha_mode,
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

    return render_template("login.html", error=error, ha_mode=ha_mode)


@auth_bp.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("auth.login"))
