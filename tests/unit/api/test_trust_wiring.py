"""Every consumer in a request asks the same trust policy, built from the live config."""

from __future__ import annotations

import json

import pytest
from fastapi import Request  # noqa: TC002 - FastAPI reads the annotation at runtime

from sendspin_bridge.security.request_identity import TrustPolicy
from tests.support.api_client import make_client

LATER = "192.0.2.10"  # a proxy the operator adds after start-up
INGRESS = "172.30.32.2"


@pytest.fixture(autouse=True)
def _standalone(monkeypatch, tmp_config):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    return tmp_config


def _who(client, **headers) -> str:
    from sendspin_bridge.api.auth import client_id

    if not any(getattr(r, "path", "") == "/api/v1/who" for r in client.app.routes):

        @client.app.get("/api/v1/who")
        def _route(request: Request) -> dict[str, str]:
            return {"client": client_id(request)}

    return client.get("/api/v1/who", headers=headers).json()["client"]


def test_a_settings_save_is_visible_to_the_next_request(tmp_config):
    """The legacy import-time snapshot went stale after the first save."""
    client = make_client(peer=LATER)
    tmp_config.write_text(json.dumps({"TRUSTED_PROXIES": []}))
    assert _who(client, **{"X-Forwarded-For": "198.51.100.7"}) == LATER

    tmp_config.write_text(json.dumps({"TRUSTED_PROXIES": [LATER]}))
    assert _who(client, **{"X-Forwarded-For": "198.51.100.7"}) == "198.51.100.7"


def test_one_request_builds_the_policy_once(tmp_config, monkeypatch):
    """The ingress middleware and the auth gate share one policy per request."""
    tmp_config.write_text(json.dumps({"TRUSTED_PROXIES": [LATER]}))
    builds: list[int] = []
    original = TrustPolicy.from_config.__func__

    def _counting(cls, config):
        builds.append(1)
        return original(cls, config)

    monkeypatch.setattr(TrustPolicy, "from_config", classmethod(_counting))
    client = make_client({"AUTH_ENABLED": True}, peer=INGRESS)

    client.get("/api/v1/auth/session", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})

    assert len(builds) == 1


def test_the_bug_report_limiter_buckets_by_the_same_policy(tmp_config, monkeypatch):
    """The legacy route rebuilt the trust set itself and could drift from the gate."""
    import sendspin_bridge.services.diagnostics.github_issue_proxy as issue_proxy

    tmp_config.write_text(json.dumps({"TRUSTED_PROXIES": [LATER]}))
    buckets: list[str] = []

    class _Proxy:
        available = True

        def check_rate_limit(self, client_ip):
            buckets.append(client_ip)
            return "rate limited"

    monkeypatch.setattr(issue_proxy, "get_issue_proxy", lambda: _Proxy())

    make_client(peer=LATER).post(
        "/api/v1/diagnostics/bug-report",
        json={"title": "Speaker drops", "description": "It drops every hour.", "email": "a@b.c"},
        headers={"X-Forwarded-For": "192.168.10.55"},
    )

    assert buckets == ["192.168.10.55"]
