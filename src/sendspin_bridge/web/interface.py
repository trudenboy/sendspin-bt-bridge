#!/usr/bin/env python3
"""
Web Interface for Sendspin Client
Provides configuration and monitoring UI.

This module is intentionally slim: Flask app initialisation, WSGI middleware,
blueprint registration, and the main() entry-point.  All route handlers live in
routes/api.py and routes/views.py; shared helpers live in config.py and state.py.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import timedelta
from pathlib import Path

from flask import Flask, Response, g, jsonify, redirect, request, send_from_directory, session, url_for
from waitress import serve  # type: ignore[import-untyped]

from sendspin_bridge.config import (
    ensure_secret_key,
    get_runtime_version,
    load_config,
    resolve_additional_web_port,
    resolve_web_port,
)

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


def _coerce_session_timeout_hours(raw_value) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return 24
    return min(168, max(1, value))


# Harden session cookies: SameSite=Lax prevents cross-site request forgery
# (all POST endpoints also use request.get_json() which rejects form-encoded
# bodies, providing defence-in-depth).  HttpOnly prevents JS cookie access.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    hours=_coerce_session_timeout_hours(_startup_config.get("SESSION_TIMEOUT_HOURS", 24))
)

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


_TRUSTED_PROXIES = {"127.0.0.1", "::1", "172.30.32.1", "172.30.32.2"}
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


app.wsgi_app = _IngressMiddleware(app.wsgi_app)  # type: ignore[method-assign]

# Register blueprints (imported after app is created to avoid circular imports)
from sendspin_bridge.web.routes.api import api_bp  # noqa: E402
from sendspin_bridge.web.routes.api_bt import bt_bp  # noqa: E402
from sendspin_bridge.web.routes.api_config import config_bp  # noqa: E402
from sendspin_bridge.web.routes.api_ha import ha_bp  # noqa: E402
from sendspin_bridge.web.routes.api_ma import ma_bp  # noqa: E402
from sendspin_bridge.web.routes.api_status import status_bp  # noqa: E402
from sendspin_bridge.web.routes.api_transport import transport_bp  # noqa: E402
from sendspin_bridge.web.routes.auth import auth_bp  # noqa: E402
from sendspin_bridge.web.routes.views import views_bp  # noqa: E402

app.register_blueprint(views_bp)
app.register_blueprint(api_bp)
app.register_blueprint(bt_bp)
app.register_blueprint(config_bp)
app.register_blueprint(ma_bp)
app.register_blueprint(status_bp)
app.register_blueprint(transport_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(ha_bp)

# WebSocket endpoints stay disabled in v2.63.0-rc.4+.
#
# The flask-sock + simple-websocket combo we shipped in rc.3 needs
# raw-socket access via the WSGI environment; ``waitress`` does not
# expose that, so the upgrade handler raises
# ``RuntimeError: Cannot obtain socket from WSGI environment.`` on
# every connect attempt and the endpoint returns HTTP 500.  See the
# rc.4 CHANGELOG entry for the analysis + the alternatives we
# considered (gunicorn+gevent, gevent.pywsgi embedded).
#
# The original problem the WS migration was supposed to solve — HA
# Supervisor ingress applying deflate compression to
# ``text/event-stream`` and corrupting SSE payloads — is closed in
# rc.4 by the ``Cache-Control: no-transform`` + ``Content-Encoding:
# identity`` headers added in ``routes/api_status.py``.  We keep
# ``routes/api_ws.py`` and its ``status_ws_iter`` /
# ``log_stream_iter`` generators (and their tests) so they're ready
# for revival if/when the bridge moves to an ASGI server (uvicorn /
# hypercorn) that supports WebSocket upgrades natively.


@app.before_request
def _generate_csp_nonce():
    """Generate a per-request nonce for CSP script-src."""
    g.csp_nonce = secrets.token_urlsafe(16)


@app.context_processor
def inject_version():
    """Make asset versions available in all templates for cache-busting."""
    runtime_version = get_runtime_version()

    def asset_version(filename: str) -> str:
        try:
            mtime = int(Path(app.static_folder or "", filename).stat().st_mtime)
        except OSError:
            mtime = 0
        return f"{runtime_version}-{mtime}"

    return {"VERSION": runtime_version, "asset_version": asset_version, "csp_nonce": g.csp_nonce}


@app.route("/static/v<version>/<path:filename>")
def vstatic(version, filename):
    """Serve static files with version in the path for cache-busting.

    HA Ingress proxy strips query parameters, so ``?v=`` cache busting does
    not work.  Embedding the version in the *path* guarantees a fresh fetch
    on every upgrade.
    """
    resp = send_from_directory(app.static_folder, filename)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    # ``send_from_directory`` returns a passthrough response (the file
    # is streamed instead of read into memory).  Disable that so the
    # gzip ``after_request`` middleware below can read the body and
    # compress it — ``app.js`` (~620 KB) and ``style.css`` (~330 KB)
    # land at ~150 KB total over the wire after gzip, which is a
    # multi-second cold-load improvement through HA Ingress / Nabu
    # Casa.  600 KB into RAM per request is well within budget.
    resp.direct_passthrough = False
    return resp


@app.after_request
def _set_cache_headers(response):
    """Prevent HA Ingress proxy and browsers from caching HTML pages.

    Also sets security headers:
      * ``Content-Security-Policy`` on HTML responses (including
        ``frame-ancestors 'self'``)
      * ``X-Content-Type-Options: nosniff`` on every response
      * ``X-Frame-Options: SAMEORIGIN`` in standalone/Docker mode.  It is
        omitted in HA add-on mode so HA Ingress can frame us (CSP
        ``frame-ancestors`` already provides the modern equivalent).
    """
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        nonce = getattr(g, "csp_nonce", "")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'self'"
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not _is_ha_addon:
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response


# Content types we'll gzip when the client asks for it.  Anything
# binary (images, fonts, audio) is excluded — it's already compressed
# upstream and gzipping it wastes CPU.
_GZIPPABLE_PREFIXES = (
    "text/html",
    "text/css",
    "text/plain",
    # ``text/javascript`` is what Flask's ``send_file`` infers from
    # ``.js`` (the modern IANA-preferred MIME type as of RFC 9239).
    # ``application/javascript`` is the legacy form some frameworks
    # still emit.  Cover both so cold-load assets always compress.
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
)
_GZIP_MIN_BYTES = 1024


@app.after_request
def _gzip_response(response):
    """Gzip text-ish responses when the client advertises support.

    Without this, the bridge ships ~960 KB of uncompressed JS+CSS on
    every cold load, which through HA Ingress (or Nabu Casa) reads
    as "the addon won't start" for several seconds.  Gzip drops the
    payload by ~70-80 % at compresslevel=6, which is barely
    measurable CPU for the request rate this bridge handles.

    Skipped paths:

    * ``response.direct_passthrough`` — covers the SSE endpoint at
      ``/api/status/stream`` (must stay uncompressed; ingress's
      ``Cache-Control: no-transform`` handshake assumes plain text)
      and any other streamed body.
    * Already-encoded responses (``Content-Encoding`` already set).
    * Non-text content types.
    * Bodies under ~1 KB (compression overhead exceeds savings).
    """
    if response.direct_passthrough:
        return response
    # Honour RFC 7231 quality values — ``gzip;q=0`` explicitly opts out
    # and substring matching would compress those clients anyway.
    if request.accept_encodings["gzip"] <= 0:
        return response
    if response.headers.get("Content-Encoding"):
        return response
    ctype = (response.content_type or "").split(";")[0].strip().lower()
    if not ctype or not any(ctype.startswith(p) for p in _GZIPPABLE_PREFIXES):
        return response
    if ctype == "text/event-stream":
        return response
    raw = response.get_data()
    if len(raw) < _GZIP_MIN_BYTES:
        return response
    import gzip as _gzip

    # ``mtime=0`` keeps the gzip header timestamp deterministic so two
    # identical bodies produce identical bytes — important for any
    # caching proxy (and for ETag/If-None-Match round-trips below).
    compressed = _gzip.compress(raw, compresslevel=6, mtime=0)
    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    # Distinguish the gzipped representation from the original in
    # caching validators.  ``send_from_directory`` sets an ETag based
    # on the *uncompressed* file inode/size; without a suffix here a
    # ``If-None-Match`` from a gzip-supporting client could match
    # against the wrong body.  RFC 9110 §8.8.3 explicitly allows
    # encoding-tagged ETags.
    etag = response.headers.get("ETag", "")
    if etag and "-gzip" not in etag:
        if etag.endswith('"'):
            response.headers["ETag"] = etag[:-1] + '-gzip"'
        else:
            response.headers["ETag"] = etag + "-gzip"
    vary = response.headers.get("Vary", "")
    if "accept-encoding" not in vary.lower():
        response.headers["Vary"] = (vary + ", " if vary else "") + "Accept-Encoding"
    return response


# Public paths that never require authentication.
#
# ``/api/auth/ha-pair`` is the bootstrap endpoint the HA custom_component
# hits before any bearer token exists; the route itself enforces the real
# gate (Supervisor-IP + ``X-Ingress-Path`` header check in
# ``routes/auth.py``), so opening it here just lets the request reach
# that handler.  Any future pre-auth bootstrap endpoint goes here too.
_PUBLIC_PATHS = {
    "/login",
    "/logout",
    "/api/health",
    "/api/preflight",
    "/api/auth/ha-pair",
}

# Cache for HA owner display name (resolved once per process lifetime)
_ingress_user_cache: str | None = None


def _resolve_ingress_user() -> str:
    """Try to resolve the HA owner's display name for Ingress sessions.

    SUPERVISOR_TOKEN cannot access core/api/auth/current_user (401), so we
    use MA_USERNAME from config (saved during HA login flow) as the primary
    source, falling back to the Supervisor /core/api/config location_name.
    """
    global _ingress_user_cache
    if _ingress_user_cache is not None:
        return _ingress_user_cache

    # 1. Use MA_USERNAME saved during MA auth setup (most reliable)
    try:
        from sendspin_bridge.config import load_config

        cfg = load_config()
        username = cfg.get("MA_USERNAME", "")
        if username:
            _ingress_user_cache = username
            return _ingress_user_cache
    except Exception as exc:
        logger.debug("Failed to load config for ingress user: %s", exc)

    _ingress_user_cache = "HA User"
    return _ingress_user_cache


@app.before_request
def _check_auth():
    """Enforce authentication when AUTH_ENABLED is True."""
    session.permanent = True  # use configured PERMANENT_SESSION_LIFETIME
    if not _auth_enabled:
        return  # auth disabled — allow all

    # HA Ingress: trust the header only when the request originates from the
    # local Supervisor proxy (prevents spoofing from LAN clients).
    if request.headers.get("X-Ingress-Path"):
        peer = request.remote_addr or ""
        if peer in _TRUSTED_PROXIES:
            # Supervisor sends user identity headers (HA 2024.x+)
            display_name = (
                request.headers.get("X-Remote-User-Display-Name") or request.headers.get("X-Remote-User-Name") or ""
            )
            if display_name:
                session["ha_user"] = display_name
            elif not session.get("ha_user"):
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

    # Bearer-token authentication (used by the HA custom_component).  Tokens
    # are issued via /api/auth/tokens and stored hashed in config.json.  A
    # successful match grants ``authenticated`` for this request only — we
    # don't promote it into the persistent session, so a leaked token
    # never grants a UI cookie.
    if request.path.startswith("/api/"):
        try:
            from sendspin_bridge.services.diagnostics.auth_tokens import extract_bearer, find_matching_token

            presented = extract_bearer(request.headers)
            if presented:
                record = find_matching_token(presented)
                if record is not None:
                    g.bearer_principal = f"bearer:{record.label}"
                    return
        except Exception:
            # Auth-token machinery must never crash auth enforcement —
            # fall through to the standard 401 path.
            pass

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
    return Response("Internal Server Error", status=500, mimetype="text/plain")


def main():
    """Start the web interface"""
    port = resolve_web_port()
    additional_port = resolve_additional_web_port()
    threads = int(os.getenv("WEB_THREADS", 8))
    if additional_port is not None:
        logger.info("Starting additional direct web interface on port %s", additional_port)
        threading.Thread(
            target=serve,
            kwargs={"app": app, "host": "0.0.0.0", "port": additional_port, "threads": threads},
            name="WebServerDirect",
            daemon=True,
        ).start()
    logger.info("Starting web interface on port %s", port)
    serve(app, host="0.0.0.0", port=port, threads=threads)


if __name__ == "__main__":
    main()
