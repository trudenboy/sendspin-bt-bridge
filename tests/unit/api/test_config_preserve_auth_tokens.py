"""Saving the configuration must keep the bearer tokens it never sends (#551).

``GET /api/v1/config`` strips ``AUTH_TOKENS`` from the response, so the
settings form can never submit it back.  The save then has to carry the list
forward from the file on disk, the same way it keeps the password hash and the
session secret; otherwise every save revoked the Home Assistant integration's
token.
"""

from __future__ import annotations

import json
import sys

import pytest

from sendspin_bridge.config.players import legacy_device_to_player

_MAC = "AA:BB:CC:DD:EE:FF"
_DEVICE = {"mac": _MAC, "player_name": "Speaker", "enabled": True}
_PLAYER = legacy_device_to_player(_DEVICE)
_TOKENS = [
    {
        "id": "abc123",
        "label": "ha-custom-component",
        "token_hash": "v2:" + "0" * 64,
        "created": "2026-10-25T12:00:00+00:00",
        "last_used": None,
    }
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "BRIDGE_NAME": "TestBridge",
                "BLUETOOTH_DEVICES": [_DEVICE],
                "BLUETOOTH_ADAPTERS": [],
                "AUTH_TOKENS": _TOKENS,
            }
        )
    )

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)

    for mod_name in ("sendspin_bridge.application.config",):
        if mod_name in sys.modules and getattr(sys.modules[mod_name], "__file__", None) is None:
            sys.modules.pop(mod_name)

    import sendspin_bridge.application.config as api_config_module

    monkeypatch.setattr(api_config_module, "CONFIG_FILE", cfg_file)

    from tests.support.api_client import make_client

    return make_client(), cfg_file


def test_save_keeps_the_auth_tokens(client):
    cl, cfg_file = client

    resp = cl.put("/api/v1/config", json={"BRIDGE_NAME": "Renamed", "players": [_PLAYER]})

    assert resp.status_code == 200
    saved = json.loads(cfg_file.read_text())
    assert saved["BRIDGE_NAME"] == "Renamed"
    assert saved["AUTH_TOKENS"] == _TOKENS


def test_save_does_not_accept_tokens_from_the_form(client):
    """Tokens are minted by their own endpoint, never by the settings form."""
    cl, cfg_file = client

    cl.put("/api/v1/config", json={"BRIDGE_NAME": "Renamed", "players": [_PLAYER], "AUTH_TOKENS": []})

    assert json.loads(cfg_file.read_text())["AUTH_TOKENS"] == _TOKENS
