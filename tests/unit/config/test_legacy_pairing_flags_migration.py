from __future__ import annotations

from sendspin_bridge.config import CONFIG_ALLOWED_KEYS, DEFAULT_CONFIG
from sendspin_bridge.config.migration import migrate_config_payload


def test_legacy_global_pairing_flags_are_removed() -> None:
    payload = {
        "EXPERIMENTAL_PAIR_JUST_WORKS": True,
        "ALLOW_HFP_PROFILE": True,
    }

    migrated = migrate_config_payload(payload, allowed_keys=CONFIG_ALLOWED_KEYS)

    assert "EXPERIMENTAL_PAIR_JUST_WORKS" not in migrated.normalized_config
    assert "ALLOW_HFP_PROFILE" not in migrated.normalized_config


def test_legacy_global_pairing_flags_are_not_runtime_defaults() -> None:
    assert "EXPERIMENTAL_PAIR_JUST_WORKS" not in DEFAULT_CONFIG
    assert "ALLOW_HFP_PROFILE" not in DEFAULT_CONFIG
