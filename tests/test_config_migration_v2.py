"""Tests for v1→v2 config migration (BLUETOOTH_DEVICES → players[])."""

import json
import uuid

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


def _migrate(data):
    """Run migrate_config_payload via the config.py wrapper (supplies allowed_keys)."""
    from config import migrate_config_payload

    return migrate_config_payload(data)


# ---------------------------------------------------------------------------
# Basic migration
# ---------------------------------------------------------------------------


def test_empty_config_no_players_migration():
    """Empty config has no BLUETOOTH_DEVICES → players key is not added by migration."""
    result = _migrate({})
    assert "players" not in result.normalized_config


def test_single_device_migrated_to_player():
    """A single BT device becomes a v2 player entry."""
    cfg = {
        "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1"}],
    }
    result = _migrate(cfg)
    players = result.normalized_config["players"]
    assert len(players) == 1
    p = players[0]
    assert p["player_name"] == "Speaker1"
    assert p["backend"]["type"] == "bluetooth_a2dp"
    assert p["backend"]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert p["enabled"] is True
    assert result.needs_persist is True


def test_multiple_devices_migrated():
    """Multiple BT devices produce multiple player entries in order."""
    cfg = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1"},
            {"mac": "11:22:33:44:55:66", "player_name": "Speaker2"},
        ],
    }
    result = _migrate(cfg)
    players = result.normalized_config["players"]
    assert len(players) == 2
    assert players[0]["player_name"] == "Speaker1"
    assert players[0]["backend"]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert players[1]["player_name"] == "Speaker2"
    assert players[1]["backend"]["mac"] == "11:22:33:44:55:66"


def test_all_device_fields_migrated():
    """Every optional BT device field maps to the correct v2 player field."""
    cfg = {
        "BLUETOOTH_DEVICES": [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "player_name": "Full Speaker",
                "adapter": "hci0",
                "listen_port": 8928,
                "delay_ms": -600,
                "enabled": False,
                "handoff_mode": "fast_handoff",
                "volume_controller": "ma",
                "room_id": "room-123",
                "room_name": "Living Room",
                "keepalive_enabled": True,
                "keepalive_interval": 60,
                "idle_disconnect_minutes": 30,
            }
        ],
    }
    result = _migrate(cfg)
    p = result.normalized_config["players"][0]
    assert p["backend"]["adapter"] == "hci0"
    assert p["listen_port"] == 8928
    assert p["static_delay_ms"] == -600
    assert p["enabled"] is False
    assert p["handoff_mode"] == "fast_handoff"
    assert p["volume_controller"] == "ma"
    assert p["room_id"] == "room-123"
    assert p["room_name"] == "Living Room"
    assert p["keepalive_enabled"] is True
    assert p["keepalive_interval"] == 60
    assert p["idle_disconnect_minutes"] == 30


# ---------------------------------------------------------------------------
# Player ID
# ---------------------------------------------------------------------------


def test_player_id_derived_from_mac_uuid5():
    """Player ID is uuid5(NAMESPACE_DNS, mac.lower())."""
    mac = "AA:BB:CC:DD:EE:FF"
    cfg = {"BLUETOOTH_DEVICES": [{"mac": mac, "player_name": "S"}]}
    result = _migrate(cfg)
    actual_id = result.normalized_config["players"][0]["id"]
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, mac.lower()))
    assert actual_id == expected_id


# ---------------------------------------------------------------------------
# LAST_VOLUMES / LAST_SINKS migration
# ---------------------------------------------------------------------------


def test_last_volumes_mac_preserved_player_id_added():
    """LAST_VOLUMES keeps the MAC key and adds a player_id alias."""
    mac = "AA:BB:CC:DD:EE:FF"
    cfg = {
        "BLUETOOTH_DEVICES": [{"mac": mac, "player_name": "S"}],
        "LAST_VOLUMES": {mac: 42},
    }
    result = _migrate(cfg)
    volumes = result.normalized_config["LAST_VOLUMES"]
    assert volumes[mac] == 42

    from config import _player_id_from_mac

    pid = _player_id_from_mac(mac)
    assert volumes[pid] == 42


