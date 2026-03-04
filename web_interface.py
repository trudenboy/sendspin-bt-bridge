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

from flask import Flask, jsonify, redirect, request, session, url_for
from waitress import serve  # type: ignore[import-untyped]

from config import ensure_secret_key, load_config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

# Set secret key (generated once and persisted to config.json so sessions
# survive container restarts).
_startup_config = load_config()
app.secret_key = ensure_secret_key(_startup_config)

# Harden session cookies: SameSite=Lax prevents cross-site request forgery
# (all POST endpoints also use request.get_json() which rejects form-encoded
# bodies, providing defence-in-depth).  HttpOnly prevents JS cookie access.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

# Cache AUTH_ENABLED at startup so _check_auth() never reads config.json
# on every request.  Like all other settings, a change takes effect after
# the service is restarted (same behaviour as SENDSPIN_SERVER, etc.).
_auth_enabled: bool = bool(_startup_config.get("AUTH_ENABLED", False))


_TRUSTED_PROXIES = {"127.0.0.1", "::1", "172.30.32.2"}


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
from routes.auth import auth_bp  # noqa: E402
from routes.views import views_bp  # noqa: E402

app.register_blueprint(views_bp)
app.register_blueprint(api_bp)
app.register_blueprint(auth_bp)

# Public paths that never require authentication
_PUBLIC_PATHS = {"/login", "/logout", "/api/status"}


@app.before_request
def _check_auth():
    """Enforce authentication when AUTH_ENABLED is True."""
    if not _auth_enabled:
        return  # auth disabled — allow all

    # HA Ingress: trust the header only when the request originates from the
    # local Supervisor proxy (prevents spoofing from LAN clients).
    if request.headers.get("X-Ingress-Path"):
        peer = request.remote_addr or ""
        if peer in _TRUSTED_PROXIES:
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
    logger.error(f"Internal server error: {e}")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return redirect("/")


def main():
    """Start the web interface"""
    port = int(os.getenv("WEB_PORT", 8080))
    logger.info(f"Starting web interface on port {port}")
    serve(app, host="0.0.0.0", port=port, threads=4)


if __name__ == "__main__":
    main()
