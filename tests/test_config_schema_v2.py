"""Tests for config schema v2: players[] array and Player/PlayerBackend definitions."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "config.schema.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


# ── Schema version ───────────────────────────────────────────────────────


def test_config_schema_version_is_2():
    from config_migration import CONFIG_SCHEMA_VERSION

    assert CONFIG_SCHEMA_VERSION == 2


def test_schema_json_const_is_2():
    schema = _load_schema()
    assert schema["properties"]["CONFIG_SCHEMA_VERSION"]["const"] == 2


# ── players key in allowed config keys ───────────────────────────────────


def test_players_in_config_allowed_keys():
    from config import CONFIG_ALLOWED_KEYS

    assert "players" in CONFIG_ALLOWED_KEYS


def test_players_in_default_config():
    from config import DEFAULT_CONFIG

    assert "players" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["players"] == []


# ── players array in schema ──────────────────────────────────────────────


def test_schema_has_players_property():
    schema = _load_schema()
    assert "players" in schema["properties"]


def test_players_schema_is_array_with_ref():
    schema = _load_schema()
    players_prop = schema["properties"]["players"]
    assert players_prop["type"] == "array"
    assert players_prop["default"] == []
    assert players_prop["items"]["$ref"] == "#/$defs/Player"


# ── Player definition ────────────────────────────────────────────────────


def test_player_definition_exists():
    schema = _load_schema()
    assert "Player" in schema["$defs"]


def test_player_required_fields():
    schema = _load_schema()
    player_def = schema["$defs"]["Player"]
    assert "player_name" in player_def["required"]
    assert "backend" in player_def["required"]


def test_player_has_expected_properties():
    schema = _load_schema()
    player_props = schema["$defs"]["Player"]["properties"]
    expected_keys = {
        "id",
        "player_name",
        "backend",
        "enabled",
        "listen_port",
        "static_delay_ms",
        "handoff_mode",
        "volume_controller",
        "idle_disconnect_minutes",
        "keepalive_enabled",
        "keepalive_interval",
        "room_id",
        "room_name",
    }
    assert expected_keys == set(player_props.keys())


def test_player_disallows_additional_properties():
    schema = _load_schema()
    assert schema["$defs"]["Player"]["additionalProperties"] is False


def test_player_backend_is_ref():
    schema = _load_schema()
    player_props = schema["$defs"]["Player"]["properties"]
    assert player_props["backend"]["$ref"] == "#/$defs/PlayerBackend"


# ── PlayerBackend definition ─────────────────────────────────────────────


def test_player_backend_definition_exists():
    schema = _load_schema()
    assert "PlayerBackend" in schema["$defs"]


def test_player_backend_required_type():
    schema = _load_schema()
    backend_def = schema["$defs"]["PlayerBackend"]
    assert "type" in backend_def["required"]


def test_player_backend_type_enum():
    schema = _load_schema()
    backend_props = schema["$defs"]["PlayerBackend"]["properties"]
    assert backend_props["type"]["enum"] == ["bluetooth_a2dp", "local_sink", "snapcast"]


def test_player_backend_has_mac_and_adapter():
    schema = _load_schema()
    backend_props = schema["$defs"]["PlayerBackend"]["properties"]
    assert "mac" in backend_props
    assert "adapter" in backend_props
    assert backend_props["mac"]["pattern"] == "^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"


def test_player_backend_allows_additional_properties():
    """PlayerBackend is extensible for future backend types."""
    schema = _load_schema()
    assert schema["$defs"]["PlayerBackend"]["additionalProperties"] is True


# ── Backward compatibility ───────────────────────────────────────────────


def test_bluetooth_devices_still_in_schema():
    schema = _load_schema()
    assert "BLUETOOTH_DEVICES" in schema["properties"]
    assert "BluetoothDevice" in schema["$defs"]


def test_bluetooth_devices_still_in_allowed_keys():
    from config import CONFIG_ALLOWED_KEYS

    assert "BLUETOOTH_DEVICES" in CONFIG_ALLOWED_KEYS
