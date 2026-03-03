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
import urllib.request as _ur
from urllib.error import HTTPError
from urllib.parse import urlparse

from flask import Blueprint, redirect, render_template, request, session, url_for

from config import check_password, load_config

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


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

    if request.method == "POST":
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
                session["authenticated"] = True
                return redirect(_safe_next_url())
            except HTTPError:
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
                session["authenticated"] = True
                return redirect(_safe_next_url())
            else:
                error = "Invalid password"

    return render_template("login.html", error=error, ha_mode=ha_mode)


@auth_bp.route("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("auth.login"))
