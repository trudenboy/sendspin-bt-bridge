"""Tests for rate-limit XFF hop selection, 500 handler, and X-Frame-Options."""

from __future__ import annotations

import json

import pytest
from flask import Blueprint, Flask, abort

from sendspin_bridge.web.routes.auth import _get_forwarded_client_ip, auth_bp


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def app():
    application = Flask(__name__)
    application.secret_key = "test-secret"
    application.config["TESTING"] = True
    application.register_blueprint(auth_bp)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


# ─── XFF rightmost-untrusted ─────────────────────────────────────────────


class TestForwardedClientIp:
    def test_single_hop_proxy_returns_real_client(self, app, monkeypatch):
        monkeypatch.setattr("sendspin_bridge.web.routes.auth._get_trusted_proxies", lambda: {"127.0.0.1"})
        with app.test_request_context(
            "/",
            headers={"X-Forwarded-For": "evil, 127.0.0.1"},
        ):
            assert _get_forwarded_client_ip() == "evil"

    def test_spoofed_leftmost_ignored(self, app, monkeypatch):
        """Spoofed client-set XFF entry should not win over the real hop."""
        monkeypatch.setattr("sendspin_bridge.web.routes.auth._get_trusted_proxies", lambda: {"127.0.0.1"})
        with app.test_request_context(
            "/",
            headers={"X-Forwarded-For": "spoofed, real-client, 127.0.0.1"},
        ):
            assert _get_forwarded_client_ip() == "real-client"

    def test_all_trusted_returns_empty(self, app, monkeypatch):
        monkeypatch.setattr("sendspin_bridge.web.routes.auth._get_trusted_proxies", lambda: {"127.0.0.1", "::1"})
        with app.test_request_context(
            "/",
            headers={"X-Forwarded-For": "127.0.0.1, ::1"},
        ):
            assert _get_forwarded_client_ip() == ""

    def test_x_real_ip_fallback(self, app, monkeypatch):
        monkeypatch.setattr("sendspin_bridge.web.routes.auth._get_trusted_proxies", lambda: {"127.0.0.1"})
        with app.test_request_context("/", headers={"X-Real-IP": "1.2.3.4"}):
            assert _get_forwarded_client_ip() == "1.2.3.4"


# ─── 500 handler plain text ──────────────────────────────────────────────


class TestServerErrorHandler:
    def _build_app(self, monkeypatch):
        import importlib

        import web_interface

        importlib.reload(web_interface)
        web_interface.app.config["TESTING"] = False
        web_interface.app.config["PROPAGATE_EXCEPTIONS"] = False
        bp = Blueprint("boom", __name__)

        @bp.route("/boom-html")
        def _boom_html():
            abort(500)

        @bp.route("/api/boom")
        def _boom_api():
            abort(500)

        web_interface.app.register_blueprint(bp)
        return web_interface.app

    def test_html_route_500_returns_plain_text(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        app = self._build_app(monkeypatch)
        client = app.test_client()
        resp = client.get("/boom-html")
        assert resp.status_code == 500
        assert resp.mimetype == "text/plain"
        assert resp.data == b"Internal Server Error"

    def test_api_route_500_returns_json(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        app = self._build_app(monkeypatch)
        client = app.test_client()
        resp = client.get("/api/boom")
        assert resp.status_code == 500
        assert resp.is_json
        assert resp.get_json()["error"] == "Internal server error"


# ─── X-Frame-Options standalone vs addon ────────────────────────────────


class TestXFrameOptions:
    def test_standalone_sets_sameorigin(self, monkeypatch):
        import importlib

        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        import web_interface

        importlib.reload(web_interface)
        client = web_interface.app.test_client()
        resp = client.get("/login")
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"

    def test_ha_addon_omits_xfo(self, monkeypatch):
        import importlib

        monkeypatch.setenv("SUPERVISOR_TOKEN", "fake-token")
        import web_interface

        importlib.reload(web_interface)
        client = web_interface.app.test_client()
        resp = client.get("/login")
        assert "X-Frame-Options" not in resp.headers
