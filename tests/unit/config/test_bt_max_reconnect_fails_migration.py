"""Tests for BT_MAX_RECONNECT_FAILS default change in v2.70.0-rc.2 (#263).

The default flipped from 0 (unlimited reconnects) to 5 to power the
auto-disable feature for never-paired devices. The migration is **truly
one-shot**: it lives in ``migrate_config_payload`` and fires only when
the config schema is upgraded from <3. After the result is persisted
(``needs_persist=True``), the bumped ``CONFIG_SCHEMA_VERSION`` value
ensures the migration block is skipped on subsequent loads, so an
operator who explicitly sets ``BT_MAX_RECONNECT_FAILS=0`` after the
upgrade keeps that choice.
"""

from __future__ import annotations

from sendspin_bridge.config import CONFIG_ALLOWED_KEYS, DEFAULT_CONFIG
from sendspin_bridge.config.migration import CONFIG_SCHEMA_VERSION, migrate_config_payload


def test_default_config_has_five():
    """Fresh installs must default to 5 to match the auto-disable contract."""
    assert DEFAULT_CONFIG["BT_MAX_RECONNECT_FAILS"] == 5


def test_schema_bumped_to_at_least_three():
    """The migration is gated on schema version, so the project schema
    must have bumped past 2."""
    assert CONFIG_SCHEMA_VERSION >= 3


def test_legacy_schema_with_zero_migrates_to_five_and_flags_persist():
    """A schema <3 config that still carries the legacy unlimited default
    must be migrated to 5 AND flagged for disk-persistence so the schema
    bump records the change (one-shot semantic)."""
    result = migrate_config_payload(
        {"CONFIG_SCHEMA_VERSION": 2, "BT_MAX_RECONNECT_FAILS": 0},
        allowed_keys=CONFIG_ALLOWED_KEYS,
    )
    assert result.normalized_config["BT_MAX_RECONNECT_FAILS"] == 5
    assert result.normalized_config["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION
    assert result.needs_persist is True


def test_current_schema_with_zero_is_preserved():
    """After the schema is bumped (and the result persisted), an operator
    who explicitly sets BT_MAX_RECONNECT_FAILS=0 to opt out of auto-disable
    must keep that choice — the migration is one-shot."""
    result = migrate_config_payload(
        {"CONFIG_SCHEMA_VERSION": CONFIG_SCHEMA_VERSION, "BT_MAX_RECONNECT_FAILS": 0},
        allowed_keys=CONFIG_ALLOWED_KEYS,
    )
    assert result.normalized_config["BT_MAX_RECONNECT_FAILS"] == 0


def test_legacy_schema_with_nonzero_preserved():
    """An operator who tuned this knob to a non-default value must keep
    their setting through the schema upgrade."""
    result = migrate_config_payload(
        {"CONFIG_SCHEMA_VERSION": 2, "BT_MAX_RECONNECT_FAILS": 12},
        allowed_keys=CONFIG_ALLOWED_KEYS,
    )
    assert result.normalized_config["BT_MAX_RECONNECT_FAILS"] == 12


def test_missing_schema_treated_as_legacy_upgrade():
    """A config without CONFIG_SCHEMA_VERSION is treated as legacy schema
    1 — so the BT_MAX_RECONNECT_FAILS=0 → 5 migration still applies."""
    result = migrate_config_payload(
        {"BT_MAX_RECONNECT_FAILS": 0},
        allowed_keys=CONFIG_ALLOWED_KEYS,
    )
    assert result.normalized_config["BT_MAX_RECONNECT_FAILS"] == 5
    assert result.needs_persist is True
