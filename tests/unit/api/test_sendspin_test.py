"""Endpoint POST /api/v1/config/sendspin-test (issue #291 follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def client(tmp_config):
    from tests.support.api_client import make_client

    return make_client()


def _post(client_obj, payload):
    return client_obj.post("/api/v1/config/sendspin-test", json=payload)


def test_malformed_server_returns_400(client):
    resp = _post(client, {"SENDSPIN_SERVER": "http://192.168.1.11:8095", "SENDSPIN_PORT": 8927})
    assert resp.status_code == 400
    problem = resp.json()
    assert problem["code"] == "config_invalid"
    # The probe's own answer travels with the problem, unchanged.
    assert problem["check"]["status"] == "error"
    assert problem["check"]["reason_code"] == "config_invalid"


def test_auto_mode_returns_200_ok(client):
    resp = _post(client, {"SENDSPIN_SERVER": "auto", "SENDSPIN_PORT": 8927})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body.get("auto_discovery") is True


def test_reachable_returns_200_ok(client):
    with patch(
        "sendspin_bridge.services.diagnostics.sendspin_port_probe.probe_sendspin_port",
        new_callable=AsyncMock,
        return_value=8927,
    ):
        resp = _post(client, {"SENDSPIN_SERVER": "192.168.1.11", "SENDSPIN_PORT": 8927})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["resolved_port"] == 8927


def test_unreachable_returns_200_error(client):
    """Unreachable host is a runtime problem, not a config problem — 200 OK with status=error."""
    with patch(
        "sendspin_bridge.services.diagnostics.sendspin_port_probe.probe_sendspin_port",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = _post(client, {"SENDSPIN_SERVER": "192.168.1.99", "SENDSPIN_PORT": 8927})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body.get("reachable") is False
