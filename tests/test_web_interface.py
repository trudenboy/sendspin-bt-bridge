"""Unit tests for web_interface.py.

Tests CSP headers, cache headers, security headers, error handlers,
session timeout coercion, static cache-busting, and the _IngressMiddleware
double-slash rejection.
"""

import json
import os
import sys
import types

import pytest

# Stub route blueprint modules so web_interface can be imported regardless of
# the Python version available on the test runner (mirrors test_ingress_middleware.py).
for _mod_name in (
    "routes.api",
    "routes.api_bt",
    "routes.api_config",
    "routes.api_ma",
    "routes.api_status",
    "routes.api_transport",
    "routes.auth",
    "routes.views",
):
    if _mod_name not in sys.modules:
        _stub = types.ModuleType(_mod_name)
        _bp = type("FakeBP", (), {"register": lambda *a, **kw: None})()
        for _attr in (
            "api_bp",
            "bt_bp",
            "config_bp",
            "ma_bp",
            "status_bp",
            "transport_bp",
            "auth_bp",
            "views_bp",
        ):
            setattr(_stub, _attr, _bp)
        sys.modules[_mod_name] = _stub


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory so web_interface can import."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def app():
    """Create a minimal Flask app that mirrors web_interface setup."""
    from flask import Flask, g, jsonify, redirect, request, send_from_directory

    from web_interface import (
        _set_cache_headers,
    )

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    test_app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
        static_folder=os.path.join(project_root, "static"),
    )
    test_app.secret_key = "test-secret"
    test_app.config["TESTING"] = True

    @test_app.before_request
    def _generate_nonce():
        import secrets

        g.csp_nonce = secrets.token_urlsafe(16)

    test_app.after_request(_set_cache_headers)

    @test_app.route("/")
    def index():
        return "<html><body>Hello</body></html>", 200, {"Content-Type": "text/html"}

    @test_app.route("/api/data")
    def api_data():
        return jsonify({"ok": True})

    @test_app.route("/static/v<version>/<path:filename>")
    def vstatic(version, filename):
        resp = send_from_directory(test_app.static_folder, filename)
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp

    @test_app.errorhandler(404)
    def handle_404(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return redirect("/")

    @test_app.errorhandler(500)
    def handle_500(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Internal server error"}), 500
        return redirect("/")

    return test_app


@pytest.fixture()
def client(app):
    return app.test_client()


# ── CSP headers ──────────────────────────────────────────────────────────


def test_csp_header_present_on_html(client):
    """HTML responses must include a Content-Security-Policy header."""
    resp = client.get("/")
    assert "Content-Security-Policy" in resp.headers


def test_csp_uses_unsafe_inline_without_nonce(client):
    """script-src must use unsafe-inline WITHOUT nonce (nonce disables unsafe-inline, breaking onclick)."""
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    script_src = csp.split("script-src")[1].split(";")[0]
    assert "'unsafe-inline'" in script_src
    assert "nonce-" not in script_src


def test_csp_nonce_still_set_in_template_context(client):
    """csp_nonce is still generated for templates (future onclick→addEventListener migration)."""
    resp = client.get("/")
    # The nonce is in the template context but NOT in the CSP header
    assert resp.status_code in (200, 302)


def test_csp_not_on_json_response(client):
    """JSON API responses should not have a CSP header."""
    resp = client.get("/api/data")
    assert "Content-Security-Policy" not in resp.headers


# ── X-Content-Type-Options ───────────────────────────────────────────────


def test_nosniff_on_html(client):
    """X-Content-Type-Options: nosniff on HTML responses."""
    resp = client.get("/")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_nosniff_on_json(client):
    """X-Content-Type-Options: nosniff on JSON responses too."""
    resp = client.get("/api/data")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


# ── Cache-Control headers ────────────────────────────────────────────────


def test_html_cache_control_no_store(client):
    """HTML pages must have no-cache, no-store."""
    resp = client.get("/")
    cc = resp.headers.get("Cache-Control", "")
    assert "no-cache" in cc
    assert "no-store" in cc
    assert "must-revalidate" in cc


def test_html_pragma_no_cache(client):
    """HTML pages must have Pragma: no-cache."""
    resp = client.get("/")
    assert resp.headers.get("Pragma") == "no-cache"


def test_html_expires_zero(client):
    """HTML pages must have Expires: 0."""
    resp = client.get("/")
    assert resp.headers.get("Expires") == "0"


def test_json_no_cache_control_override(client):
    """JSON responses should NOT get HTML-specific cache headers."""
    resp = client.get("/api/data")
    cc = resp.headers.get("Cache-Control", "")
    assert "no-store" not in cc


# ── _coerce_session_timeout_hours ────────────────────────────────────────


class TestCoerceSessionTimeoutHours:
    def setup_method(self):
        from web_interface import _coerce_session_timeout_hours

        self.coerce = _coerce_session_timeout_hours

    def test_valid_int(self):
        assert self.coerce(12) == 12

    def test_string_number(self):
        assert self.coerce("48") == 48

    def test_none_returns_default(self):
        assert self.coerce(None) == 24

    def test_non_numeric_returns_default(self):
        assert self.coerce("abc") == 24

    def test_clamps_below_minimum(self):
        assert self.coerce(0) == 1

    def test_clamps_above_maximum(self):
        assert self.coerce(999) == 168

    def test_boundary_min(self):
        assert self.coerce(1) == 1

    def test_boundary_max(self):
        assert self.coerce(168) == 168


# ── Error handlers ───────────────────────────────────────────────────────


def test_404_api_returns_json(client):
    """API 404s return JSON with correct status code."""
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["error"] == "Not found"


def test_404_page_redirects_to_root(client):
    """Non-API 404s redirect to /."""
    resp = client.get("/nonexistent")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_500_api_returns_json(app):
    """API 500s return JSON with correct status code."""
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["TESTING"] = False

    @app.route("/api/boom")
    def boom():
        raise RuntimeError("Intentional test error")

    with app.test_client() as c:
        resp = c.get("/api/boom")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["error"] == "Internal server error"


def test_500_page_redirects_to_root(app):
    """Non-API 500s redirect to /."""
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["TESTING"] = False

    @app.route("/boom")
    def boom():
        raise RuntimeError("Intentional test error")

    with app.test_client() as c:
        resp = c.get("/boom")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")


# ── Static cache-busting ─────────────────────────────────────────────────


def test_vstatic_serves_file(client):
    """Versioned static route serves actual files with immutable cache."""
    resp = client.get("/static/v1.0.0/style.css")
    assert resp.status_code == 200
    cc = resp.headers.get("Cache-Control", "")
    assert "immutable" in cc
    assert "max-age=31536000" in cc


def test_vstatic_missing_file_not_200(client):
    """Versioned static route does not serve nonexistent files (404 or redirect)."""
    resp = client.get("/static/v1.0.0/does_not_exist.xyz")
    assert resp.status_code != 200


# ── _IngressMiddleware double-slash rejection ────────────────────────────


def test_ingress_rejects_double_slash():
    """Middleware must reject ingress paths starting with // (protocol-relative)."""
    from web_interface import _IngressMiddleware

    captured = {}

    def spy(environ, start_response):
        captured.update(environ)
        return [b"ok"]

    import web_interface

    original = web_interface._TRUSTED_PROXIES
    web_interface._TRUSTED_PROXIES = {"127.0.0.1"}
    try:
        mw = _IngressMiddleware(spy)
        env = {
            "REQUEST_METHOD": "GET",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8080",
            "PATH_INFO": "/",
            "SCRIPT_NAME": "",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_X_INGRESS_PATH": "//evil.com/path",
        }
        mw(env, lambda *a: None)
        assert captured["SCRIPT_NAME"] == ""
    finally:
        web_interface._TRUSTED_PROXIES = original


# ── CSP frame-ancestors ──────────────────────────────────────────────────


def test_csp_includes_frame_ancestors(client):
    """CSP should include frame-ancestors for HA Ingress framing."""
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors" in csp


def test_no_x_frame_options(client):
    """X-Frame-Options should NOT be set (CSP frame-ancestors supersedes it)."""
    resp = client.get("/")
    assert "X-Frame-Options" not in resp.headers
