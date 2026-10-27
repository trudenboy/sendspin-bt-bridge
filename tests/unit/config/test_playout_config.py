import json
from pathlib import Path

import pytest

from sendspin_bridge.config import CONFIG_ALLOWED_KEYS, DEFAULT_CONFIG

SCHEMA = Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json"


def test_playout_sync_defaults_to_off():
    assert DEFAULT_CONFIG["PLAYOUT_SYNC"] == "off"
    assert "PLAYOUT_SYNC" in CONFIG_ALLOWED_KEYS


def test_schema_restricts_playout_sync():
    prop = json.loads(SCHEMA.read_text())["properties"]["PLAYOUT_SYNC"]
    assert prop["enum"] == ["off", "measure", "loop"] and prop["default"] == "off"


def test_config_model_rejects_unknown_mode():
    from pydantic import ValidationError

    from sendspin_bridge.application.models.config import BridgeConfig

    with pytest.raises(ValidationError):
        BridgeConfig.model_validate({"PLAYOUT_SYNC": "fast"})


def test_mode_change_restarts_daemons():
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs

    actions = diff_configs({"PLAYOUT_SYNC": "off"}, {"PLAYOUT_SYNC": "loop"})
    assert [a.kind for a in actions] == [ActionKind.GLOBAL_RESTART]


def test_post_config_accepts_the_key():
    from sendspin_bridge.application.config import _ALLOWED_POST_CONFIG_KEYS

    assert "PLAYOUT_SYNC" in _ALLOWED_POST_CONFIG_KEYS
