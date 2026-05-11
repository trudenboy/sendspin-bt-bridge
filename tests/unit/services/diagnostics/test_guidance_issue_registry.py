from __future__ import annotations

from sendspin_bridge.services.diagnostics.guidance_issue_registry import ISSUE_REGISTRY


def test_sink_system_muted_issue_definition_exists():
    assert "sink_system_muted" in ISSUE_REGISTRY
    defn = ISSUE_REGISTRY["sink_system_muted"]
    assert defn.layer == "sink_verification"
    assert defn.severity == "warning"
    assert defn.priority == 42
    assert "sink_muted_at_system_level" in defn.default_reason_codes


def test_never_paired_priority_above_repair_required():
    """The 'never paired' issue (#260) must outrank 'repair_required' so a
    device that's missing from BlueZ surfaces the Start pairing remediation
    instead of a generic re-pair card. Priority sorts ascending (lower=higher
    rank), so we expect never_paired < repair_required."""
    assert "never_paired" in ISSUE_REGISTRY
    defn = ISSUE_REGISTRY["never_paired"]
    repair = ISSUE_REGISTRY["repair_required"]
    assert defn.priority < repair.priority, (
        f"never_paired priority {defn.priority} must be lower than "
        f"repair_required priority {repair.priority} to outrank it"
    )
    assert defn.severity == "error"
    assert defn.layer == "sink_verification"
    assert "never_paired" in defn.default_reason_codes


def test_auto_disabled_never_paired_priority_below_auto_released():
    """The 'auto_disabled_never_paired' card (#263) sits in the bridge_control
    layer alongside auto_released but with a slightly higher priority because
    the operator action ('Re-enable') is different from auto_released's
    'Reconnect'."""
    assert "auto_disabled_never_paired" in ISSUE_REGISTRY
    defn = ISSUE_REGISTRY["auto_disabled_never_paired"]
    assert defn.layer == "bridge_control"
    assert defn.severity == "warning"
    assert "device_auto_disabled" in defn.default_reason_codes
    assert "never_paired" in defn.default_reason_codes
