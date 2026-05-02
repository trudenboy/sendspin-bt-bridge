"""Tests for save_device_static_delay (issue #237 — MA-driven delay persistence)."""

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


def test_save_device_static_delay_writes_to_device_dict(tmp_path):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay("AA:BB:CC:DD:EE:FF", 750)

    devices = _read_devices(tmp_path)
    assert devices[0]["static_delay_ms"] == 750
    # Must NOT introduce a parallel LAST_* cache map — delay is a config field.
    saved = json.loads((tmp_path / "config.json").read_text())
    assert "LAST_STATIC_DELAYS" not in saved


def test_save_device_static_delay_zero_persists(tmp_path):
    """Regression: delay=0 must not be treated as falsy."""
    _write_config(
        tmp_path,
        {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": 500}]},
    )
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay("AA:BB:CC:DD:EE:FF", 0)

    assert _read_devices(tmp_path)[0]["static_delay_ms"] == 0


def test_save_device_static_delay_clamps_negative_to_zero(tmp_path):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay("AA:BB:CC:DD:EE:FF", -250)

    assert _read_devices(tmp_path)[0]["static_delay_ms"] == 0


def test_save_device_static_delay_clamps_above_5000(tmp_path):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay("AA:BB:CC:DD:EE:FF", 99999)

    assert _read_devices(tmp_path)[0]["static_delay_ms"] == 5000


def test_save_device_static_delay_unknown_mac_warns_and_skips(tmp_path, caplog):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from sendspin_bridge.config import save_device_static_delay

    with caplog.at_level("WARNING"):
        save_device_static_delay("11:22:33:44:55:66", 400)

    devices = _read_devices(tmp_path)
    assert "static_delay_ms" not in devices[0]
    assert any("unknown Bluetooth device" in rec.message for rec in caplog.records)


def test_save_device_static_delay_normalizes_mac_case(tmp_path):
    """Stored device MAC is uppercase; lookup must accept lowercase input."""
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay("aa:bb:cc:dd:ee:ff", 1200)

    assert _read_devices(tmp_path)[0]["static_delay_ms"] == 1200


def test_save_device_static_delay_invalid_value_warns_and_skips(tmp_path, caplog):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from sendspin_bridge.config import save_device_static_delay

    with caplog.at_level("WARNING"):
        save_device_static_delay("AA:BB:CC:DD:EE:FF", "not_a_number")  # type: ignore[arg-type]

    devices = _read_devices(tmp_path)
    assert "static_delay_ms" not in devices[0]
    assert any("invalid value" in rec.message for rec in caplog.records)


def test_save_device_static_delay_no_op_without_mac(tmp_path):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay(None, 400)
    save_device_static_delay("", 400)

    assert "static_delay_ms" not in _read_devices(tmp_path)[0]


def test_save_device_static_delay_no_op_without_config_file(tmp_path):
    from sendspin_bridge.config import save_device_static_delay

    # Should not raise even though no config file exists.
    save_device_static_delay("AA:BB:CC:DD:EE:FF", 400)
    assert not (tmp_path / "config.json").exists()
