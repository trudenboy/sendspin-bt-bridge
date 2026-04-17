"""Integration tests: ma_auth routes must reject SSRF-style URLs.

Each endpoint that accepts ``ma_url`` or ``ha_url`` from the request body is
fuzzed against private/loopback/link-local hosts.  The tests also assert that
when the URL is rejected, no outbound HTTP is attempted (network helpers must
stay unmocked-but-untouched).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from routes import ma_auth as ma_auth_module
from routes.api_ma import ma_bp


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
    application.register_blueprint(ma_bp)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


# Sample URLs that must be rejected by is_safe_external_url in *default*
# mode — cloud metadata (link-local), multicast/reserved/unspecified, and
# any non-http(s) scheme.  Loopback and RFC1918 addresses are LAN-legit
# for this project and only blocked when ``SENDSPIN_STRICT_SSRF=1``; see
# ``UNSAFE_URLS_STRICT`` for that class.
UNSAFE_URLS = [
    "http://169.254.169.254/latest/meta-data/",  # AWS/GCP IMDS
    "http://169.254.1.1/",  # link-local
    "http://224.0.0.1/",  # multicast
    "http://0.0.0.0/",  # unspecified
    "file:///etc/passwd",
    "javascript:alert(1)",
    "gopher://attacker/",
]


UNSAFE_URLS_STRICT = [
    "http://127.0.0.1:8095",
    "http://127.0.0.1:22",
    "http://10.0.0.1:8095",
    "http://192.168.1.10:8095",
]


class TestApiMaLoginSSRF:
    """POST /api/ma/login must reject unsafe ma_url."""

    @pytest.mark.parametrize("url", UNSAFE_URLS)
    def test_rejects_unsafe_urls(self, client, url, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        urlopen = MagicMock()
        with (
            patch.object(ma_auth_module._ur, "urlopen", urlopen),
            patch.object(ma_auth_module, "get_main_loop", return_value=MagicMock()),
        ):
            resp = client.post(
                "/api/ma/login",
                json={"url": url, "username": "u", "password": "p"},
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid or disallowed URL"
        urlopen.assert_not_called()

    @pytest.mark.parametrize("url", UNSAFE_URLS_STRICT)
    def test_rejects_private_urls_in_strict_mode(self, client, url, monkeypatch):
        """Loopback + RFC1918 URLs are rejected when strict mode is on."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        urlopen = MagicMock()
        with (
            patch.object(ma_auth_module._ur, "urlopen", urlopen),
            patch.object(ma_auth_module, "get_main_loop", return_value=MagicMock()),
        ):
            resp = client.post(
                "/api/ma/login",
                json={"url": url, "username": "u", "password": "p"},
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid or disallowed URL"
        urlopen.assert_not_called()


class TestApiMaHaAuthPageSSRF:
    """GET /api/ma/ha-auth-page must reject unsafe ma_url."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254",
            "http://224.0.0.1",
            "file:///etc/passwd",
        ],
    )
    def test_rejects_unsafe_urls(self, client, url, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        resp = client.get("/api/ma/ha-auth-page", query_string={"ma_url": url})
        assert resp.status_code == 400
        assert b"Invalid or disallowed URL" in resp.data

    def test_accepts_empty_ma_url(self, client):
        resp = client.get("/api/ma/ha-auth-page")
        # Empty URL just renders the page with null MA_URL — not a rejection
        assert resp.status_code == 200


class TestApiMaHaSilentAuthSSRF:
    """POST /api/ma/ha-silent-auth must reject unsafe ma_url."""

    @pytest.mark.parametrize("url", ["http://169.254.169.254/", "http://224.0.0.1/"])
    def test_rejects_unsafe_urls(self, client, url, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        urlopen = MagicMock()
        with patch.object(ma_auth_module._ur, "urlopen", urlopen):
            resp = client.post(
                "/api/ma/ha-silent-auth",
                json={"ha_token": "t", "ma_url": url},
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid or disallowed URL"
        urlopen.assert_not_called()


class TestApiMaHaLoginSSRF:
    """POST /api/ma/ha-login (init) must reject unsafe ma_url."""

    @pytest.mark.parametrize("url", ["http://169.254.169.254", "http://224.0.0.1"])
    def test_init_rejects_unsafe_urls(self, client, url, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        urlopen = MagicMock()
        with patch.object(ma_auth_module._ur, "urlopen", urlopen):
            resp = client.post(
                "/api/ma/ha-login",
                json={"step": "init", "ma_url": url, "username": "u", "password": "p"},
            )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "Invalid or disallowed URL"
        urlopen.assert_not_called()


class TestMfaUsesSessionNotBody:
    """Step=mfa must read ha_url/client_id/state from session, not body.

    Exercises the hardened path: attacker supplies ``ha_url=evil.com`` in the
    MFA body, but backend should still route to the session-saved HA URL.
    """

    def test_missing_session_rejected(self, client, monkeypatch):
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)
        resp = client.post(
            "/api/ma/ha-login",
            json={
                "step": "mfa",
                "ma_url": "http://ma.example.com:8095",
                "ha_url": "http://evil.example.com",
                "client_id": "evil",
                "state": "x",
                "code": "123456",
            },
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert "Session expired" in body["error"]

    def test_mfa_uses_session_ha_url_not_body(self, client, monkeypatch):
        # Accept any URL for this test
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)
        captured_calls = []

        def _fake_flow_step(ha_url, flow_id, payload, client_id):
            captured_calls.append((ha_url, flow_id, payload, client_id))
            return {"type": "abort"}

        with patch.object(ma_auth_module, "_ha_login_flow_step", side_effect=_fake_flow_step):
            # Seed session as if init had stored OAuth state pointing to a
            # trusted HA URL.
            with client.session_transaction() as sess:
                sess["_ha_oauth"] = {
                    "auth_mode": "ma_oauth",
                    "flow_id": "real-flow-id",
                    "ha_url": "http://real-ha.example.com:8123",
                    "client_id": "real-client",
                    "state": "real-state",
                    "ma_url": "http://ma.example.com:8095",
                    "username": "alice",
                }
            # Attacker supplies a different ha_url/client_id/state in body
            resp = client.post(
                "/api/ma/ha-login",
                json={
                    "step": "mfa",
                    "ma_url": "http://ma.example.com:8095",
                    "ha_url": "http://evil.example.com",
                    "client_id": "evil-client",
                    "state": "evil-state",
                    "code": "123456",
                },
            )
        # Session values were used — not body values
        assert captured_calls
        ha_url_arg, flow_id_arg, payload_arg, client_id_arg = captured_calls[0]
        assert ha_url_arg == "http://real-ha.example.com:8123"
        assert flow_id_arg == "real-flow-id"
        assert client_id_arg == "real-client"
        assert payload_arg == {"code": "123456"}
        # Abort cleans session
        with client.session_transaction() as sess:
            assert "_ha_oauth" not in sess
        assert resp.status_code == 400

    def test_mfa_rejects_when_session_ma_url_mismatches_body(self, client, monkeypatch):
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)
        with client.session_transaction() as sess:
            sess["_ha_oauth"] = {
                "auth_mode": "ma_oauth",
                "flow_id": "real-flow-id",
                "ha_url": "http://real-ha.example.com:8123",
                "client_id": "real-client",
                "state": "real-state",
                "ma_url": "http://ma.example.com:8095",
                "username": "alice",
            }
        resp = client.post(
            "/api/ma/ha-login",
            json={
                "step": "mfa",
                "ma_url": "http://different-ma.example.com:8095",
                "code": "123456",
            },
        )
        assert resp.status_code == 400
        with client.session_transaction() as sess:
            assert "_ha_oauth" not in sess


class TestDeriveHaUrlsFiltering:
    def test_private_candidates_filtered(self, monkeypatch):
        # Make is_safe_external_url hard-coded to reject everything for this
        # test (stand-in for a private/loopback result)
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: False)
        result = ma_auth_module._derive_ha_urls_from_ma("http://ma.example.com:8095")
        assert result == []

    def test_public_candidates_kept(self, monkeypatch):
        monkeypatch.setattr(ma_auth_module, "is_safe_external_url", lambda _u: True)
        result = ma_auth_module._derive_ha_urls_from_ma("https://ma.example.com:8095")
        assert "https://ma.example.com" in result
        assert "https://ma.example.com:8123" in result
