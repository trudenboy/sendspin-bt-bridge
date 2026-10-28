import json
from pathlib import Path

import pytest

from sendspin_bridge.config import CONFIG_ALLOWED_KEYS, DEFAULT_CONFIG, air_sensor_effective

SCHEMA = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json"
BT = {"backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"}}


def test_defaults_off_and_allowed():
    assert DEFAULT_CONFIG["EXPERIMENTAL_AIR_SENSOR"] is False
    assert "EXPERIMENTAL_AIR_SENSOR" in CONFIG_ALLOWED_KEYS


def test_schema_declares_a_boolean():
    prop = json.loads(SCHEMA.read_text())["properties"]["EXPERIMENTAL_AIR_SENSOR"]
    assert prop["type"] == "boolean" and prop["default"] is False


@pytest.mark.parametrize(
    ("cfg", "player", "expected"),
    [
        ({"EXPERIMENTAL_AIR_SENSOR": False, "PLAYOUT_SYNC": "measure"}, BT, (False, "switch_off")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "off"}, BT, (False, "needs_measure")),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "loop"}, BT, (False, "loop_not_supported_yet")),
        (
            {"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"},
            {**BT, "playout_sync_enabled": False},
            (False, "needs_measure"),
        ),
        (
            {"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"},
            {"backend": {"type": "pulse_sink", "sink": "x"}},
            (False, "not_bluetooth"),
        ),
        ({"EXPERIMENTAL_AIR_SENSOR": True, "PLAYOUT_SYNC": "measure"}, BT, (True, None)),
    ],
)
def test_effective_rule(cfg, player, expected):
    assert air_sensor_effective(player, cfg) == expected


def test_change_needs_a_bridge_restart():
    # R-17: the switch decides at start which monitor path owns the HCI socket
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs

    actions = diff_configs({"EXPERIMENTAL_AIR_SENSOR": False}, {"EXPERIMENTAL_AIR_SENSOR": True})
    assert [a.kind for a in actions] == [ActionKind.RESTART_REQUIRED]


def test_post_config_accepts_and_validates():
    from pydantic import ValidationError

    from sendspin_bridge.application.config import _ALLOWED_POST_CONFIG_KEYS
    from sendspin_bridge.application.models.config import BridgeConfig

    assert "EXPERIMENTAL_AIR_SENSOR" in _ALLOWED_POST_CONFIG_KEYS
    with pytest.raises(ValidationError):
        BridgeConfig.model_validate({"EXPERIMENTAL_AIR_SENSOR": "maybe"})
