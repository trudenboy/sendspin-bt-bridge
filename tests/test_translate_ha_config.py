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
    _optional_int_opt,
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
        "ha_area_name_assist_enabled": True,
        "bluetooth_devices": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Speaker"}],
        "bluetooth_adapters": [],
        "tz": "UTC",
        "log_level": "info",
        "ma_auto_silent_auth": True,
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


def test_optional_int_opt_present():
    assert _optional_int_opt({"k": "42"}, "k") == 42


def test_optional_int_opt_missing():
    assert _optional_int_opt({}, "k") is None


def test_optional_int_opt_blank():
    assert _optional_int_opt({"k": ""}, "k") is None


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
    """When pulse_latency_msec is absent, default to 600."""
    opts = _minimal_options()
    assert "pulse_latency_msec" not in opts
    _write_json(tmp_path / "options.json", opts)

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["PULSE_LATENCY_MSEC"] == 600


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
        "LAST_SINKS": {"AA:BB:CC:DD:EE:FF": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"},
        "AUTH_PASSWORD_HASH": "abc123hash",
        "SECRET_KEY": "s3cr3t",
        "MA_ACCESS_TOKEN": "oauth-access",
        "MA_REFRESH_TOKEN": "oauth-refresh",
        "BLUETOOTH_DEVICES": [],
    }
    _write_json(tmp_path / "config.json", existing)
    _write_json(tmp_path / "options.json", _minimal_options())

    with patch("scripts.translate_ha_config._detect_adapters", return_value=[]):
        main()

    cfg = _read_json(tmp_path / "config.json")
    from config import CONFIG_SCHEMA_VERSION

    assert cfg["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION
    assert cfg["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 50}
    assert cfg["LAST_SINKS"] == {"AA:BB:CC:DD:EE:FF": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"}
    assert cfg["AUTH_PASSWORD_HASH"] == "abc123hash"
    assert cfg["SECRET_KEY"] == "s3cr3t"
    assert cfg["MA_ACCESS_TOKEN"] == "oauth-access"
    assert cfg["MA_REFRESH_TOKEN"] == "oauth-refresh"


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
        web_port=18080,
        base_listen_port=19000,
        bridge_name="MyBridge",
        bluetooth_devices=[
            {"mac": "AA:BB:CC:DD:EE:FF", "name": "Kitchen"},
            {"mac": "11:22:33:44:55:66", "name": "Bedroom"},
        ],
        tz="Europe/London",
        log_level="debug",
        prefer_sbc_codec=True,
        pulse_latency_msec=100,
        startup_banner_grace_seconds=7,
        recovery_banner_grace_seconds=9,
        bt_check_interval=30,
        bt_max_reconnect_fails=5,
        ma_api_url="http://ma:8095",
        ma_api_token="tok123",
    )
    _write_json(tmp_path / "options.json", opts)

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")

    from config import CONFIG_SCHEMA_VERSION

    assert cfg["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION
    assert cfg["SENDSPIN_SERVER"] == "10.0.0.5"
    assert cfg["SENDSPIN_PORT"] == 9001
    assert cfg["WEB_PORT"] is None
    assert cfg["BASE_LISTEN_PORT"] == 19000
    assert cfg["BRIDGE_NAME"] == "MyBridge"
    assert len(cfg["BLUETOOTH_DEVICES"]) == 2
    assert cfg["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert cfg["BLUETOOTH_DEVICES"][1]["name"] == "Bedroom"
    assert cfg["TZ"] == "Europe/London"
    assert cfg["STARTUP_BANNER_GRACE_SECONDS"] == 7
    assert cfg["RECOVERY_BANNER_GRACE_SECONDS"] == 9
    assert cfg["UPDATE_CHANNEL"] == "stable"


def test_translation_uses_installed_addon_track(tmp_path):
    _write_json(
        tmp_path / "options.json",
        _minimal_options(
            log_level="debug",
            prefer_sbc_codec=True,
            pulse_latency_msec=100,
            bt_check_interval=30,
            bt_max_reconnect_fails=5,
            ma_api_url="http://ma:8095",
            ma_api_token="tok123",
            ma_auto_silent_auth=False,
        ),
    )

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="rc"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["UPDATE_CHANNEL"] == "rc"
    assert "AUTH_ENABLED" not in cfg  # managed by web_interface in addon mode
    assert cfg["LOG_LEVEL"] == "DEBUG"
    assert cfg["PREFER_SBC_CODEC"] is True
    assert cfg["PULSE_LATENCY_MSEC"] == 100
    assert cfg["BT_CHECK_INTERVAL"] == 30
    assert cfg["BT_MAX_RECONNECT_FAILS"] == 5
    assert cfg["MA_API_URL"] == "http://ma:8095"
    assert cfg["MA_API_TOKEN"] == "tok123"
    assert cfg["MA_AUTO_SILENT_AUTH"] is False
    assert cfg["HA_AREA_NAME_ASSIST_ENABLED"] is True
    # enabled defaults to True for devices without explicit field
    for dev in cfg["BLUETOOTH_DEVICES"]:
        assert dev.get("enabled") is True


def test_translation_preserves_saved_port_overrides_when_options_omit_them(tmp_path):
    _write_json(
        tmp_path / "config.json",
        {
            "WEB_PORT": 18080,
            "BASE_LISTEN_PORT": 19000,
            "BLUETOOTH_DEVICES": [],
        },
    )
    _write_json(tmp_path / "options.json", _minimal_options())

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["WEB_PORT"] is None
    assert cfg["BASE_LISTEN_PORT"] == 19000


def test_translation_preserves_existing_ha_adapter_area_map(tmp_path):
    _write_json(
        tmp_path / "config.json",
        {"HA_ADAPTER_AREA_MAP": {"AA:BB:CC:DD:EE:FF": {"area_id": "living-room", "area_name": "Living Room"}}},
    )
    _write_json(tmp_path / "options.json", _minimal_options())

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["HA_ADAPTER_AREA_MAP"] == {"AA:BB:CC:DD:EE:FF": {"area_id": "living-room", "area_name": "Living Room"}}


def test_translation_respects_explicit_ha_area_name_assist_setting(tmp_path):
    _write_json(tmp_path / "options.json", _minimal_options(ha_area_name_assist_enabled=False))

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["HA_AREA_NAME_ASSIST_ENABLED"] is False


def test_translation_preserves_settings_experimental_card_toggles(tmp_path):
    """All five toggles in the Settings → Experimental features card
    are managed only via the bridge web UI — none are exposed in the
    addon's options.json schema.  Without explicit preservation the
    translator rewrites config.json on every restart and silently
    drops the operator's choices, which looks like the toggles
    "don't save".

    The card holds: A2DP sink recovery dance, Reload PA BT module,
    Adapter auto-recovery, Live RSSI badge, Allow HFP / HSP profile.

    NOTE: ``EXPERIMENTAL_PAIR_JUST_WORKS`` is *not* in this card —
    it lives in the scan modal as a per-pair transient override.
    Its preservation is asserted in the separate scan-modal-flag
    test below so the two concerns stay separable."""
    _write_json(
        tmp_path / "config.json",
        {
            "EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE": True,
            "EXPERIMENTAL_PA_MODULE_RELOAD": True,
            "EXPERIMENTAL_ADAPTER_AUTO_RECOVERY": True,
            "EXPERIMENTAL_RSSI_BADGE": True,
            "ALLOW_HFP_PROFILE": True,
            "BLUETOOTH_DEVICES": [],
        },
    )
    _write_json(tmp_path / "options.json", _minimal_options())

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE"] is True
    assert cfg["EXPERIMENTAL_PA_MODULE_RELOAD"] is True
    assert cfg["EXPERIMENTAL_ADAPTER_AUTO_RECOVERY"] is True
    assert cfg["EXPERIMENTAL_RSSI_BADGE"] is True
    assert cfg["ALLOW_HFP_PROFILE"] is True


def test_translation_preserves_scan_modal_pair_just_works_flag(tmp_path):
    """``EXPERIMENTAL_PAIR_JUST_WORKS`` lives in the scan modal as a
    per-pair toggle (not in the Settings card) — but the value can
    still end up in config.json via POST /api/config when the bridge
    persists a global default, so the translator must preserve it
    across addon restarts the same way as the Settings flags."""
    _write_json(
        tmp_path / "config.json",
        {"EXPERIMENTAL_PAIR_JUST_WORKS": True, "BLUETOOTH_DEVICES": []},
    )
    _write_json(tmp_path / "options.json", _minimal_options())

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["EXPERIMENTAL_PAIR_JUST_WORKS"] is True


def test_translation_preserves_other_config_only_settings(tmp_path):
    """Same gap affects every other web-UI-managed field that the
    addon schema doesn't expose: AUTH_ENABLED, BRUTE_FORCE_PROTECTION,
    MA_WEBSOCKET_MONITOR, AUTO_UPDATE, CHECK_UPDATES, SMOOTH_RESTART,
    TRUSTED_PROXIES.  All reach config.json via POST /api/config from
    the bridge UI.  Pin them here so addon restarts can't silently
    clobber operator choices, and surface the regression now so we
    don't have to re-discover it per flag."""
    _write_json(
        tmp_path / "config.json",
        {
            "AUTH_ENABLED": True,
            "BRUTE_FORCE_PROTECTION": False,
            "MA_WEBSOCKET_MONITOR": False,
            "AUTO_UPDATE": True,
            "CHECK_UPDATES": False,
            "SMOOTH_RESTART": False,
            "TRUSTED_PROXIES": ["10.0.0.1", "10.0.0.2"],
            "BLUETOOTH_DEVICES": [],
        },
    )
    _write_json(tmp_path / "options.json", _minimal_options())

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["AUTH_ENABLED"] is True
    assert cfg["BRUTE_FORCE_PROTECTION"] is False
    assert cfg["MA_WEBSOCKET_MONITOR"] is False
    assert cfg["AUTO_UPDATE"] is True
    assert cfg["CHECK_UPDATES"] is False
    assert cfg["SMOOTH_RESTART"] is False
    assert cfg["TRUSTED_PROXIES"] == ["10.0.0.1", "10.0.0.2"]


def test_translation_preserves_existing_startup_banner_grace_when_option_omitted(tmp_path):
    _write_json(
        tmp_path / "config.json",
        {"STARTUP_BANNER_GRACE_SECONDS": 4, "RECOVERY_BANNER_GRACE_SECONDS": 6},
    )
    _write_json(tmp_path / "options.json", _minimal_options())

    with (
        patch("scripts.translate_ha_config._detect_adapters", return_value=[]),
        patch("scripts.translate_ha_config.get_self_delivery_channel", return_value="stable"),
    ):
        main()

    cfg = _read_json(tmp_path / "config.json")
    assert cfg["STARTUP_BANNER_GRACE_SECONDS"] == 4
    assert cfg["RECOVERY_BANNER_GRACE_SECONDS"] == 6


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
