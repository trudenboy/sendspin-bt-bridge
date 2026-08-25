"""Persisting a device flag must follow the config path in force.

`persist_device_enabled` and `persist_device_released` write through a helper
that resolves `CONFIG_FILE` when asked, but both guarded that call with an
existence check against the path bound at import.  Redirect the config — a
test, or a runtime that moved it — and the guard looked at a file that is not
there, returned early, and the write never happened.
"""

from __future__ import annotations

import json

import pytest

from sendspin_bridge.services.bluetooth import persist_device_enabled, persist_device_released


@pytest.fixture
def redirected_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    cfg = tmp_path / "elsewhere" / "config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"BLUETOOTH_DEVICES": [{"player_name": "Kitchen", "enabled": True}]}))
    monkeypatch.setattr(config, "CONFIG_DIR", cfg.parent)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg)
    return cfg


def test_the_enabled_flag_lands_in_the_live_config(redirected_config):
    persist_device_enabled("Kitchen", False)

    saved = json.loads(redirected_config.read_text())
    assert saved["BLUETOOTH_DEVICES"][0]["enabled"] is False


def test_a_release_lands_in_the_live_config(redirected_config):
    persist_device_released("Kitchen", True, released_by="user")

    saved = json.loads(redirected_config.read_text())
    assert saved["BLUETOOTH_DEVICES"][0]["released"] is True
    assert saved["BLUETOOTH_DEVICES"][0]["released_by"] == "user"
