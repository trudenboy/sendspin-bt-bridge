"""Tests for BT_MAX_RECONNECT_FAILS default change in v2.70.0-rc.2 (#263).

The default flipped from 0 (unlimited reconnects) to 5 to power the
auto-disable feature for never-paired devices. Existing configs that
have the old default (0) are migrated to 5 automatically. Operators
who explicitly chose a non-default value (any int >= 1) keep their
setting; operators who explicitly want unlimited can re-set 0 after
upgrade — the migration is one-shot and runs only when the value
matches the old default.
"""

from __future__ import annotations

from sendspin_bridge.config import DEFAULT_CONFIG
from sendspin_bridge.config.migration import _normalize_loaded_config


def test_default_config_has_five():
    """Fresh installs must default to 5 to match the auto-disable contract."""
    assert DEFAULT_CONFIG["BT_MAX_RECONNECT_FAILS"] == 5


def test_existing_config_with_legacy_zero_migrates_to_five():
    """A config that still has the legacy unlimited default must be migrated
    to the new default so the auto-disable feature engages without an
    explicit user action."""
    config = {"BT_MAX_RECONNECT_FAILS": 0}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["BT_MAX_RECONNECT_FAILS"] == 5


def test_existing_config_with_nonzero_preserved():
    """An operator who has tuned this knob to a non-default value must keep
    their setting through the migration."""
    config = {"BT_MAX_RECONNECT_FAILS": 12}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["BT_MAX_RECONNECT_FAILS"] == 12


def test_missing_key_inherits_new_default():
    """A config without the key at all resolves to 5 through DEFAULT_CONFIG."""
    config: dict = {}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    resolved = config.get("BT_MAX_RECONNECT_FAILS", DEFAULT_CONFIG["BT_MAX_RECONNECT_FAILS"])
    assert resolved == 5
