"""Config schema v5 -> v6 migration."""

import copy
import json
from pathlib import Path

from sendspin_bridge.config.players import player_id_for_mac

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "config_v5_production.json"
ENEBY = "FC:58:FA:EB:08:6C"
YANDEX = "2C:D2:6B:B8:EC:5B"
LENCO = "30:21:0E:0A:AE:5A"


def _migrate(raw: dict):
    from sendspin_bridge.config import migrate_config_payload

    return migrate_config_payload(copy.deepcopy(raw))


def _production() -> dict:
    return json.loads(FIXTURE.read_text())


def test_schema_version_is_6():
    from sendspin_bridge.config.migration import CONFIG_SCHEMA_VERSION

    assert CONFIG_SCHEMA_VERSION == 6


def test_production_config_migrates_to_players():
    result = _migrate(_production())
    cfg = result.normalized_config
    assert result.needs_persist
    assert cfg["CONFIG_SCHEMA_VERSION"] == 6
    for legacy in ("BLUETOOTH_DEVICES", "LAST_VOLUMES", "LAST_SINKS"):
        assert legacy not in cfg
    players = cfg["players"]
    assert [p["name"] for p in players] == [
        "ENEBY20",
        "Yandex mini 2 007 a",
        "WH-1000XM4",
        "Lenco LS-500",
        "OpenMove AfterShokz",
        "ENEBY Portable",
    ]
    assert players[0]["id"] == player_id_for_mac(ENEBY)
    assert players[0]["backend"] == {"type": "bluetooth_a2dp", "mac": ENEBY, "adapter": "hci0"}
    assert players[0]["listen_port"] == 8928
    assert players[2]["enabled"] is False
    assert players[5]["keepalive_interval"] == 30


def test_runtime_state_moves_to_player_state():
    state = _migrate(_production()).normalized_config["PLAYER_STATE"]
    assert state[player_id_for_mac(ENEBY)] == {"volume": 45, "sink": "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"}
    # Review focus 1: a lower-case LAST_VOLUMES key still finds its speaker.
    assert state[player_id_for_mac(YANDEX)] == {"volume": 30}
    # Review focus 3: released + who released it survive.
    assert state[player_id_for_mac(LENCO)] == {"released": True, "released_by": "user"}
    # A volume for a speaker that is not configured is dropped.
    assert player_id_for_mac("00:00:00:00:00:01") not in state


def test_migration_is_idempotent():
    once = _migrate(_production()).normalized_config
    twice = _migrate(once)
    assert twice.normalized_config == once
    assert not twice.needs_persist


def test_malformed_entries_are_skipped_with_warnings():
    raw = {
        "CONFIG_SCHEMA_VERSION": 5,
        "BLUETOOTH_DEVICES": [None, {"player_name": "no mac"}, {"mac": "nope"}, {"mac": ENEBY}, {"mac": ENEBY.lower()}],
    }
    result = _migrate(raw)
    assert [p["backend"]["mac"] for p in result.normalized_config["players"]] == [ENEBY]
    messages = " ".join(issue.message for issue in result.warnings)
    assert "without a valid MAC" in messages
    assert "duplicate" in messages


def test_players_win_over_leftover_bluetooth_devices():
    raw = {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [{"id": "keep", "name": "Kept", "backend": {"type": "bluetooth_a2dp", "mac": ENEBY}}],
        "BLUETOOTH_DEVICES": [{"mac": YANDEX}],
    }
    result = _migrate(raw)
    assert [p["id"] for p in result.normalized_config["players"]] == ["keep"]
    assert "BLUETOOTH_DEVICES" not in result.normalized_config
    assert result.needs_persist
    assert any("ignored" in issue.message for issue in result.warnings)


def test_newer_schema_is_left_alone():
    raw = {"CONFIG_SCHEMA_VERSION": 7, "players": [], "BLUETOOTH_DEVICES": [{"mac": ENEBY}]}
    result = _migrate(raw)
    assert result.normalized_config["CONFIG_SCHEMA_VERSION"] == 7
    assert "players" in result.normalized_config
    assert result.normalized_config["players"] == []


def test_legacy_v5_device_fixes_still_apply_before_conversion():
    raw = {
        "CONFIG_SCHEMA_VERSION": 4,
        "BLUETOOTH_DEVICES": [{"mac": ENEBY, "static_delay_ms": -300, "handoff_mode": "x"}],
    }
    player = _migrate(raw).normalized_config["players"][0]
    assert player["static_delay_ms"] == 0
    assert "handoff_mode" not in player


def test_load_config_normalizes_players_and_prunes_state(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    pid = player_id_for_mac(ENEBY)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {"id": pid, "name": "A", "backend": {"type": "bluetooth_a2dp", "mac": ENEBY}},
                    {"id": "bad", "backend": {"type": "warp_drive"}},
                ],
                "PLAYER_STATE": {pid: {"volume": 150, "sink": "s"}, "ghost": {"volume": 10}},
            }
        )
    )
    loaded = config.load_config()
    assert [p["id"] for p in loaded["players"]] == [pid]
    assert loaded["PLAYER_STATE"] == {pid: {"sink": "s"}}


def test_player_with_out_of_range_setting_is_kept(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    pid = player_id_for_mac(ENEBY)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": pid,
                        "name": "A",
                        "idle_mode": "warp",
                        "backend": {"type": "bluetooth_a2dp", "mac": ENEBY},
                    }
                ],
            }
        )
    )
    loaded = config.load_config()
    assert [p["id"] for p in loaded["players"]] == [pid]
    assert loaded["players"][0]["idle_mode"] == "warp"
