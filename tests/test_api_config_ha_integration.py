"""Round-trip tests for ``HA_INTEGRATION`` via ``/api/config``.

Covers the new flows surfaced by the Settings → Home Assistant tab:

* GET masks the MQTT password as ``***REDACTED***`` while preserving the
  rest of the block, and it never includes ``AUTH_TOKENS``.
* POST persists the block and treats an empty / redacted password as
  "keep existing" so a round-trip can't blank a working broker password.
* Download (``/api/config/download``) redacts the same password and
  surfaces the rest.
"""

from __future__ import annotations

import json
import sys

import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with the api_config blueprint mounted on a tmp config."""
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "BRIDGE_NAME": "TestBridge",
                "BLUETOOTH_DEVICES": [],
                "BLUETOOTH_ADAPTERS": [],
                "HA_INTEGRATION": {
                    "enabled": True,
                    "mode": "mqtt",
                    "mqtt": {
                        "broker": "broker.local",
                        "port": 1883,
                        "username": "u",
                        "password": "supersecret",
                        "discovery_prefix": "homeassistant",
                        "tls": False,
                        "client_id": "",
                    },
                    "rest": {"advertise_mdns": True, "supervisor_pair": True},
                },
                "AUTH_TOKENS": [
                    {
                        "id": "abc123",
                        "label": "ha-cc",
                        "token_hash": "v1:600000:00:11",
                        "created": "2026-04-28T12:00:00+00:00",
                        "last_used": None,
                    }
                ],
            }
        )
    )

    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)

    # Drop stubs from prior tests, if any.
    for mod_name in ("routes.api_config",):
        if mod_name in sys.modules and getattr(sys.modules[mod_name], "__file__", None) is None:
            sys.modules.pop(mod_name)

    import routes.api_config as api_config_module

    monkeypatch.setattr(api_config_module, "CONFIG_FILE", cfg_file)

    from routes.api_config import config_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(config_bp)
    yield app.test_client(), cfg_file


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------


def test_get_redacts_mqtt_password(client):
    cl, _ = client
    resp = cl.get("/api/config")
    assert resp.status_code == 200
    body = resp.get_json()
    ha = body.get("HA_INTEGRATION") or {}
    mqtt = ha.get("mqtt") or {}
    assert mqtt.get("password") == "***REDACTED***"
    # Surrounding fields preserved
    assert mqtt.get("broker") == "broker.local"
    assert mqtt.get("username") == "u"
    assert ha.get("enabled") is True
    assert ha.get("mode") == "mqtt"


def test_get_omits_auth_tokens(client):
    cl, _ = client
    resp = cl.get("/api/config")
    body = resp.get_json()
    assert "AUTH_TOKENS" not in body


def test_get_redacts_blank_password_to_empty(client, tmp_path, monkeypatch):
    cl, cfg_file = client
    raw = json.loads(cfg_file.read_text())
    raw["HA_INTEGRATION"]["mqtt"]["password"] = ""
    cfg_file.write_text(json.dumps(raw))
    resp = cl.get("/api/config")
    mqtt = resp.get_json()["HA_INTEGRATION"]["mqtt"]
    # Empty password → empty string in response (no false "redacted" hint).
    assert mqtt["password"] == ""


# ---------------------------------------------------------------------------
# POST /api/config — round-trip
# ---------------------------------------------------------------------------


def _read_persisted(cfg_file):
    return json.loads(cfg_file.read_text())


def test_post_preserves_password_when_blank(client):
    cl, cfg_file = client
    payload = {
        "HA_INTEGRATION": {
            "enabled": True,
            "mode": "mqtt",
            "mqtt": {
                "broker": "broker.local",
                "port": 1883,
                "username": "u",
                "password": "",  # blank from form → keep existing
                "discovery_prefix": "homeassistant",
                "tls": False,
                "client_id": "",
            },
            "rest": {"advertise_mdns": True, "supervisor_pair": True},
        },
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
    }
    resp = cl.post("/api/config", json=payload)
    assert resp.status_code == 200, resp.data
    persisted = _read_persisted(cfg_file)
    assert persisted["HA_INTEGRATION"]["mqtt"]["password"] == "supersecret"


def test_post_preserves_password_when_redacted_marker(client):
    cl, cfg_file = client
    payload = {
        "HA_INTEGRATION": {
            "enabled": True,
            "mode": "mqtt",
            "mqtt": {
                "broker": "broker.local",
                "port": 1883,
                "username": "u",
                "password": "***REDACTED***",  # echoed back from GET
                "discovery_prefix": "homeassistant",
                "tls": False,
                "client_id": "",
            },
            "rest": {"advertise_mdns": True, "supervisor_pair": True},
        },
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
    }
    resp = cl.post("/api/config", json=payload)
    assert resp.status_code == 200, resp.data
    persisted = _read_persisted(cfg_file)
    assert persisted["HA_INTEGRATION"]["mqtt"]["password"] == "supersecret"


def test_post_overwrites_password_when_explicit(client):
    cl, cfg_file = client
    payload = {
        "HA_INTEGRATION": {
            "enabled": True,
            "mode": "mqtt",
            "mqtt": {
                "broker": "broker.local",
                "port": 1883,
                "username": "u",
                "password": "newpass",
                "discovery_prefix": "homeassistant",
                "tls": False,
                "client_id": "",
            },
            "rest": {"advertise_mdns": True, "supervisor_pair": True},
        },
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
    }
    resp = cl.post("/api/config", json=payload)
    assert resp.status_code == 200, resp.data
    persisted = _read_persisted(cfg_file)
    assert persisted["HA_INTEGRATION"]["mqtt"]["password"] == "newpass"


def test_post_disable_round_trips(client):
    cl, cfg_file = client
    payload = {
        "HA_INTEGRATION": {
            "enabled": False,
            "mode": "off",
            "mqtt": {
                "broker": "auto",
                "port": 1883,
                "username": "",
                "password": "",
                "discovery_prefix": "homeassistant",
                "tls": False,
                "client_id": "",
            },
            "rest": {"advertise_mdns": True, "supervisor_pair": True},
        },
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
    }
    resp = cl.post("/api/config", json=payload)
    assert resp.status_code == 200, resp.data
    persisted = _read_persisted(cfg_file)
    assert persisted["HA_INTEGRATION"]["enabled"] is False
    assert persisted["HA_INTEGRATION"]["mode"] == "off"


# ---------------------------------------------------------------------------
# Download endpoint redaction
# ---------------------------------------------------------------------------


def test_download_redacts_mqtt_password(client):
    cl, _ = client
    resp = cl.get("/api/config/download")
    assert resp.status_code == 200
    payload = json.loads(resp.data)
    mqtt = payload["HA_INTEGRATION"]["mqtt"]
    assert mqtt["password"] == "***REDACTED***"
    # Other fields preserved.
    assert mqtt["broker"] == "broker.local"
    assert mqtt["username"] == "u"
