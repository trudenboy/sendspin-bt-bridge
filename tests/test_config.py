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


def test_save_device_volume(tmp_path):
    _write_config(tmp_path, {})
    from config import save_device_volume

    save_device_volume("AA:BB:CC:DD:EE:FF", 75)
    with open(tmp_path / "config.json") as f:
        assert json.load(f)["LAST_VOLUMES"]["AA:BB:CC:DD:EE:FF"] == 75


def test_save_volume_zero(tmp_path):
    """Regression: volume=0 must not be treated as falsy."""
    _write_config(tmp_path, {})
    from config import save_device_volume

    save_device_volume("AA:BB:CC:DD:EE:FF", 0)
    with open(tmp_path / "config.json") as f:
        assert json.load(f)["LAST_VOLUMES"]["AA:BB:CC:DD:EE:FF"] == 0


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
