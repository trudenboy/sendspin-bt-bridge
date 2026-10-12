"""validate_uploaded_config speaks schema v6."""

from sendspin_bridge.config.players import player_id_for_mac
from sendspin_bridge.services.infrastructure.config_validation import validate_uploaded_config

A = "AA:BB:CC:DD:EE:01"
B = "AA:BB:CC:DD:EE:02"


def _bt(mac, **extra):
    return {"name": mac, "backend": {"type": "bluetooth_a2dp", "mac": mac}, **extra}


def test_v5_upload_is_returned_as_players():
    result = validate_uploaded_config({"BLUETOOTH_DEVICES": [{"mac": A.lower()}]}, default_base_listen_port=8928)
    assert result.is_valid
    players = result.normalized_config["players"]
    assert players[0]["backend"]["mac"] == A
    assert players[0]["id"] == player_id_for_mac(A)
    assert "BLUETOOTH_DEVICES" not in result.normalized_config


def test_error_paths_point_at_the_player_index():
    # Review focus 5: a pulse_sink player first, the bad Bluetooth one third.
    payload = {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [
            {"id": "dac", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "alsa_output.x"}},
            _bt(A),
            _bt(B, listen_port=80),
        ],
    }
    result = validate_uploaded_config(payload, default_base_listen_port=8928)
    assert not result.is_valid
    assert [e.field for e in result.errors] == ["players[2].listen_port"]


def test_duplicate_effective_ports_are_reported_on_players():
    payload = {"CONFIG_SCHEMA_VERSION": 6, "players": [_bt(A, listen_port=9001), _bt(B, listen_port=9001)]}
    result = validate_uploaded_config(payload, default_base_listen_port=8928)
    assert any(e.field == "players[1].listen_port" for e in result.errors)


def test_pulse_sink_model_errors_use_backend_paths_without_the_union_tag():
    payload = {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [_bt(A), {"name": "DAC", "backend": {"type": "pulse_sink", "sink": ""}}],
    }
    result = validate_uploaded_config(payload, default_base_listen_port=8928)
    assert [e.field for e in result.errors] == ["players[1].backend.sink"]


def test_new_pulse_sink_player_without_id_is_valid_and_keeps_no_id():
    payload = {
        "CONFIG_SCHEMA_VERSION": 6,
        "players": [{"name": "DAC", "listen_port": "9100", "backend": {"type": "pulse_sink", "sink": "alsa_output.x"}}],
    }
    result = validate_uploaded_config(payload, default_base_listen_port=8928)
    assert result.is_valid
    player = result.normalized_config["players"][0]
    assert "id" not in player
    assert player["listen_port"] == 9100


def test_bluetooth_player_without_mac_is_an_error():
    payload = {"CONFIG_SCHEMA_VERSION": 6, "players": [{"name": "X", "backend": {"type": "bluetooth_a2dp"}}]}
    result = validate_uploaded_config(payload, default_base_listen_port=8928)
    assert [e.field for e in result.errors] == ["players[0].backend.mac"]
