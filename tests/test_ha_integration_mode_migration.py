"""Migration: legacy ``HA_INTEGRATION.mode == "both"`` → ``"mqtt"``.

``both`` shipped briefly in v2.65.0-rc.1 / rc.2 but was removed in
rc.3 because running both transports at once produced duplicate HA
entities (same unique_id, two different platforms).  The carry-forward
choice is "mqtt" — broker creds tend to be the high-cost setup; an
operator who actually wanted the REST/custom_component path can flip
the mode back to "rest" with one click after upgrade.
"""

from __future__ import annotations

from sendspin_bridge.config.migration import _normalize_ha_integration


def test_both_normalised_to_mqtt():
    config = {
        "HA_INTEGRATION": {
            "enabled": True,
            "mode": "both",
            "mqtt": {"broker": "broker.local"},
        }
    }
    _normalize_ha_integration(config, defaults={})
    assert config["HA_INTEGRATION"]["mode"] == "mqtt"
    # Surrounding fields untouched.
    assert config["HA_INTEGRATION"]["enabled"] is True
    assert config["HA_INTEGRATION"]["mqtt"]["broker"] == "broker.local"


def test_both_case_insensitive():
    config = {"HA_INTEGRATION": {"mode": "BOTH"}}
    _normalize_ha_integration(config, defaults={})
    assert config["HA_INTEGRATION"]["mode"] == "mqtt"


def test_both_with_whitespace():
    config = {"HA_INTEGRATION": {"mode": "  both  "}}
    _normalize_ha_integration(config, defaults={})
    assert config["HA_INTEGRATION"]["mode"] == "mqtt"


def test_other_modes_left_alone():
    for original in ("off", "mqtt", "rest"):
        config = {"HA_INTEGRATION": {"mode": original}}
        _normalize_ha_integration(config, defaults={})
        assert config["HA_INTEGRATION"]["mode"] == original


def test_missing_block_no_op():
    config = {}
    _normalize_ha_integration(config, defaults={})
    assert config == {}


def test_non_dict_block_left_alone():
    config = {"HA_INTEGRATION": "garbage"}
    _normalize_ha_integration(config, defaults={})
    assert config["HA_INTEGRATION"] == "garbage"


def test_non_string_mode_left_alone():
    config = {"HA_INTEGRATION": {"mode": None}}
    _normalize_ha_integration(config, defaults={})
    assert config["HA_INTEGRATION"]["mode"] is None
