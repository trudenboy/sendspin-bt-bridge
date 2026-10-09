"""Saving the configuration must never trade an unreadable config for lost secrets.

The merge step reads the on-disk config to carry forward the keys the settings
form never submits — the password hash, the session secret, the MA tokens, the
per-device volumes.  When that read failed the handler used to log at DEBUG and
continue with an empty ``existing``, then overwrite the file wholesale: one
truncated or unreadable ``config.json`` and the operator's credentials were
gone, with the endpoint still answering ``{"success": true}``.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

_MAC = "AA:BB:CC:DD:EE:FF"

_SECRETS = {
    "AUTH_PASSWORD_HASH": "v1:600000:aa:bb",
    "SECRET_KEY": "0123456789abcdef",
    "MA_ACCESS_TOKEN": "access-token-value",
    "MA_REFRESH_TOKEN": "refresh-token-value",
}

_DEVICE = {"mac": _MAC, "player_name": "Speaker", "enabled": True}


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "BRIDGE_NAME": "TestBridge",
                "BLUETOOTH_DEVICES": [_DEVICE],
                "BLUETOOTH_ADAPTERS": [],
                "LAST_VOLUMES": {_MAC: 42},
                **_SECRETS,
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

    return make_client(), cfg_file, api_config_module


def test_corrupt_existing_config_fails_the_request(client):
    """A truncated config.json must abort the save, not wipe the secrets."""
    cl, cfg_file, _ = client
    cfg_file.write_text('{"BRIDGE_NAME": "TestBridge", "SECRET_KEY": "0123456')
    before = cfg_file.read_text()

    resp = cl.put("/api/v1/config", json={"BRIDGE_NAME": "Renamed", "BLUETOOTH_DEVICES": [_DEVICE]})

    assert resp.status_code == 500
    assert resp.json()["code"] == "config_corrupted"
    assert cfg_file.read_text() == before


def test_successful_save_preserves_never_submitted_secrets(client):
    cl, cfg_file, _ = client

    resp = cl.put("/api/v1/config", json={"BRIDGE_NAME": "Renamed", "BLUETOOTH_DEVICES": [_DEVICE]})

    assert resp.status_code == 200
    saved = json.loads(cfg_file.read_text())
    assert saved["BRIDGE_NAME"] == "Renamed"
    for key, value in _SECRETS.items():
        assert saved[key] == value
    assert saved["LAST_VOLUMES"] == {_MAC: 42}


def test_first_run_without_a_config_file_still_saves(client):
    cl, cfg_file, _ = client
    cfg_file.unlink()

    resp = cl.put("/api/v1/config", json={"BRIDGE_NAME": "FreshInstall"})

    assert resp.status_code == 200
    assert json.loads(cfg_file.read_text())["BRIDGE_NAME"] == "FreshInstall"


# ── the same trade, on the upload route ──────────────────────────────────


def test_upload_refuses_when_the_existing_config_cannot_be_read(client):
    """The upload route swallowed the read the same way.

    Its preserve loop is the only thing carrying the password hash and the
    tokens across an upload, and the write replaces the file wholesale.
    """
    cl, cfg_file, _ = client
    cfg_file.write_text('{"BRIDGE_NAME": "TestBridge", "SECRET_KEY": "0123456')
    before = cfg_file.read_text()

    resp = cl.post(
        "/api/v1/config/import",
        files={"file": ("config.json", io.BytesIO(json.dumps({"BRIDGE_NAME": "Uploaded"}).encode()))},
    )

    assert resp.status_code == 500
    assert resp.json()["code"] == "config_corrupted"
    assert cfg_file.read_text() == before


def test_upload_keeps_the_secrets_the_form_never_sends(client):
    cl, cfg_file, _ = client

    resp = cl.post(
        "/api/v1/config/import",
        files={"file": ("config.json", io.BytesIO(json.dumps({"BRIDGE_NAME": "Uploaded"}).encode()))},
    )

    assert resp.status_code == 200
    saved = json.loads(cfg_file.read_text())
    assert saved["BRIDGE_NAME"] == "Uploaded"
    for key, value in _SECRETS.items():
        assert saved[key] == value


def test_upload_does_not_accept_a_secret_the_file_carries(client):
    """A secret in the uploaded file is not the operator's to set here."""
    cl, cfg_file, _ = client

    cl.post(
        "/api/v1/config/import",
        files={
            "file": (
                "config.json",
                io.BytesIO(json.dumps({"BRIDGE_NAME": "Uploaded", "AUTH_PASSWORD_HASH": "attacker"}).encode()),
            )
        },
    )

    assert json.loads(cfg_file.read_text())["AUTH_PASSWORD_HASH"] == _SECRETS["AUTH_PASSWORD_HASH"]


def test_upload_cannot_turn_on_authentication_without_a_password(client, monkeypatch):
    """Same rule as saving the settings: no password, no auth — or nobody can sign in."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    cl, cfg_file, _ = client
    stored = json.loads(cfg_file.read_text())
    stored.pop("AUTH_PASSWORD_HASH", None)
    cfg_file.write_text(json.dumps(stored))

    resp = cl.post(
        "/api/v1/config/import",
        files={"file": ("config.json", io.BytesIO(json.dumps({"BRIDGE_NAME": "X", "AUTH_ENABLED": True}).encode()))},
    )

    assert resp.status_code == 400
    assert "password" in resp.json()["detail"].lower()
    assert not json.loads(cfg_file.read_text()).get("AUTH_ENABLED")
