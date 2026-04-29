"""Tests for /logout hardening: CSRF + session.clear() + lockout bucket."""

from __future__ import annotations

import json
import secrets

import pytest
from flask import Flask

from sendspin_bridge.web.routes.auth import auth_bp


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def app():
    application = Flask(
        __name__,
        template_folder="../../../src/sendspin_bridge/web/templates",
        static_folder="../../../src/sendspin_bridge/web/static",
    )
    application.secret_key = "test-secret"
    application.config["TESTING"] = True
    # CSRF guard short-circuits when global auth is off; logout tests
    # exercise the auth-on path, so simulate it.
    application.config["AUTH_ENABLED"] = True
    application.register_blueprint(auth_bp)

    @application.route("/static/v<version>/<path:filename>")
    def vstatic(version, filename):
        from flask import send_from_directory

        return send_from_directory(application.static_folder, filename)

    @application.context_processor
    def _inject_version():
        return {"VERSION": "0.0.0-test"}

    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def _seed_csrf(client):
    token = secrets.token_hex(32)
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    return token


class TestLogoutGet:
    def test_get_returns_405_with_html_link(self, client):
        resp = client.get("/logout")
        assert resp.status_code == 405
        assert b"Logout requires POST" in resp.data
        assert b"/login" in resp.data

    def test_get_does_not_clear_session(self, client):
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["ha_user"] = "alice"
        client.get("/logout")
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("ha_user") == "alice"


class TestLogoutCsrf:
    def test_post_without_csrf_returns_403(self, client):
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        resp = client.post("/logout")
        assert resp.status_code == 403
        body = resp.get_json()
        assert body["error"] == "Invalid CSRF token"
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True

    def test_post_with_wrong_csrf_returns_403(self, client):
        _seed_csrf(client)
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        resp = client.post("/logout", data={"csrf_token": "not-the-real-token"})
        assert resp.status_code == 403
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True


class TestLogoutPostSuccess:
    def test_clears_session_fully(self, client):
        token = _seed_csrf(client)
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["ha_user"] = "alice"
            sess["auth_method"] = "ha_core"
            sess["_ha_login_user"] = "alice"
            sess["_ha_oauth"] = {"flow_id": "x"}
        resp = client.post("/logout", data={"csrf_token": token})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        with client.session_transaction() as sess:
            for key in (
                "authenticated",
                "ha_user",
                "auth_method",
                "_ha_login_user",
                "_ha_oauth",
                "csrf_token",
            ):
                assert key not in sess

    def test_preserves_lockout_bucket(self, client):
        token = _seed_csrf(client)
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["_lockout_client_id"] = "stable-bucket-xyz"
        resp = client.post("/logout", data={"csrf_token": token})
        assert resp.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("_lockout_client_id") == "stable-bucket-xyz"
            assert "authenticated" not in sess
