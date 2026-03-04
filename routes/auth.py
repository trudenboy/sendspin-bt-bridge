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
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
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
                _clear_failures(client_ip)
                session["authenticated"] = True
                return redirect(_safe_next_url())
            except HTTPError:
                _record_failure(client_ip)
                error = "Invalid credentials"
            except Exception as exc:
                logger.warning("HA supervisor auth error: %s", exc)
                error = "Authentication service unavailable"
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
