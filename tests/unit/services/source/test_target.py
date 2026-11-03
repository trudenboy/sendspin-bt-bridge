"""Choosing a Music Assistant player/group for the Bluetooth source."""

from __future__ import annotations

from sendspin_bridge.services.source import target

PLAYERS = [
    {"player_id": "kitchen", "display_name": "Kitchen", "type": "player", "available": True},
    {"player_id": "house", "display_name": "House", "type": "group", "available": True},
    {
        "player_id": "source-1",
        "display_name": "Sendspin BT Bridge",
        "type": "source",
        "source_list": [{"id": "sendspin_source://audio_source/peer-1"}],
    },
    {"player_id": "offline", "name": "Offline", "type": "player", "available": False},
]


def test_targets_are_groups_then_players_and_never_the_source_itself():
    targets, uri = target.targets_from_players(PLAYERS, "peer-1")

    assert [entry["player_id"] for entry in targets] == ["house", "kitchen", "offline"]
    assert targets[0]["type"] == "group"
    assert targets[2]["available"] is False
    assert uri == "sendspin_source://audio_source/peer-1"


def test_a_source_uri_falls_back_to_the_stable_template_without_a_matching_source():
    _targets, uri = target.targets_from_players(PLAYERS, "another-peer")

    assert uri == "sendspin_source://audio_source/another-peer"


def test_only_players_and_groups_are_offerable():
    targets, _uri = target.targets_from_players(
        [
            {"player_id": "a", "name": "A", "type": "player"},
            {"player_id": "s", "name": "S", "type": "source"},
            {"player_id": "x", "name": "X", "type": "plugin"},
        ],
        None,
    )

    assert [entry["player_id"] for entry in targets] == ["a"]
