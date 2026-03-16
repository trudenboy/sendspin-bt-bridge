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
    from config import DEFAULT_CONFIG, load_config

    cfg = load_config()
    for key, default in DEFAULT_CONFIG.items():
        assert cfg[key] == default


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


def test_load_config_normalizes_types_and_prunes_orphan_volumes(tmp_path):
    _write_config(
        tmp_path,
        {
            "SENDSPIN_PORT": "9001",
            "BT_CHECK_INTERVAL": "15",
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
    assert loaded["PREFER_SBC_CODEC"] is True
    assert loaded["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert loaded["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 55}


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
