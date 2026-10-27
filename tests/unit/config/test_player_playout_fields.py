"""``playout_sync_enabled``: the per-player opt-out from the playout sync engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sendspin_bridge.config import effective_playout_mode
from sendspin_bridge.config.players import Player
from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs
from tests.support.config_v6 import v6_config

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json").read_text()
)
BACKEND = {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:01"}


def test_false_opts_the_player_out_whatever_the_global_mode():
    assert effective_playout_mode({"playout_sync_enabled": False}, {"PLAYOUT_SYNC": "loop"}) == "off"
    assert effective_playout_mode({"playout_sync_enabled": False}, {"PLAYOUT_SYNC": "measure"}) == "off"


@pytest.mark.parametrize("player", [{"playout_sync_enabled": None}, {}, {"playout_sync_enabled": True}])
def test_none_missing_and_true_follow_the_global_mode(player):
    assert effective_playout_mode(player, {"PLAYOUT_SYNC": "loop"}) == "loop"


def test_a_global_off_stays_off_for_an_enabled_player():
    assert effective_playout_mode({"playout_sync_enabled": True}, {"PLAYOUT_SYNC": "off"}) == "off"


def test_a_missing_global_key_means_off():
    assert effective_playout_mode({}, {}) == "off"


@pytest.mark.parametrize("value", [True, False, None])
def test_the_player_model_accepts_a_boolean_or_null(value):
    assert Player(id="p", backend=BACKEND, playout_sync_enabled=value).playout_sync_enabled is value


def test_the_player_model_rejects_a_string():
    with pytest.raises(ValidationError):
        Player(id="p", backend=BACKEND, playout_sync_enabled="no")


def test_the_schema_describes_it_with_default_true():
    prop = SCHEMA["$defs"]["Player"]["properties"]["playout_sync_enabled"]
    types = {branch.get("type") for branch in prop["anyOf"]}
    assert types == {"boolean", "null"}
    assert prop["default"] is True


def test_a_change_warm_restarts_that_player_only():
    old = v6_config(
        [{"mac": "AA:BB:CC:DD:EE:01", "player_name": "A"}, {"mac": "AA:BB:CC:DD:EE:02", "player_name": "B"}]
    )
    new = json.loads(json.dumps(old))
    new["players"][0]["playout_sync_enabled"] = False

    actions = diff_configs(old, new)

    assert [(a.kind, a.player_id) for a in actions] == [(ActionKind.WARM_RESTART, old["players"][0]["id"])]
    assert actions[0].fields == ["playout_sync_enabled"]
