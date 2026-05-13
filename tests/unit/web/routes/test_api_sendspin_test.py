"""Endpoint POST /api/sendspin/test (issue #291 follow-up)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


def _make_app():
    """Minimal Flask app wiring the api_config blueprint for integration tests."""
    from flask import Flask

    from sendspin_bridge.web.routes import api_config

    app = Flask(__name__)
    app.register_blueprint(api_config.config_bp)
    return app


@pytest.fixture
def client():
    app = _make_app()
    return app.test_client()


def _post(client_obj, payload):
    return client_obj.post(
        "/api/sendspin/test",
        data=json.dumps(payload),
        content_type="application/json",
    )


def test_malformed_server_returns_400(client):
    resp = _post(client, {"SENDSPIN_SERVER": "http://192.168.1.11:8095", "SENDSPIN_PORT": 8927})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["status"] == "error"
    assert body.get("reason_code") == "config_invalid"


def test_auto_mode_returns_200_ok(client):
    resp = _post(client, {"SENDSPIN_SERVER": "auto", "SENDSPIN_PORT": 8927})
    assert resp.status_code == 200
    body = resp.get_json()
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
    body = resp.get_json()
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
    body = resp.get_json()
    assert body["status"] == "error"
    assert body.get("reachable") is False
