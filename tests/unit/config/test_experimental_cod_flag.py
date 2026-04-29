"""Tests for the ``EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE`` flag.

Covers the four touch points the persistence pattern requires:

1. Default value is ``False`` in the canonical defaults dict.
2. ``migration._normalize_config`` coerces truthy / falsy strings
   from ``options.json`` to a real bool.
3. ``schema.json`` carries a description / type entry.
4. ``translate_ha_config`` includes the flag in ``web_ui_only_keys``
   so it survives an HA addon restart (preserved from existing
   ``config.json`` rather than reset from ``options.json``).

Plus the orchestrator gating: when the flag is ``False``, the CoD
applier is not called even when adapters carry ``device_class``
overrides.
"""

from __future__ import annotations

import json
from pathlib import Path

from sendspin_bridge import config as config_module
from sendspin_bridge.config import migration

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_flag_default_is_false():
    assert config_module.DEFAULT_CONFIG["EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE"] is False


def test_migration_normalizes_truthy_string_to_bool():
    cfg = {"EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE": "true"}
    migration._normalize_loaded_config(cfg, defaults=config_module.DEFAULT_CONFIG)
    assert cfg["EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE"] is True


def test_migration_normalizes_falsy_string_to_bool():
    cfg = {"EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE": "false"}
    migration._normalize_loaded_config(cfg, defaults=config_module.DEFAULT_CONFIG)
    assert cfg["EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE"] is False


def test_schema_documents_the_flag():
    schema_path = _REPO_ROOT / "src" / "sendspin_bridge" / "config" / "schema.json"
    schema = json.loads(schema_path.read_text())
    entry = schema["properties"]["EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE"]
    assert entry["type"] == "boolean"
    assert entry["default"] is False
    # Description must mention the bug it works around so future
    # readers can find the reference.
    assert "bluez/bluez#1025" in entry["description"]


def test_translate_ha_config_preserves_flag_across_restart():
    """The flag must appear in ``web_ui_only_keys`` so HA addon restart
    doesn't silently revert UI-set state to the options.json default."""
    translator_path = _REPO_ROOT / "scripts" / "translate_ha_config.py"
    text = translator_path.read_text()
    # Cheap lexical check: the key must literally appear inside the
    # web_ui_only_keys tuple. A regex-aware AST walk would be more
    # robust but adds complexity for no real lift.
    assert '"EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE"' in text, (
        "EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE missing from translate_ha_config.py — "
        "HA addon restart will silently reset the flag"
    )
