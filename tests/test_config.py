"""Tests for config.py — pure functions that don't require hardware."""

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory for every test."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config_load_logged_once", False, raising=False)


def _write_config(tmp_path, data):
    (tmp_path / "config.json").write_text(json.dumps(data))


def test_load_defaults_when_no_file():
    from config import CONFIG_SCHEMA_VERSION, DEFAULT_CONFIG, load_config

    cfg = load_config()
    for key, default in DEFAULT_CONFIG.items():
        assert cfg[key] == default
    assert cfg["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION


def test_default_config_keys_present_in_json_schema():
    """``config.schema.json`` is the machine-readable surface external
    tooling validates user configs against.  Every top-level
    ``DEFAULT_CONFIG`` key must be declared in the schema's
    ``properties`` block, otherwise the schema and the runtime drift
    silently — operators editing config.json get no validation hint
    for the missing key.

    Regression test for Copilot review on PR #196 (``ALLOW_HFP_PROFILE``
    landed in ``DEFAULT_CONFIG`` but was missing from the schema).
    """
    from pathlib import Path

    from config import DEFAULT_CONFIG

    schema_path = Path(__file__).resolve().parents[1] / "config.schema.json"
    schema = json.loads(schema_path.read_text())
    schema_props = set((schema.get("properties") or {}).keys())

    # ``CONFIG_SCHEMA_VERSION`` is internal bookkeeping (carries the
    # migration version, not a user-tunable knob) — schema does not
    # need to advertise it.
    runtime_keys = set(DEFAULT_CONFIG) - {"CONFIG_SCHEMA_VERSION"}
    missing = sorted(runtime_keys - schema_props)
    assert not missing, f"DEFAULT_CONFIG keys missing from config.schema.json: {missing}"


def test_load_known_keys(tmp_path):
    _write_config(tmp_path, {"SENDSPIN_SERVER": "10.0.0.1", "SENDSPIN_PORT": 1234})
    from config import load_config

    cfg = load_config()
    assert cfg["SENDSPIN_SERVER"] == "10.0.0.1"
    assert cfg["SENDSPIN_PORT"] == 1234


def test_load_ignores_unknown_keys(tmp_path):
    _write_config(tmp_path, {"UNKNOWN": "x"})
    from config import load_config

    assert "UNKNOWN" not in load_config()


def test_load_handles_corrupted_json(tmp_path):
    (tmp_path / "config.json").write_text("{bad")
    from config import DEFAULT_CONFIG, load_config

    assert load_config() == DEFAULT_CONFIG
    backups = sorted(tmp_path.glob("config.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "{bad"


def test_load_migrates_legacy_config_keys(tmp_path):
    _write_config(
        tmp_path,
        {
            "BLUETOOTH_MAC": "aa:bb:cc:dd:ee:ff",
            "LAST_VOLUME": 33,
            "SENDSPIN_SERVER": "10.0.0.1",
        },
    )
    from config import CONFIG_SCHEMA_VERSION, load_config

    loaded = load_config()

    assert loaded["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION
    assert loaded["BLUETOOTH_DEVICES"] == [
        {"mac": "AA:BB:CC:DD:EE:FF", "adapter": "", "player_name": "Sendspin Player"}
    ]
    assert loaded["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 33}
    saved = json.loads((tmp_path / "config.json").read_text())
    assert "BLUETOOTH_MAC" not in saved
    assert "LAST_VOLUME" not in saved
    assert saved["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 33}


def test_save_device_volume(tmp_path):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from config import save_device_volume

    save_device_volume("AA:BB:CC:DD:EE:FF", 75)
    with open(tmp_path / "config.json") as f:
        assert json.load(f)["LAST_VOLUMES"]["AA:BB:CC:DD:EE:FF"] == 75


def test_save_volume_zero(tmp_path):
    """Regression: volume=0 must not be treated as falsy."""
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from config import save_device_volume

    save_device_volume("AA:BB:CC:DD:EE:FF", 0)
    with open(tmp_path / "config.json") as f:
        assert json.load(f)["LAST_VOLUMES"]["AA:BB:CC:DD:EE:FF"] == 0


def test_save_device_sink_normalizes_mac_and_sink(tmp_path):
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}]})
    from config import save_device_sink

    save_device_sink(" aa:bb:cc:dd:ee:ff ", " bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink ")

    with open(tmp_path / "config.json") as f:
        assert json.load(f)["LAST_SINKS"]["AA:BB:CC:DD:EE:FF"] == "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"


def test_save_device_volume_skips_unknown_device(tmp_path):
    _write_config(
        tmp_path,
        {
            "BLUETOOTH_DEVICES": [{"mac": "11:22:33:44:55:66"}],
            "LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 40},
        },
    )
    from config import save_device_volume

    save_device_volume("AA:BB:CC:DD:EE:FF", 75)

    with open(tmp_path / "config.json") as f:
        saved = json.load(f)
    assert saved["LAST_VOLUMES"] == {}


def test_player_id_deterministic_and_case_insensitive():
    from config import _player_id_from_mac

    assert _player_id_from_mac("aa:bb:cc:dd:ee:ff") == _player_id_from_mac("AA:BB:CC:DD:EE:FF")


def test_password_roundtrip():
    from config import check_password, hash_password

    assert check_password("secret", hash_password("secret"))
    assert not check_password("wrong", hash_password("secret"))


def test_check_password_handles_garbage():
    from config import check_password

    assert not check_password("x", "")
    assert not check_password("x", "not_a_hash")


def test_load_ma_auto_silent_auth(tmp_path):
    """MA_AUTO_SILENT_AUTH must survive load_config() round-trip."""
    _write_config(tmp_path, {"MA_AUTO_SILENT_AUTH": False})
    from config import load_config

    assert load_config()["MA_AUTO_SILENT_AUTH"] is False


def test_load_preserves_ma_oauth_tokens(tmp_path):
    _write_config(
        tmp_path,
        {
            "MA_ACCESS_TOKEN": "access-token",
            "MA_REFRESH_TOKEN": "refresh-token",
        },
    )
    from config import load_config

    loaded = load_config()

    assert loaded["MA_ACCESS_TOKEN"] == "access-token"
    assert loaded["MA_REFRESH_TOKEN"] == "refresh-token"


def test_load_normalizes_last_sinks_keys(tmp_path):
    _write_config(
        tmp_path,
        {
            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
            "LAST_SINKS": {" aa:bb:cc:dd:ee:ff ": " bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink "},
        },
    )
    from config import load_config

    loaded = load_config()

    assert loaded["LAST_SINKS"] == {"AA:BB:CC:DD:EE:FF": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"}


def test_load_config_normalizes_types_and_prunes_orphan_volumes(tmp_path):
    _write_config(
        tmp_path,
        {
            "SENDSPIN_PORT": "9001",
            "BT_CHECK_INTERVAL": "15",
            "BT_CHURN_THRESHOLD": "3",
            "BT_CHURN_WINDOW": "120.5",
            "PREFER_SBC_CODEC": "true",
            "BLUETOOTH_DEVICES": [{"mac": "aa:bb:cc:dd:ee:ff"}],
            "LAST_VOLUMES": {
                "AA:BB:CC:DD:EE:FF": 55,
                "11:22:33:44:55:66": 80,
            },
        },
    )
    from config import load_config

    loaded = load_config()

    assert loaded["SENDSPIN_PORT"] == 9001
    assert loaded["BT_CHECK_INTERVAL"] == 15
    assert loaded["BT_CHURN_THRESHOLD"] == 3
    assert loaded["BT_CHURN_WINDOW"] == 120.5
    assert loaded["PREFER_SBC_CODEC"] is True
    assert loaded["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert loaded["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 55}


def test_load_config_normalizes_room_metadata(tmp_path):
    _write_config(
        tmp_path,
        {
            "BLUETOOTH_DEVICES": [
                {
                    "mac": "aa:bb:cc:dd:ee:ff",
                    "player_name": "Kitchen",
                    "room_name": "  Living Room  ",
                    "room_id": " living-room ",
                }
            ]
        },
    )
    from config import load_config

    loaded = load_config()

    assert loaded["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert loaded["BLUETOOTH_DEVICES"][0]["room_name"] == "Living Room"
    assert loaded["BLUETOOTH_DEVICES"][0]["room_id"] == "living-room"
    assert "handoff_mode" not in loaded["BLUETOOTH_DEVICES"][0]


def test_resolve_device_room_context_prefers_manual_room_metadata_over_ha_area():
    from config import resolve_device_room_context

    resolved = resolve_device_room_context(
        {
            "BLUETOOTH_DEVICES": [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "player_name": "Kitchen",
                    "room_id": "kitchen",
                    "room_name": "Kitchen",
                }
            ],
            "HA_AREA_NAME_ASSIST_ENABLED": True,
            "HA_ADAPTER_AREA_MAP": {"11:22:33:44:55:66": {"area_id": "living-room", "area_name": "Living Room"}},
        },
        player_name="Kitchen @ Bridge",
        device_mac="AA:BB:CC:DD:EE:FF",
        adapter_mac="11:22:33:44:55:66",
    )

    assert resolved == {
        "room_id": "kitchen",
        "room_name": "Kitchen",
        "room_source": "manual",
        "room_confidence": "operator",
    }


def test_runtime_version_prefers_persisted_install_ref(tmp_path, monkeypatch):
    from config import get_installed_version_ref, get_runtime_version

    ref_file = tmp_path / ".release-ref"
    ref_file.write_text("v2.42.4-rc.2\n")
    monkeypatch.setenv("SENDSPIN_VERSION_REF_FILE", str(ref_file))

    assert get_installed_version_ref() == "v2.42.4-rc.2"
    assert get_runtime_version() == "2.42.4-rc.2"


def test_runtime_version_ignores_non_semver_install_ref(tmp_path, monkeypatch):
    from config import VERSION, get_installed_version_ref, get_runtime_version

    ref_file = tmp_path / ".release-ref"
    ref_file.write_text("main\n")
    monkeypatch.setenv("SENDSPIN_VERSION_REF_FILE", str(ref_file))

    assert get_installed_version_ref() is None
    assert get_runtime_version() == VERSION


def test_load_config_normalizes_optional_port_overrides(tmp_path):
    _write_config(tmp_path, {"WEB_PORT": "18080", "BASE_LISTEN_PORT": "19000"})
    from config import load_config

    loaded = load_config()

    assert loaded["WEB_PORT"] == 18080
    assert loaded["BASE_LISTEN_PORT"] == 19000


def test_load_config_clears_invalid_optional_port_overrides(tmp_path):
    _write_config(tmp_path, {"WEB_PORT": "99999", "BASE_LISTEN_PORT": "invalid"})
    from config import load_config

    loaded = load_config()

    assert loaded["WEB_PORT"] is None
    assert loaded["BASE_LISTEN_PORT"] is None


def test_load_config_normalizes_update_channel(tmp_path):
    _write_config(tmp_path, {"UPDATE_CHANNEL": "RC"})
    from config import load_config

    loaded = load_config()

    assert loaded["UPDATE_CHANNEL"] == "rc"


def test_load_config_falls_back_to_stable_for_invalid_update_channel(tmp_path):
    _write_config(tmp_path, {"UPDATE_CHANNEL": "nightly"})
    from config import DEFAULT_UPDATE_CHANNEL, load_config

    loaded = load_config()

    assert loaded["UPDATE_CHANNEL"] == DEFAULT_UPDATE_CHANNEL


def test_load_config_normalizes_ha_adapter_area_map(tmp_path):
    _write_config(
        tmp_path,
        {
            "HA_ADAPTER_AREA_MAP": {
                " aa:bb:cc:dd:ee:ff ": {"area_id": "living-room", "area_name": "Living Room"},
                "11:22:33:44:55:66": {"area_name": "Missing area id"},
                "bad": "invalid",
            }
        },
    )
    from config import load_config

    loaded = load_config()

    assert loaded["HA_ADAPTER_AREA_MAP"] == {
        "AA:BB:CC:DD:EE:FF": {"area_id": "living-room", "area_name": "Living Room"}
    }


def test_load_config_defaults_ha_area_name_assist_to_false_outside_addon(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    _write_config(tmp_path, {})
    from config import load_config

    loaded = load_config()

    assert loaded["HA_AREA_NAME_ASSIST_ENABLED"] is False


def test_load_config_defaults_ha_area_name_assist_to_true_in_ha_addon(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    _write_config(tmp_path, {})
    from config import load_config

    loaded = load_config()

    assert loaded["HA_AREA_NAME_ASSIST_ENABLED"] is True


def test_detect_ha_addon_channel_uses_hostname_suffix():
    from config import detect_ha_addon_channel

    assert (
        detect_ha_addon_channel(
            env={"SUPERVISOR_TOKEN": "token", "HOSTNAME": "85b1ecde-sendspin-bt-bridge-rc"},
        )
        == "rc"
    )
    assert (
        detect_ha_addon_channel(
            env={"SUPERVISOR_TOKEN": "token", "HOSTNAME": "85b1ecde-sendspin-bt-bridge-beta"},
        )
        == "beta"
    )
    assert (
        detect_ha_addon_channel(
            env={"SUPERVISOR_TOKEN": "token", "HOSTNAME": "85b1ecde-sendspin-bt-bridge"},
        )
        == "stable"
    )


def test_resolve_runtime_ports_follow_installed_addon_track_not_update_channel():
    from config import resolve_base_listen_port, resolve_web_port

    rc_env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge-rc",
        "UPDATE_CHANNEL": "stable",
    }
    stable_env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge",
        "UPDATE_CHANNEL": "beta",
    }

    assert resolve_web_port(env=rc_env) == 8081
    assert resolve_base_listen_port(env=rc_env) == 9028
    assert resolve_web_port(env=stable_env) == 8080
    assert resolve_base_listen_port(env=stable_env) == 8928


def test_resolve_runtime_ports_allow_base_port_env_override_in_ha_addon():
    from config import resolve_base_listen_port, resolve_web_port

    env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge-beta",
        "BASE_LISTEN_PORT": "19000",
    }

    assert resolve_web_port(env=env) == 8082
    assert resolve_base_listen_port(env=env) == 19000


def test_resolve_web_port_uses_ingress_port_from_supervisor_api():
    from unittest.mock import patch

    from config import resolve_web_port

    env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge",
    }
    with patch(
        "services.ha_addon.get_self_addon_info",
        return_value={"ingress_port": 38745},
    ):
        assert resolve_web_port(env=env) == 38745


def test_resolve_web_port_falls_back_to_channel_default_without_ingress_port():
    from unittest.mock import patch

    from config import resolve_web_port

    env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge",
    }
    with patch("services.ha_addon.get_self_addon_info", return_value=None):
        assert resolve_web_port(env=env) == 8080


def test_resolve_web_port_ingress_port_zero_falls_back_to_channel_default():
    from unittest.mock import patch

    from config import resolve_web_port

    env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge-rc",
    }
    with patch(
        "services.ha_addon.get_self_addon_info",
        return_value={"ingress_port": 0},
    ):
        assert resolve_web_port(env=env) == 8081


