"""Tests for the HA Supervisor fallback opt-in gate.

When ``_ha_flow_start`` returns ``None`` (HA core unreachable), the login
handler used to silently fall through to ``_supervisor_auth``, which does not
verify MFA.  After hardening, this fallback is off by default; enabling it
requires the ``ALLOW_SUPERVISOR_FALLBACK=1`` environment variable.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture(autouse=True)
def _ha_mode(monkeypatch):
    """Pretend we are in HA addon mode so the HA-core login path is taken."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "fake-token")


@pytest.fixture()
def client():
    from tests.support.api_client import make_client

    return make_client()


def _post_login(client):
    return client.post("/api/v1/auth/session", json={"method": "ha", "username": "alice", "password": "pw"})


class TestFallbackOffByDefault:
    def test_fallback_refused_when_env_unset(self, client, monkeypatch, caplog):
        monkeypatch.delenv("ALLOW_SUPERVISOR_FALLBACK", raising=False)
        super_called = {"count": 0}

        def _fake_supervisor_auth(*a, **kw):
            super_called["count"] += 1
            return True

        with (
            patch("sendspin_bridge.application.auth.flow_start", return_value=None),
            patch("sendspin_bridge.application.auth._supervisor_auth", side_effect=_fake_supervisor_auth),
            caplog.at_level(logging.ERROR, logger="sendspin_bridge.application.auth"),
        ):
            resp = _post_login(client)

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Authentication service unavailable"
        assert super_called["count"] == 0
        assert any("refusing Supervisor fallback" in rec.message for rec in caplog.records)


class TestFallbackOnOptIn:
    def test_fallback_allowed_when_env_set(self, client, monkeypatch, caplog):
        monkeypatch.setenv("ALLOW_SUPERVISOR_FALLBACK", "1")

        with (
            patch("sendspin_bridge.application.auth.flow_start", return_value=None),
            patch("sendspin_bridge.application.auth._supervisor_auth", return_value=True),
            caplog.at_level(logging.WARNING, logger="sendspin_bridge.application.auth"),
        ):
            resp = _post_login(client)

        assert resp.status_code == 200
        assert resp.json()["status"] == "signed_in"
        assert any("does NOT verify MFA" in rec.message for rec in caplog.records)
        session = client.get("/api/v1/auth/session").json()
        assert session["authenticated"] is True
        assert session["user"] == "alice"

    def test_fallback_invalid_creds_records_failure(self, client, monkeypatch):
        monkeypatch.setenv("ALLOW_SUPERVISOR_FALLBACK", "1")
        import sendspin_bridge.application.auth as auth_uc

        with (
            patch("sendspin_bridge.application.auth.flow_start", return_value=None),
            patch("sendspin_bridge.application.auth._supervisor_auth", return_value=False),
            patch.object(auth_uc.rate_limiter, "record_failure") as record_failure,
        ):
            resp = _post_login(client)

        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"
        record_failure.assert_called_once()
        assert client.get("/api/v1/auth/session").json()["authenticated"] is False
