"""``schema.json`` describes the v6 file: ``players`` + ``PLAYER_STATE``, no v5 keys."""

import json
from pathlib import Path

from sendspin_bridge.config.migration import CONFIG_SCHEMA_VERSION
from sendspin_bridge.config.players import Player

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json").read_text()
)


def test_schema_version_is_the_runtime_version():
    assert SCHEMA["properties"]["CONFIG_SCHEMA_VERSION"]["const"] == CONFIG_SCHEMA_VERSION == 6


def test_v5_keys_are_gone():
    for key in ("BLUETOOTH_DEVICES", "LAST_VOLUMES", "LAST_SINKS"):
        assert key not in SCHEMA["properties"]
    assert "BluetoothDevice" not in SCHEMA["$defs"]


def test_players_are_player_definitions_with_every_model_field():
    assert SCHEMA["properties"]["players"]["items"] == {"$ref": "#/$defs/Player"}
    player = SCHEMA["$defs"]["Player"]
    assert set(player["properties"]) == set(Player.model_json_schema()["properties"])
    assert set(player["required"]) == {"id", "backend"}
    for backend in ("BluetoothBackend", "PulseSinkBackend", "SinkMatch"):
        assert backend in SCHEMA["$defs"]


def test_player_state_shape():
    state = SCHEMA["properties"]["PLAYER_STATE"]["additionalProperties"]["properties"]
    assert state["volume"] == {"type": "integer", "minimum": 0, "maximum": 100}
    assert set(state) == {"volume", "sink", "released", "released_by"}
