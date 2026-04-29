"""Tests for the HA Supervisor fallback opt-in gate.

When ``_ha_flow_start`` returns ``None`` (HA core unreachable), the login
handler used to silently fall through to ``_supervisor_auth``, which does not
verify MFA.  After hardening, this fallback is off by default; enabling it
requires the ``ALLOW_SUPERVISOR_FALLBACK=1`` environment variable.
"""

from __future__ import annotations

import json
import logging
import secrets
from unittest.mock import patch

import pytest
from flask import Flask

from sendspin_bridge.web.routes.auth import auth_bp


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture(autouse=True)
def _ha_mode(monkeypatch):
    """Pretend we are in HA addon mode so the HA-core login path is taken."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "fake-token")


@pytest.fixture()
def app():
    application = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    application.secret_key = "test-secret"
    application.config["TESTING"] = True
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


def _post_login(client, **extra):
    token = secrets.token_hex(32)
    with client.session_transaction() as sess:
        sess["csrf_token"] = token
    data = {"csrf_token": token, "username": "alice", "password": "pw"}
    data.update(extra)
    return client.post("/login", data=data)


class TestFallbackOffByDefault:
    def test_fallback_refused_when_env_unset(self, client, monkeypatch, caplog):
        monkeypatch.delenv("ALLOW_SUPERVISOR_FALLBACK", raising=False)
        super_called = {"count": 0}

        def _fake_supervisor_auth(*a, **kw):
            super_called["count"] += 1
            return True

        with (
            patch("sendspin_bridge.web.routes.auth._ha_flow_start", return_value=None),
            patch("sendspin_bridge.web.routes.auth._supervisor_auth", side_effect=_fake_supervisor_auth),
            caplog.at_level(logging.ERROR, logger="sendspin_bridge.web.routes.auth"),
        ):
            resp = _post_login(client)

        assert resp.status_code == 200
        assert b"Authentication service unavailable" in resp.data
        assert super_called["count"] == 0
        assert any("refusing Supervisor fallback" in rec.message for rec in caplog.records)


class TestFallbackOnOptIn:
    def test_fallback_allowed_when_env_set(self, client, monkeypatch, caplog):
        monkeypatch.setenv("ALLOW_SUPERVISOR_FALLBACK", "1")

        with (
            patch("sendspin_bridge.web.routes.auth._ha_flow_start", return_value=None),
            patch("sendspin_bridge.web.routes.auth._supervisor_auth", return_value=True),
            caplog.at_level(logging.WARNING, logger="sendspin_bridge.web.routes.auth"),
        ):
            resp = _post_login(client)

        assert resp.status_code == 302
        assert "/login" not in resp.headers["Location"] or "next" in resp.headers["Location"]
        assert any("does NOT verify MFA" in rec.message for rec in caplog.records)
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("ha_user") == "alice"
            # outer /login wrapper stamps auth_method from form.method; the
            # inner handler's "ha_supervisor_fallback" marker is overwritten —
            # the authoritative signal that fallback was used is the log line.

    def test_fallback_invalid_creds_records_failure(self, client, monkeypatch):
        monkeypatch.setenv("ALLOW_SUPERVISOR_FALLBACK", "1")

        with (
            patch("sendspin_bridge.web.routes.auth._ha_flow_start", return_value=None),
            patch("sendspin_bridge.web.routes.auth._supervisor_auth", return_value=False),
            patch("sendspin_bridge.web.routes.auth._record_failure") as record_failure,
        ):
            resp = _post_login(client)

        assert resp.status_code == 200
        assert b"Invalid credentials" in resp.data
        record_failure.assert_called_once()
        with client.session_transaction() as sess:
            assert "authenticated" not in sess
