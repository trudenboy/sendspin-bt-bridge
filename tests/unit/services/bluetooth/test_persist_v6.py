"""Enabled / released flags persist into players and PLAYER_STATE (schema v6)."""

import json

import pytest

from sendspin_bridge.config.players import player_id_for_mac

MAC = "AA:BB:CC:DD:EE:FF"
PID = player_id_for_mac(MAC)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [{"id": PID, "name": "Kitchen", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}],
            }
        )
    )
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"bluetooth_devices": [{"mac": MAC, "player_name": "Kitchen"}]}))
    import sendspin_bridge.services.bluetooth as bt_svc

    monkeypatch.setattr(bt_svc, "_OPTIONS_FILE", options)
    return tmp_path


def _disk(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_enabled_flag_on_player_and_not_in_options(tmp_path):
    from sendspin_bridge.services.bluetooth import persist_device_enabled

    before = (tmp_path / "options.json").read_text()
    persist_device_enabled("Kitchen", False)
    assert _disk(tmp_path)["players"][0]["enabled"] is False
    assert (tmp_path / "options.json").read_text() == before


def test_released_flag_in_player_state(tmp_path):
    from sendspin_bridge.services.bluetooth import persist_device_released

    persist_device_released("Kitchen", True, released_by="user")
    assert _disk(tmp_path)["PLAYER_STATE"][PID] == {"released": True, "released_by": "user"}
    persist_device_released("Kitchen", False)
    assert _disk(tmp_path)["PLAYER_STATE"][PID] == {"released": False}


def test_apply_device_enabled_finds_disabled_player_by_id(tmp_path, monkeypatch):
    import sendspin_bridge.services.bluetooth.bt_commands as bt_commands

    monkeypatch.setattr(bt_commands, "find_client_by_player_id", lambda _pid: None)
    monkeypatch.setattr(bt_commands, "diff_configs", lambda old, new: [])
    result = bt_commands.apply_device_enabled(PID, False)
    assert result.success
    assert _disk(tmp_path)["players"][0]["enabled"] is False