def test_last_sinks_mac_preserved_player_id_added():
    """LAST_SINKS keeps the MAC key and adds a player_id alias."""
    mac = "AA:BB:CC:DD:EE:FF"
    sink = "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
    cfg = {
        "BLUETOOTH_DEVICES": [{"mac": mac, "player_name": "S"}],
        "LAST_SINKS": {mac: sink},
    }
    result = _migrate(cfg)
    sinks = result.normalized_config["LAST_SINKS"]
    assert sinks[mac] == sink

    from config import _player_id_from_mac

    pid = _player_id_from_mac(mac)
    assert sinks[pid] == sink


# ---------------------------------------------------------------------------
# Skip / no-op cases
# ---------------------------------------------------------------------------


def test_existing_players_not_remigrated():
    """Config that already has players[] is NOT re-migrated from devices."""
    cfg = {
        "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Old"}],
        "players": [{"id": "custom-id", "player_name": "Custom"}],
    }
    result = _migrate(cfg)
    players = result.normalized_config["players"]
    assert len(players) == 1
    assert players[0]["id"] == "custom-id"
    assert players[0]["player_name"] == "Custom"


def test_both_devices_and_players_players_wins():
    """When both BLUETOOTH_DEVICES and players[] exist, players wins."""
    cfg = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "DeviceSpeaker"},
        ],
        "players": [
            {"id": "p1", "player_name": "Player1"},
            {"id": "p2", "player_name": "Player2"},
        ],
    }
    result = _migrate(cfg)
    players = result.normalized_config["players"]
    assert len(players) == 2
    assert players[0]["id"] == "p1"
    assert players[1]["id"] == "p2"
    # No migration warning for players since they already exist
    player_warnings = [w for w in result.warnings if w.field == "players"]
    assert not player_warnings


def test_empty_devices_list_no_migration():
    """BLUETOOTH_DEVICES: [] does not trigger migration (no devices to convert)."""
    cfg = {"BLUETOOTH_DEVICES": []}
    result = _migrate(cfg)
    assert "players" not in result.normalized_config


def test_empty_devices_list_defaults_to_empty_players(tmp_path):
    """Through load_config, empty BLUETOOTH_DEVICES still yields players: [] from defaults."""
    _write_config(tmp_path, {"BLUETOOTH_DEVICES": []})
    from config import load_config

    cfg = load_config()
    assert cfg["players"] == []


# ---------------------------------------------------------------------------
# Migration warning
# ---------------------------------------------------------------------------


def test_migration_warning_emitted():
    """Migration appends a warning about the number of migrated devices."""
    cfg = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "S1"},
            {"mac": "11:22:33:44:55:66", "player_name": "S2"},
        ],
    }
    result = _migrate(cfg)
    player_warnings = [w for w in result.warnings if w.field == "players"]
    assert len(player_warnings) == 1
    assert "Migrated 2 device(s)" in player_warnings[0].message
    assert "schema v2" in player_warnings[0].message


# ---------------------------------------------------------------------------
# Round-trip: migrate → persist → reload → stable
# ---------------------------------------------------------------------------


def test_round_trip_migrate_save_reload(tmp_path):
    """First load migrates and persists; second load reads the same players."""
    _write_config(
        tmp_path,
        {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Speaker1"},
                {"mac": "11:22:33:44:55:66", "player_name": "Speaker2"},
            ],
            "LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 75},
        },
    )
    from config import load_config

    first = load_config()
    assert len(first["players"]) == 2
    assert first["players"][0]["player_name"] == "Speaker1"

    # Reset the logged-once flag so second load also works
    import config

    config._config_load_logged_once = False

    second = load_config()
    assert second["players"] == first["players"]
