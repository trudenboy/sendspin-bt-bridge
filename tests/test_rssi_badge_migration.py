"""Tests for the EXPERIMENTAL_RSSI_BADGE → RSSI_BADGE rename in v2.64.0.

The flag was promoted out of "experimental" once it stabilised; the
default flipped from False to True.  Existing operator preferences
must round-trip through the migration unchanged so anyone who had
explicitly disabled the badge keeps it disabled, and anyone who had
explicitly enabled it stays enabled.
"""

from __future__ import annotations

from config import DEFAULT_CONFIG
from config_migration import _normalize_loaded_config


def test_legacy_key_with_true_migrates_to_new_key():
    """Operator who opted into the experimental flag keeps it enabled."""
    config = {"EXPERIMENTAL_RSSI_BADGE": True}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["RSSI_BADGE"] is True
    assert "EXPERIMENTAL_RSSI_BADGE" not in config


def test_legacy_key_with_false_migrates_to_new_key():
    """Operator who explicitly disabled the experimental flag keeps it
    disabled — important so the default flip from False→True doesn't
    silently turn the feature back on for someone who had reasons to
    keep it off (CPU-constrained host, mgmt-socket conflicts)."""
    config = {"EXPERIMENTAL_RSSI_BADGE": False}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["RSSI_BADGE"] is False
    assert "EXPERIMENTAL_RSSI_BADGE" not in config


def test_new_key_takes_precedence_when_both_present():
    """If both keys are present (e.g. a config edited mid-upgrade by
    hand), the new key wins and the old one is dropped."""
    config = {"EXPERIMENTAL_RSSI_BADGE": False, "RSSI_BADGE": True}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["RSSI_BADGE"] is True
    assert "EXPERIMENTAL_RSSI_BADGE" not in config


def test_fresh_config_without_legacy_key_inherits_default_true():
    """No legacy key, no new key → consumer reads ``True`` via the
    DEFAULT_CONFIG fallback.  ``_normalize_loaded_config`` deliberately
    leaves missing keys missing — callers like ``bridge_orchestrator``
    pull defaults at use time via ``config.get(key, default)``."""
    config: dict = {}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    # Either explicitly written (after migration of legacy) or inherited
    # at lookup time — both shapes resolve to True.
    resolved = config.get("RSSI_BADGE", DEFAULT_CONFIG["RSSI_BADGE"])
    assert resolved is True


def test_default_config_has_rssi_badge_true_not_legacy():
    """The default-config dict no longer carries the legacy key."""
    assert DEFAULT_CONFIG.get("RSSI_BADGE") is True
    assert "EXPERIMENTAL_RSSI_BADGE" not in DEFAULT_CONFIG
