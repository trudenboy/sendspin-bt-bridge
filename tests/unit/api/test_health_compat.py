"""The image's Docker HEALTHCHECK (and older monitoring) probe ``/api/health``."""

from __future__ import annotations


def test_api_health_answers_like_v1_without_signing_in(tmp_config):
    from tests.support.api_client import make_client

    client = make_client({"AUTH_ENABLED": True, "AUTH_PASSWORD_HASH": "x"})

    resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
