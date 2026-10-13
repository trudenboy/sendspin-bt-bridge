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


# ---------------------------------------------------------------------------
# Players are identified by id, not by MAC (stage 3)
# ---------------------------------------------------------------------------


def _sink_player(pid="dac-1", sink="alsa_output.usb-dac", **extra):
    return {"id": pid, "name": f"DAC {pid}", "backend": {"type": "pulse_sink", "sink": sink}, **extra}


def _with(cfg, *players):
    return {**cfg, "players": [*cfg["players"], *players]}


def test_adding_a_pulse_sink_player_starts_it_by_player_id():
    old = _cfg({"mac": A})
    new = _with(_cfg({"mac": A}), _sink_player())

    actions = diff_configs(old, new)

    assert [a.kind for a in actions] == [ActionKind.START_CLIENT]
    (start,) = actions
    assert start.player_id == "dac-1"
    assert start.mac is None
    assert start.label == "DAC dac-1"
    assert start.payload["device"] == _sink_player()


def test_removing_a_pulse_sink_player_stops_it_by_player_id():
    old = _with(_cfg({"mac": A}), _sink_player())
    new = _cfg({"mac": A})

    actions = diff_configs(old, new)

    assert [(a.kind, a.player_id, a.mac, a.fields) for a in actions] == [
        (ActionKind.STOP_CLIENT, "dac-1", None, ["removed"])
    ]


def test_disabling_a_pulse_sink_player_stops_it():
    old = _with(_cfg(), _sink_player())
    new = _with(_cfg(), _sink_player(enabled=False))

    actions = diff_configs(old, new)

    assert [(a.kind, a.player_id, a.fields) for a in actions] == [(ActionKind.STOP_CLIENT, "dac-1", ["enabled"])]


def test_a_hot_field_on_a_pulse_sink_player_is_hot_applied_by_player_id():
    old = _with(_cfg(), _sink_player(static_delay_ms=0))
    new = _with(_cfg(), _sink_player(static_delay_ms=120))

    actions = diff_configs(old, new)

    assert len(actions) == 1
    (hot,) = actions
    assert (hot.kind, hot.player_id, hot.mac) == (ActionKind.HOT_APPLY, "dac-1", None)
    assert hot.fields == ["static_delay_ms"]
    assert hot.payload == {"static_delay_ms": 120}


def test_renaming_or_moving_a_pulse_sink_player_warm_restarts_it_with_the_player_dict():
    old = _with(_cfg(), _sink_player())
    renamed = {**_sink_player(), "name": "Desk DAC"}
    moved = _sink_player(sink="alsa_output.other")

    rename_actions = diff_configs(old, _with(_cfg(), renamed))
    move_actions = diff_configs(old, _with(_cfg(), moved))

    assert [(a.kind, a.fields) for a in rename_actions] == [(ActionKind.WARM_RESTART, ["player_name"])]
    assert rename_actions[0].payload["device"] == renamed
    assert [(a.kind, a.fields) for a in move_actions] == [(ActionKind.WARM_RESTART, ["sink"])]
    assert move_actions[0].payload["device"] == moved


def test_bluetooth_actions_carry_the_player_id_and_keep_the_mac():
    old = _cfg({"mac": A, "adapter": "hci0"})
    new = _cfg({"mac": A, "adapter": "hci1"})

    actions = diff_configs(old, new)

    assert [a.kind for a in actions] == [ActionKind.BT_REMOVE, ActionKind.WARM_RESTART]
    pid = new["players"][0]["id"]
    assert all((a.mac, a.player_id) == (A, pid) for a in actions)
    assert actions[0].payload == {"old_adapter": "hci0"}
    # A Bluetooth payload stays the v5-shaped entry activation reads.
    assert actions[1].payload["device"]["mac"] == A
    assert actions[1].payload["device"]["adapter"] == "hci1"


def test_a_pulse_sink_player_in_front_does_not_shift_bluetooth_port_indexes():
    """Bluetooth players keep their position among Bluetooth players."""
    old = {**_cfg({"mac": A}), "players": [_sink_player(), *_cfg({"mac": A})["players"]]}
    new = {**_cfg({"mac": A}, {"mac": B}), "players": [_sink_player(), *_cfg({"mac": A}, {"mac": B})["players"]]}

    (start,) = diff_configs(old, new)

    assert start.mac == B
    assert start.payload["device_index"] == 1


def test_a_pulse_sink_player_port_index_follows_all_bluetooth_players():
    """Non-Bluetooth players: number of Bluetooth players + position among the others."""
    bt = _cfg({"mac": A}, {"mac": B})
    old = {**bt, "players": [_sink_player("dac-1"), *bt["players"]]}
    new = {**bt, "players": [_sink_player("dac-1"), bt["players"][0], _sink_player("dac-2"), bt["players"][1]]}

    (start,) = diff_configs(old, new)

    assert start.player_id == "dac-2"
    assert start.payload["device_index"] == 2 + 1
