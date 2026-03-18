"""Unit tests for scripts/translate_ha_config.py.

Tests HA addon options → config.json translation including edge cases
for zero-valued options, runtime state preservation, and adapter merging.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.translate_ha_config import (
    _detect_adapters,
    _int_opt,
    _merge_adapters,
    main,
)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _minimal_options(**overrides) -> dict:
    """Return a minimal valid options.json payload."""
    opts = {
        "sendspin_server": "192.168.1.10",
        "sendspin_port": 9000,
        "bridge_name": "TestBridge",
        "bluetooth_devices": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Speaker"}],
        "bluetooth_adapters": [],
        "tz": "UTC",
        "log_level": "info",
        "volume_via_ma": True,
        "update_channel": "stable",
    }
    opts.update(overrides)
    return opts


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    """Redirect OPTIONS_FILE and CONFIG_FILE to tmp_path."""
    import scripts.translate_ha_config as mod

    monkeypatch.setattr(mod, "OPTIONS_FILE", str(tmp_path / "options.json"))
    monkeypatch.setattr(mod, "CONFIG_FILE", str(tmp_path / "config.json"))


# ── _int_opt helper ──────────────────────────────────────────────────────


def test_int_opt_present():
    assert _int_opt({"k": 42}, "k", 99) == 42


def test_int_opt_zero_preserved():
    """Zero is a valid explicit value and must NOT fall back to default."""
    assert _int_opt({"k": 0}, "k", 200) == 0


def test_int_opt_missing():
    assert _int_opt({}, "k", 200) == 200


def test_int_opt_none():
    assert _int_opt({"k": None}, "k", 200) == 200


# ── Zero-valued options ──────────────────────────────────────────────────


def test_zero_latency_preserved(tmp_path):
    """pulse_latency_msec=0 must appear as PULSE_LATENCY_MSEC=0, not 200."""
    _write_json(tmp_path / "options.json", _minimal_options(pulse_latency_msec=0))

    with (
        patch.object(_detect_adapters, "__wrapped__", side_effect=lambda: [])
        if hasattr(_detect_adapters, "__wrapped__")
        else patch("scripts.translate_ha_config._detect_adapters", return_value=[])
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["PULSE_LATENCY_MSEC"] == 0


def test_default_latency(tmp_path):
    """When pulse_latency_msec is absent, default to 200."""
    opts = _minimal_options()
    assert "pulse_latency_msec" not in opts
    _write_json(tmp_path / "options.json", opts)

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["PULSE_LATENCY_MSEC"] == 200


def test_zero_bt_check_interval(tmp_path):
    """bt_check_interval=0 must be preserved as 0, not the default 10."""
    _write_json(tmp_path / "options.json", _minimal_options(bt_check_interval=0))

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["BT_CHECK_INTERVAL"] == 0


# ── Runtime state preservation ───────────────────────────────────────────


def test_runtime_state_preserved(tmp_path):
    """LAST_VOLUMES, AUTH_PASSWORD_HASH, SECRET_KEY should survive translation."""
    existing = {
        "LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 50},
        "AUTH_PASSWORD_HASH": "abc123hash",
        "SECRET_KEY": "s3cr3t",
        "BLUETOOTH_DEVICES": [],
    }
    _write_json(tmp_path / "config.json", existing)
    _write_json(tmp_path / "options.json", _minimal_options())

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 50}
    assert cfg["AUTH_PASSWORD_HASH"] == "abc123hash"
    assert cfg["SECRET_KEY"] == "s3cr3t"


# ── Adapter merging ──────────────────────────────────────────────────────


def test_merge_adapters_detected_plus_user():
    """Detected adapters + user-supplied adapters should merge correctly."""
    detected = [{"id": "hci0", "mac": "11:22:33:44:55:66", "name": "hci0"}]
    user = [
        {"id": "hci0", "mac": "11:22:33:44:55:66", "name": "Living Room"},
        {"id": "hci1", "mac": "AA:BB:CC:DD:EE:FF", "name": "Bedroom"},
    ]
    result = _merge_adapters(detected, user)

    macs = {a["mac"] for a in result}
    assert "11:22:33:44:55:66" in macs
    assert "AA:BB:CC:DD:EE:FF" in macs

    hci0 = next(a for a in result if a["mac"] == "11:22:33:44:55:66")
    assert hci0["name"] == "Living Room"  # user name overrides detected


def test_merge_adapters_with_bluetoothctl(tmp_path):
    """Mock bluetoothctl output with two controllers and verify detection + merge."""
    bt_output = "Controller 11:22:33:44:55:66 raspberry [default]\nController AA:BB:CC:DD:EE:FF dongle\n"
    user_adapters = [{"mac": "11:22:33:44:55:66", "name": "MyPi"}]

    with (
        patch("subprocess.check_output", return_value=bt_output.encode()),
        patch("scripts.translate_ha_config._mac_to_hci", return_value=""),
    ):
        detected = _detect_adapters()

    assert len(detected) == 2
    result = _merge_adapters(detected, user_adapters)

    pi = next(a for a in result if a["mac"] == "11:22:33:44:55:66")
    assert pi["name"] == "MyPi"  # user override
    dongle = next(a for a in result if a["mac"] == "AA:BB:CC:DD:EE:FF")
    assert dongle["name"] == "dongle"  # detected name preserved


# ── Full translation ─────────────────────────────────────────────────────


def test_basic_translation(tmp_path):
    """Full options → config translation with all required fields."""
    opts = _minimal_options(
        sendspin_server="10.0.0.5",
        sendspin_port=9001,
        bridge_name="MyBridge",
        bluetooth_devices=[
            {"mac": "AA:BB:CC:DD:EE:FF", "name": "Kitchen"},
            {"mac": "11:22:33:44:55:66", "name": "Bedroom"},
        ],
        tz="Europe/London",
        log_level="debug",
        volume_via_ma=False,
        prefer_sbc_codec=True,
        pulse_latency_msec=100,
        bt_check_interval=30,
        bt_max_reconnect_fails=5,
        ma_api_url="http://ma:8095",
        ma_api_token="tok123",
    )
    _write_json(tmp_path / "options.json", opts)

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")

    assert cfg["SENDSPIN_SERVER"] == "10.0.0.5"
    assert cfg["SENDSPIN_PORT"] == 9001
    assert cfg["BRIDGE_NAME"] == "MyBridge"
    assert len(cfg["BLUETOOTH_DEVICES"]) == 2
    assert cfg["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert cfg["BLUETOOTH_DEVICES"][1]["name"] == "Bedroom"
    assert cfg["TZ"] == "Europe/London"
    assert cfg["UPDATE_CHANNEL"] == "stable"


def test_translation_normalizes_update_channel(tmp_path):
    _write_json(
        tmp_path / "options.json",
        _minimal_options(
            update_channel="RC",
            log_level="debug",
            volume_via_ma=False,
            prefer_sbc_codec=True,
            pulse_latency_msec=100,
            bt_check_interval=30,
            bt_max_reconnect_fails=5,
            ma_api_url="http://ma:8095",
            ma_api_token="tok123",
        ),
    )

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["UPDATE_CHANNEL"] == "rc"
    assert "AUTH_ENABLED" not in cfg  # managed by web_interface in addon mode
    assert cfg["LOG_LEVEL"] == "DEBUG"
    assert cfg["VOLUME_VIA_MA"] is False
    assert cfg["PREFER_SBC_CODEC"] is True
    assert cfg["PULSE_LATENCY_MSEC"] == 100
    assert cfg["BT_CHECK_INTERVAL"] == 30
    assert cfg["BT_MAX_RECONNECT_FAILS"] == 5
    assert cfg["MA_API_URL"] == "http://ma:8095"
    assert cfg["MA_API_TOKEN"] == "tok123"
    # enabled defaults to True for devices without explicit field
    for dev in cfg["BLUETOOTH_DEVICES"]:
        assert dev.get("enabled") is True


def test_translate_script_runs_as_direct_file() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "translate_ha_config.py"
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd="/",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "options.json not found" in result.stdout
