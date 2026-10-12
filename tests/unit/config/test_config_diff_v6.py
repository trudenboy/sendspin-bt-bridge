"""config_diff reads schema v6 configs."""

from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs
from tests.support.config_v6 import v6_config

A = "AA:BB:CC:DD:EE:01"
B = "AA:BB:CC:DD:EE:02"


def _cfg(*devices):
    return v6_config(list(devices))


def test_unchanged_players_produce_no_actions():
    cfg = _cfg({"mac": A, "player_name": "A"})
    assert diff_configs(cfg, cfg) == []


def test_added_player_starts_with_its_list_index():
    actions = diff_configs(_cfg({"mac": A}), _cfg({"mac": A}, {"mac": B}))
    start = [a for a in actions if a.kind == ActionKind.START_CLIENT]
    assert [a.mac for a in start] == [B]
    assert start[0].payload["device"]["mac"] == B
    assert start[0].payload["device_index"] == 1


def test_volume_state_change_is_not_a_config_change():
    old = _cfg({"mac": A})
    new = _cfg({"mac": A})
    new["PLAYER_STATE"] = {old["players"][0]["id"]: {"volume": 10}}
    assert diff_configs(old, new) == []
