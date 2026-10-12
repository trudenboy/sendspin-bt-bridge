"""Schema v6 player models and the v5 <-> v6 conversions."""

import uuid

import pytest
from pydantic import ValidationError

from sendspin_bridge.config.players import (
    Player,
    device_entries,
    find_player_by_id,
    find_player_by_mac,
    get_players,
    legacy_device_to_player,
    player_id_for_mac,
    player_state,
    player_to_device_entry,
)

MAC = "FC:58:FA:EB:08:6C"


def test_player_id_matches_legacy_derivation():
    from sendspin_bridge.config import _player_id_from_mac

    assert player_id_for_mac(MAC) == str(uuid.uuid5(uuid.NAMESPACE_DNS, MAC.lower()))
    assert player_id_for_mac(MAC) == _player_id_from_mac(MAC)
    assert player_id_for_mac(" fc:58:fa:eb:08:6c ") == player_id_for_mac(MAC)


def test_legacy_device_to_player_moves_bt_fields_into_backend():
    player = legacy_device_to_player(
        {
            "mac": MAC.lower(),
            "adapter": "hci0",
            "player_name": "ENEBY20",
            "enabled": False,
            "static_delay_ms": 120,
            "idle_mode": "power_save",
            "keepalive_interval": 30,
            "released": True,
            "released_by": "user",
        }
    )
    assert player["id"] == player_id_for_mac(MAC)
    assert player["name"] == "ENEBY20"
    assert player["backend"] == {"type": "bluetooth_a2dp", "mac": MAC, "adapter": "hci0"}
    assert player["enabled"] is False
    assert player["static_delay_ms"] == 120
    assert player["keepalive_interval"] == 30  # legacy extra kept until stage 3
    for moved in ("mac", "adapter", "player_name", "released", "released_by"):
        assert moved not in player


def test_legacy_device_to_player_keeps_existing_id():
    player = legacy_device_to_player({"mac": MAC}, existing_id="custom-id")
    assert player["id"] == "custom-id"


def test_legacy_device_to_player_name_falls_back_to_mac():
    assert legacy_device_to_player({"mac": MAC})["name"] == MAC


@pytest.mark.parametrize("bad", [{}, {"mac": ""}, {"mac": "not-a-mac"}, {"mac": 42}])
def test_legacy_device_to_player_rejects_missing_or_bad_mac(bad):
    with pytest.raises(ValueError):
        legacy_device_to_player(bad)


def test_player_to_device_entry_round_trips_v5_shape():
    device = {"mac": MAC, "adapter": "hci1", "player_name": "Kitchen", "listen_port": 8930}
    entry = player_to_device_entry(legacy_device_to_player(device))
    assert entry == {**device, "id": player_id_for_mac(MAC)}


def test_player_to_device_entry_skips_pulse_sink():
    assert player_to_device_entry({"id": "x", "backend": {"type": "pulse_sink", "sink": "s"}}) is None


def test_device_entries_merges_released_state_in_list_order():
    other = "2C:D2:6B:B8:EC:5B"
    config = {
        "players": [
            legacy_device_to_player({"mac": other, "player_name": "B"}),
            legacy_device_to_player({"mac": MAC, "player_name": "A"}),
            {"id": "sink-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "alsa_output.x"}},
        ],
        "PLAYER_STATE": {player_id_for_mac(MAC): {"released": True, "released_by": "auto", "volume": 40}},
    }
    entries = device_entries(config)
    assert [e["player_name"] for e in entries] == ["B", "A"]
    assert entries[1]["released"] is True
    assert entries[1]["released_by"] == "auto"
    assert "volume" not in entries[1]
    assert "released" not in entries[0]


def test_device_entries_tolerates_garbage():
    assert device_entries({}) == []
    assert device_entries({"players": "nope"}) == []
    assert device_entries({"players": [None, {"backend": None}, {"backend": {"type": "bluetooth_a2dp"}}]}) == []


def test_get_players_validates_and_skips_invalid(caplog):
    config = {
        "players": [
            legacy_device_to_player({"mac": MAC}),
            {"id": "", "backend": {"type": "bluetooth_a2dp", "mac": MAC}},
            {"id": "p", "backend": {"type": "pulse_sink", "sink": "s"}, "idle_mode": "auto_disconnect"},
        ]
    }
    players = get_players(config)
    assert [p.id for p in players] == [player_id_for_mac(MAC)]
    assert "Skipping invalid player" in caplog.text


def test_pulse_sink_rejects_bluetooth_only_options():
    with pytest.raises(ValidationError):
        Player.model_validate(
            {"id": "p", "backend": {"type": "pulse_sink", "sink": "s"}, "keep_alive_method": "infrasound"}
        )


def test_find_helpers_return_live_dicts():
    config = {"players": [legacy_device_to_player({"mac": MAC})]}
    found = find_player_by_mac(config, MAC.lower())
    found["name"] = "changed"
    assert config["players"][0]["name"] == "changed"
    assert find_player_by_id(config, player_id_for_mac(MAC)) is config["players"][0]
    assert find_player_by_mac(config, "00:00:00:00:00:00") is None


def test_player_state_creates_and_returns_live_entry():
    config: dict = {}
    player_state(config, "abc")["volume"] = 30
    assert config == {"PLAYER_STATE": {"abc": {"volume": 30}}}


def test_supported_actions_per_backend():
    from sendspin_bridge.config.players import supported_actions_for

    assert supported_actions_for("bluetooth_a2dp") == [
        "volume",
        "transport",
        "power_save",
        "reconnect",
        "repair",
        "standby",
        "claim",
        "pairing_window",
        "rssi",
        "battery",
    ]
    assert supported_actions_for("pulse_sink") == ["volume", "transport", "power_save"]
    assert supported_actions_for("unknown") == []