def test_resolve_additional_web_port_is_disabled_in_ha_addon():
    from config import resolve_additional_web_port

    env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge-beta",
        "WEB_PORT": "18080",
    }

    assert resolve_additional_web_port(env=env) is None


def test_resolve_ports_follow_saved_config_overrides(tmp_path):
    _write_config(tmp_path, {"WEB_PORT": 18080, "BASE_LISTEN_PORT": 19000})
    from config import resolve_base_listen_port, resolve_web_port

    assert resolve_web_port() == 18080
    assert resolve_base_listen_port() == 19000


def test_load_config_persists_current_schema_version_for_legacy_file(tmp_path):
    _write_config(tmp_path, {"SENDSPIN_SERVER": "10.0.0.1"})
    from config import CONFIG_SCHEMA_VERSION, load_config

    loaded = load_config()

    assert loaded["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION
    with open(tmp_path / "config.json") as f:
        saved = json.load(f)
    assert saved["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION


def test_update_config(tmp_path):
    """update_config should atomically modify config.json."""
    _write_config(tmp_path, {"SENDSPIN_PORT": 9000})
    from config import update_config

    update_config(lambda cfg: cfg.__setitem__("SENDSPIN_PORT", 1234))
    with open(tmp_path / "config.json") as f:
        assert json.load(f)["SENDSPIN_PORT"] == 1234


def test_load_config_logs_info_only_once_then_debug(tmp_path, caplog):
    _write_config(tmp_path, {"SENDSPIN_SERVER": "10.0.0.1"})
    import config

    with caplog.at_level("DEBUG", logger="config"):
        config.load_config()
        config.load_config()

    records = [record for record in caplog.records if "Loaded config from" in record.getMessage()]
    assert [record.levelname for record in records] == ["INFO", "DEBUG"]


def test_update_config_logs_info_for_real_config_change(tmp_path, caplog):
    _write_config(tmp_path, {"SENDSPIN_PORT": 9000})
    from config import update_config

    with caplog.at_level("DEBUG", logger="config"):
        update_config(lambda cfg: cfg.__setitem__("SENDSPIN_PORT", 1234))

    assert "Updated config at" in caplog.text
    assert "SENDSPIN_PORT" in caplog.text


def test_update_config_logs_debug_for_runtime_state_only_change(tmp_path, caplog):
    from config import CONFIG_SCHEMA_VERSION, update_config

    _write_config(tmp_path, {"CONFIG_SCHEMA_VERSION": CONFIG_SCHEMA_VERSION, "LAST_VOLUMES": {}})

    with caplog.at_level("DEBUG", logger="config"):
        update_config(lambda cfg: cfg.__setitem__("LAST_VOLUMES", {"AA:BB:CC:DD:EE:FF": 55}))

    records = [record for record in caplog.records if "Updated runtime config state in" in record.getMessage()]
    assert len(records) == 1
    assert records[0].levelname == "DEBUG"
    assert "LAST_VOLUMES" in records[0].getMessage()


def test_update_config_creates_dir(tmp_path, monkeypatch):
    """update_config should create CONFIG_DIR if it doesn't exist."""
    import config

    sub = tmp_path / "sub"
    monkeypatch.setattr(config, "CONFIG_DIR", sub)
    monkeypatch.setattr(config, "CONFIG_FILE", sub / "config.json")

    from config import update_config

    update_config(lambda cfg: cfg.__setitem__("key", "val"))
    assert sub.exists()
    with open(sub / "config.json") as f:
        assert json.load(f)["key"] == "val"


def test_config_default_has_ma_username():
    """DEFAULT_CONFIG includes MA_USERNAME."""
    from config import DEFAULT_CONFIG

    assert "MA_USERNAME" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["MA_USERNAME"] == ""
