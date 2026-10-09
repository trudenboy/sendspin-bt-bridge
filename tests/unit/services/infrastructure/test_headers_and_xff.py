"""Tests for rate-limit XFF hop selection, the 500 handler, and X-Frame-Options."""

from __future__ import annotations

import json

import pytest
from fastapi import Request  # noqa: TC002 - FastAPI reads the annotation at runtime

from sendspin_bridge.security.request_identity import TrustPolicy


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


def _client_with_probe(monkeypatch, trusted: set[str]):
    """An app whose ``/probe`` answers with the address a request is attributed to.

    The request arrives from 127.0.0.1, a trusted proxy, so the forwarded
    headers count as they would behind a real one.
    """
    from sendspin_bridge.api import auth
    from tests.support.api_client import make_client

    monkeypatch.setattr(auth, "trust_policy", lambda _request: TrustPolicy(trusted))
    client = make_client(peer="127.0.0.1")

    @client.app.get("/probe")
    def _probe(request: Request) -> dict[str, str]:
        return {"client": auth.client_id(request)}

    return client


# ─── XFF rightmost-untrusted ─────────────────────────────────────────────


class TestForwardedClientIp:
    def test_single_hop_proxy_returns_real_client(self, monkeypatch):
        client = _client_with_probe(monkeypatch, {"127.0.0.1"})
        resp = client.get("/probe", headers={"X-Forwarded-For": "evil, 127.0.0.1"})
        assert resp.json()["client"] == "evil"

    def test_spoofed_leftmost_ignored(self, monkeypatch):
        """Spoofed client-set XFF entry should not win over the real hop."""
        client = _client_with_probe(monkeypatch, {"127.0.0.1"})
        resp = client.get("/probe", headers={"X-Forwarded-For": "spoofed, real-client, 127.0.0.1"})
        assert resp.json()["client"] == "real-client"

    def test_x_real_ip_fallback(self, monkeypatch):
        client = _client_with_probe(monkeypatch, {"127.0.0.1"})
        resp = client.get("/probe", headers={"X-Real-IP": "1.2.3.4"})
        assert resp.json()["client"] == "1.2.3.4"

    def test_untrusted_peer_headers_ignored(self, monkeypatch):
        from sendspin_bridge.api import auth
        from tests.support.api_client import make_client

        monkeypatch.setattr(auth, "trust_policy", lambda _request: TrustPolicy({"127.0.0.1"}))
        client = make_client(peer="192.0.2.7")

        @client.app.get("/probe")
        def _probe(request: Request) -> dict[str, str]:
            return {"client": auth.client_id(request)}

        resp = client.get("/probe", headers={"X-Forwarded-For": "1.2.3.4"})
        assert resp.json()["client"] == "192.0.2.7"


# ─── 500 handler ─────────────────────────────────────────────────────────


class TestServerErrorHandler:
    def test_unexpected_error_is_a_problem_without_internals(self):
        from tests.support.api_client import make_client

        client = make_client()

        @client.app.get("/api/v1/boom")
        def _boom():
            raise RuntimeError("secret internals")

        resp = client.get("/api/v1/boom")
        assert resp.status_code == 500
        assert resp.headers["content-type"].startswith("application/problem+json")
        body = resp.json()
        assert body["code"] == "internal_error"
        assert "secret internals" not in resp.text


# ─── X-Frame-Options standalone vs addon ────────────────────────────────


class TestXFrameOptions:
    def test_standalone_sets_sameorigin(self, monkeypatch):
        from tests.support.api_client import make_client

        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        resp = make_client().get("/api/v1/health")
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"

    def test_ha_addon_omits_xfo(self, monkeypatch):
        from tests.support.api_client import make_client

        monkeypatch.setenv("SUPERVISOR_TOKEN", "fake-token")
        resp = make_client().get("/api/v1/health")
        assert "X-Frame-Options" not in resp.headers
