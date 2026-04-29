"""``HA_INTEGRATION`` namespace classification in ``services/config_diff.py``."""

from __future__ import annotations

from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs


def _base_cfg(ha_block):
    return {
        "BLUETOOTH_DEVICES": [],
        "HA_INTEGRATION": ha_block,
    }


def test_no_ha_change_emits_no_action():
    block = {
        "enabled": False,
        "mode": "off",
        "mqtt": {"broker": "auto", "port": 1883},
        "rest": {"advertise_mdns": True},
    }
    actions = diff_configs(_base_cfg(block), _base_cfg(block))
    assert not any(a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE for a in actions)


def test_enabling_integration_emits_lifecycle_action():
    old = _base_cfg({"enabled": False, "mode": "off"})
    new = _base_cfg({"enabled": True, "mode": "mqtt"})
    actions = diff_configs(old, new)
    matches = [a for a in actions if a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE]
    assert len(matches) == 1
    assert "enabled" in matches[0].fields
    assert "mode" in matches[0].fields
    assert matches[0].payload["target"]["mode"] == "mqtt"


def test_broker_change_emits_lifecycle_action():
    old = _base_cfg({"enabled": True, "mode": "mqtt", "mqtt": {"broker": "auto", "port": 1883}})
    new = _base_cfg({"enabled": True, "mode": "mqtt", "mqtt": {"broker": "192.168.1.10", "port": 1883}})
    actions = diff_configs(old, new)
    matches = [a for a in actions if a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE]
    assert len(matches) == 1
    assert "mqtt.broker" in matches[0].fields


def test_password_change_emits_lifecycle_action():
    old = _base_cfg({"enabled": True, "mode": "mqtt", "mqtt": {"broker": "h", "password": "old"}})
    new = _base_cfg({"enabled": True, "mode": "mqtt", "mqtt": {"broker": "h", "password": "new"}})
    actions = diff_configs(old, new)
    matches = [a for a in actions if a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE]
    assert matches and "mqtt.password" in matches[0].fields


def test_mdns_toggle_emits_lifecycle_action():
    old = _base_cfg({"enabled": True, "mode": "rest", "rest": {"advertise_mdns": True}})
    new = _base_cfg({"enabled": True, "mode": "rest", "rest": {"advertise_mdns": False}})
    actions = diff_configs(old, new)
    matches = [a for a in actions if a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE]
    assert matches and "rest.advertise_mdns" in matches[0].fields


def test_unknown_nested_key_does_not_emit_action():
    """Drift protection: an unrecognised ``mqtt.unknown_key`` must not
    trigger a lifecycle restart — it isn't in the whitelist."""
    old = _base_cfg({"enabled": True, "mode": "mqtt", "mqtt": {"broker": "h", "junk": "a"}})
    new = _base_cfg({"enabled": True, "mode": "mqtt", "mqtt": {"broker": "h", "junk": "b"}})
    actions = diff_configs(old, new)
    assert not any(a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE for a in actions)


def test_auth_tokens_change_does_not_emit_action():
    """``AUTH_TOKENS`` mutates only via /api/auth/tokens — config-write
    round-trips must produce no reconfig action."""
    old = {"BLUETOOTH_DEVICES": [], "AUTH_TOKENS": []}
    new = {"BLUETOOTH_DEVICES": [], "AUTH_TOKENS": [{"id": "x", "token_hash": "abc"}]}
    actions = diff_configs(old, new)
    assert not actions


def test_lifecycle_action_payload_carries_full_target_block():
    """Executor wants the WHOLE new HA_INTEGRATION block, not just diffs,
    so it can restart with the new full config in one shot."""
    old = _base_cfg({"enabled": False, "mode": "off"})
    new = _base_cfg(
        {
            "enabled": True,
            "mode": "mqtt",
            "mqtt": {"broker": "10.0.0.1", "port": 8883, "tls": True},
            "rest": {"advertise_mdns": True},
        }
    )
    actions = diff_configs(old, new)
    matches = [a for a in actions if a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE]
    assert matches
    target = matches[0].payload["target"]
    assert target["mqtt"]["tls"] is True
    assert target["mqtt"]["port"] == 8883
    assert target["rest"]["advertise_mdns"] is True


def test_missing_old_block_treats_as_first_enable():
    """Loading a config that lacked HA_INTEGRATION before should produce
    no action when defaults are off-equivalent."""
    old = {"BLUETOOTH_DEVICES": []}
    new = _base_cfg({"enabled": False, "mode": "off"})
    actions = diff_configs(old, new)
    assert not any(a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE for a in actions)


def test_first_enable_from_missing_emits_action():
    old = {"BLUETOOTH_DEVICES": []}
    new = _base_cfg({"enabled": True, "mode": "mqtt"})
    actions = diff_configs(old, new)
    matches = [a for a in actions if a.kind is ActionKind.HA_INTEGRATION_LIFECYCLE]
    assert matches and ("enabled" in matches[0].fields or "mode" in matches[0].fields)
