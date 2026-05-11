"""Tests for save_device_enabled (#263 auto-disable persistence)."""

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config_load_logged_once", False, raising=False)


def _write_config(tmp_path, data):
    (tmp_path / "config.json").write_text(json.dumps(data))


def _read_devices(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())["BLUETOOTH_DEVICES"]


def test_save_device_enabled_false_writes_to_device_dict(tmp_path):
    """Auto-disable path needs to flip BLUETOOTH_DEVICES[i].enabled to False
    so the change survives a bridge restart."""
    _write_config(
        tmp_path,
        {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "enabled": True}]},
    )
    from sendspin_bridge.config import save_device_enabled

    save_device_enabled("AA:BB:CC:DD:EE:FF", False)

    assert _read_devices(tmp_path)[0]["enabled"] is False


def test_save_device_enabled_true_writes_to_device_dict(tmp_path):
    """Re-enable path (operator clicks Re-enable on the recovery card)."""
    _write_config(
        tmp_path,
        {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "enabled": False}]},
    )
    from sendspin_bridge.config import save_device_enabled

    save_device_enabled("AA:BB:CC:DD:EE:FF", True)

    assert _read_devices(tmp_path)[0]["enabled"] is True


def test_save_device_enabled_handles_unknown_mac(tmp_path):
    """No-op when the MAC is not in the config — must not raise or add new entries."""
    _write_config(
        tmp_path,
        {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "enabled": True}]},
    )
    from sendspin_bridge.config import save_device_enabled

    save_device_enabled("99:99:99:99:99:99", False)

    devices = _read_devices(tmp_path)
    assert len(devices) == 1
    assert devices[0]["enabled"] is True


def test_save_device_enabled_normalizes_mac_case(tmp_path):
    """Lowercase input must match uppercase stored MAC (consistent with siblings)."""
    _write_config(
        tmp_path,
        {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "enabled": True}]},
    )
    from sendspin_bridge.config import save_device_enabled

    save_device_enabled("aa:bb:cc:dd:ee:ff", False)

    assert _read_devices(tmp_path)[0]["enabled"] is False


def test_save_device_enabled_skipped_when_mac_none(tmp_path):
    """None mac (no bt_manager) is silently ignored — sibling pattern."""
    _write_config(
        tmp_path,
        {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "enabled": True}]},
    )
    from sendspin_bridge.config import save_device_enabled

    save_device_enabled(None, False)

    assert _read_devices(tmp_path)[0]["enabled"] is True


def test_save_device_enabled_exported():
    """The helper must be in the public API surface so callers can import it."""
    import sendspin_bridge.config as config

    assert "save_device_enabled" in config.__all__
    assert hasattr(config, "save_device_enabled")
