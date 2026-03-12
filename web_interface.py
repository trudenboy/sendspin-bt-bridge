#!/usr/bin/env python3
"""
Web Interface for Sendspin Client
Provides configuration and monitoring UI.

This module is intentionally slim: Flask app initialisation, WSGI middleware,
blueprint registration, and the main() entry-point.  All route handlers live in
routes/api.py and routes/views.py; shared helpers live in config.py and state.py.
"""

import logging
import os
from datetime import timedelta
from typing import Optional

from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from waitress import serve  # type: ignore[import-untyped]

from config import VERSION, ensure_secret_key, load_config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

# Set secret key (generated once and persisted to config.json so sessions
# survive container restarts).
_startup_config = load_config()
app.secret_key = ensure_secret_key(_startup_config)

# Apply configured log level to root logger
_startup_log_level = _startup_config.get("LOG_LEVEL", "INFO").upper()
if _startup_log_level not in ("INFO", "DEBUG"):
    _startup_log_level = "INFO"
logging.getLogger().setLevel(getattr(logging, _startup_log_level))

# Harden session cookies: SameSite=Lax prevents cross-site request forgery
# (all POST endpoints also use request.get_json() which rejects form-encoded
# bodies, providing defence-in-depth).  HttpOnly prevents JS cookie access.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)

# Cache AUTH_ENABLED at startup so _check_auth() never reads config.json
# on every request.  Like all other settings, a change takes effect after
# the service is restarted (same behaviour as SENDSPIN_SERVER, etc.).
_is_ha_addon = bool(os.environ.get("SUPERVISOR_TOKEN"))
# In HA addon mode, auth is always enforced (users log in via HA credentials).
# In Docker/standalone mode, the AUTH_ENABLED toggle controls auth.
_auth_enabled: bool = _is_ha_addon or bool(_startup_config.get("AUTH_ENABLED", False))
app.config["AUTH_ENABLED"] = _auth_enabled
app.config["IS_HA_ADDON"] = _is_ha_addon
if _auth_enabled:
    if _is_ha_addon:
        logger.info("Web UI password protection is enabled (HA addon — always on)")
    else:
        logger.info("Web UI password protection is enabled (restart required to change)")


_TRUSTED_PROXIES = {"127.0.0.1", "::1", "172.30.32.2"}
_extra = _startup_config.get("TRUSTED_PROXIES") or []
if isinstance(_extra, list):
    _TRUSTED_PROXIES |= set(_extra)


class _IngressMiddleware:
    """WSGI middleware: sets SCRIPT_NAME from X-Ingress-Path header before Flask
    creates its URL adapter, so that url_for() correctly prefixes all URLs.

    Only honors the header from trusted HA Supervisor proxy addresses and
    validates the value is a safe absolute path (single leading ``/``).
    """

    def __init__(self, wsgi_app):
        self._app = wsgi_app

    def __call__(self, environ, start_response):
        peer = environ.get("REMOTE_ADDR", "")
        if peer in _TRUSTED_PROXIES:
            ingress_path = environ.get("HTTP_X_INGRESS_PATH", "").rstrip("/")
            # Only accept a single-leading-slash absolute path (no //, no scheme)
            if ingress_path and ingress_path.startswith("/") and not ingress_path.startswith("//"):
                environ["SCRIPT_NAME"] = ingress_path
        return self._app(environ, start_response)


app.wsgi_app = _IngressMiddleware(app.wsgi_app)

# Register blueprints (imported after app is created to avoid circular imports)
from routes.api import api_bp  # noqa: E402
from routes.api_bt import bt_bp  # noqa: E402
from routes.api_config import config_bp  # noqa: E402
from routes.api_ma import ma_bp  # noqa: E402
from routes.api_status import status_bp  # noqa: E402
from routes.auth import auth_bp  # noqa: E402
from routes.views import views_bp  # noqa: E402

app.register_blueprint(views_bp)
app.register_blueprint(api_bp)
app.register_blueprint(bt_bp)
app.register_blueprint(config_bp)
app.register_blueprint(ma_bp)
app.register_blueprint(status_bp)
app.register_blueprint(auth_bp)


@app.context_processor
def inject_version():
    """Make VERSION available in all templates for cache-busting."""
    return {"VERSION": VERSION}


@app.route("/static/v<version>/<path:filename>")
def vstatic(version, filename):
    """Serve static files with version in the path for cache-busting.

    HA Ingress proxy strips query parameters, so ``?v=`` cache busting does
    not work.  Embedding the version in the *path* guarantees a fresh fetch
    on every upgrade.
    """
    resp = send_from_directory(app.static_folder, filename)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.after_request
def _set_cache_headers(response):
    """Prevent HA Ingress proxy and browsers from caching HTML pages."""
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Public paths that never require authentication
_PUBLIC_PATHS = {"/login", "/logout", "/api/health", "/api/preflight"}

# Cache for HA owner display name (resolved once per process lifetime)
_ingress_user_cache: Optional[str] = None


def _resolve_ingress_user() -> str:
    """Try to resolve the HA owner's display name via Supervisor API."""
    global _ingress_user_cache
    if _ingress_user_cache is not None:
        return _ingress_user_cache
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        _ingress_user_cache = "HA User"
        return _ingress_user_cache
    try:
        import json
        from urllib.request import Request, urlopen

        req = Request(
            "http://supervisor/core/api/auth/current_user",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        resp = urlopen(req, timeout=5)
        data = json.loads(resp.read())
        name = data.get("name") or data.get("id") or "HA User"
        _ingress_user_cache = name
    except Exception:
        _ingress_user_cache = "HA User"
    return _ingress_user_cache


@app.before_request
def _check_auth():
    """Enforce authentication when AUTH_ENABLED is True."""
    session.permanent = True  # use PERMANENT_SESSION_LIFETIME (24h)
    if not _auth_enabled:
        return  # auth disabled — allow all

    # HA Ingress: trust the header only when the request originates from the
    # local Supervisor proxy (prevents spoofing from LAN clients).
    if request.headers.get("X-Ingress-Path"):
        peer = request.remote_addr or ""
        if peer in _TRUSTED_PROXIES:
            if not session.get("ha_user"):
                session["ha_user"] = _resolve_ingress_user()
                session["authenticated"] = True
            return

    # Static assets and public API endpoints are always reachable
    if request.path.startswith("/static/"):
        return
    if request.path in _PUBLIC_PATHS:
        return

    # Active session
    if session.get("authenticated"):
        return

    # Unauthenticated — return 401 for API calls, redirect to login for pages
    if request.path.startswith("/api/"):
        return jsonify({"error": "Unauthorized"}), 401
    return redirect(url_for("auth.login", next=request.path))


@app.errorhandler(404)
def _handle_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return redirect("/")


@app.errorhandler(500)
def _handle_500(e):
    logger.error("Internal server error: %s", e)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return redirect("/")


def main():
    """Start the web interface"""
    port = int(os.getenv("WEB_PORT", 8080))
    threads = int(os.getenv("WEB_THREADS", 8))
    logger.info("Starting web interface on port %s", port)
    serve(app, host="0.0.0.0", port=port, threads=threads)


if __name__ == "__main__":
    main()
