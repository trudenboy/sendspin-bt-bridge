"""Tests for config.py — pure functions that don't require hardware."""

import json

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory for every test."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


def _write_config(tmp_path, data):
    (tmp_path / "config.json").write_text(json.dumps(data))


def test_load_defaults_when_no_file():
    from config import CONFIG_SCHEMA_VERSION, DEFAULT_CONFIG, load_config

    cfg = load_config()
    for key, default in DEFAULT_CONFIG.items():
        assert cfg[key] == default
    assert cfg["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION


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


def test_load_volume_via_ma(tmp_path):
    """VOLUME_VIA_MA must survive load_config() round-trip."""
    _write_config(tmp_path, {"VOLUME_VIA_MA": False})
    from config import load_config

    assert load_config()["VOLUME_VIA_MA"] is False


def test_load_ma_auto_silent_auth(tmp_path):
    """MA_AUTO_SILENT_AUTH must survive load_config() round-trip."""
    _write_config(tmp_path, {"MA_AUTO_SILENT_AUTH": False})
    from config import load_config

    assert load_config()["MA_AUTO_SILENT_AUTH"] is False


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


def test_resolve_additional_web_port_uses_explicit_ha_override():
    from config import resolve_additional_web_port

    env = {
        "SUPERVISOR_TOKEN": "token",
        "HOSTNAME": "85b1ecde-sendspin-bt-bridge-beta",
        "WEB_PORT": "18080",
    }

    assert resolve_additional_web_port(env=env) == 18080


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
