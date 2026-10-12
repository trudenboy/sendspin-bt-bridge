"""Per-player values persist into players / PLAYER_STATE (schema v6)."""

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
                "players": [{"id": PID, "name": "S", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}],
            }
        )
    )


def _disk(tmp_path):
    return json.loads((tmp_path / "config.json").read_text())


def test_volume_goes_to_player_state(tmp_path):
    from sendspin_bridge.config import save_device_volume

    save_device_volume(MAC.lower(), 37)
    assert _disk(tmp_path)["PLAYER_STATE"][PID]["volume"] == 37
    assert "LAST_VOLUMES" not in _disk(tmp_path)


def test_volume_for_unknown_speaker_is_not_saved(tmp_path):
    from sendspin_bridge.config import save_device_volume

    save_device_volume("11:22:33:44:55:66", 50)
    assert _disk(tmp_path).get("PLAYER_STATE", {}) == {}


def test_sink_goes_to_player_state(tmp_path):
    from sendspin_bridge.config import save_device_sink

    save_device_sink(MAC, " bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink ")
    assert _disk(tmp_path)["PLAYER_STATE"][PID]["sink"] == "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"


def test_static_delay_goes_to_the_player(tmp_path):
    # Review focus 5: an MA-pushed delay after migration lands on the player.
    from sendspin_bridge.config import save_device_static_delay

    save_device_static_delay(MAC, 640, source="ma", codec="sbc")
    player = _disk(tmp_path)["players"][0]
    assert player["static_delay_ms"] == 640
    assert player["static_delay_source"] == "ma"
    assert player["static_delay_codec"] == "sbc"


def test_buffer_setting_goes_to_the_player(tmp_path):
    from sendspin_bridge.config import save_device_buffer_setting

    save_device_buffer_setting(MAC, "min_buffer_ms", 400)
    assert _disk(tmp_path)["players"][0]["min_buffer_ms"] == 400


def test_load_player_state_for_mac_reads_v5_and_v6(tmp_path):
    from sendspin_bridge.config import load_player_state_for_mac, save_device_volume

    save_device_volume(MAC, 20)
    assert load_player_state_for_mac(MAC) == {"volume": 20}
    (tmp_path / "config.json").write_text(json.dumps({"BLUETOOTH_DEVICES": [{"mac": MAC}], "LAST_VOLUMES": {MAC: 70}}))
    assert load_player_state_for_mac(MAC) == {"volume": 70}
    assert load_player_state_for_mac("11:22:33:44:55:66") == {}
