"""COMPENSATE_OUTPUT_LATENCY and PLAYOUT_DIAG are ordinary config keys."""

from __future__ import annotations

import pytest

from sendspin_bridge.application.models.config import BridgeConfig
from sendspin_bridge.config import DEFAULT_CONFIG


def test_defaults():
    assert DEFAULT_CONFIG["COMPENSATE_OUTPUT_LATENCY"] is True
    assert DEFAULT_CONFIG["PLAYOUT_DIAG"] is False


def test_the_config_model_carries_both_with_their_defaults():
    fields = BridgeConfig.model_fields
    assert fields["COMPENSATE_OUTPUT_LATENCY"].default is True
    assert fields["PLAYOUT_DIAG"].default is False


@pytest.mark.parametrize("key", ["COMPENSATE_OUTPUT_LATENCY", "PLAYOUT_DIAG"])
def test_the_web_ui_may_save_them(key):
    from sendspin_bridge.application.config import _ALLOWED_POST_CONFIG_KEYS

    assert key in _ALLOWED_POST_CONFIG_KEYS


@pytest.mark.parametrize("key", ["COMPENSATE_OUTPUT_LATENCY", "PLAYOUT_DIAG"])
def test_schema_json_describes_them(key):
    import json
    from pathlib import Path

    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "src" / "sendspin_bridge" / "config" / "schema.json").read_text()
    )
    assert schema["properties"][key]["type"] == "boolean"
    assert schema["properties"][key]["default"] is DEFAULT_CONFIG[key]
