"""Tests for the pre-submit bug-report classifier (issue #262)."""

from __future__ import annotations

from sendspin_bridge.services.diagnostics.bugreport_classifier import (
    classify_likely_causes,
)


def _recovery_with_issue(key: str) -> dict:
    return {"issues": [{"key": key, "title": "x", "summary": "y"}]}


def test_classify_never_paired_match():
    causes = classify_likely_causes(
        recovery_snapshot=_recovery_with_issue("never_paired"),
        diagnostics={},
    )
    assert len(causes) == 1
    assert causes[0]["code"] == "never_paired_device"
    assert causes[0]["confidence"] == "high"
    assert "Start pairing" in causes[0]["hint"]
    # Copilot review on PR #290 — must return a structured action_key, not a
    # hash-fragment action_url that the frontend can't route to.
    assert causes[0]["action_key"] == "open_bluetooth_devices"
    assert "action_url" not in causes[0]


def test_classify_uses_action_key_not_action_url():
    """Every cause must use the new action_key contract — the deprecated
    action_url field was a no-op in the frontend (no hash routing)."""
    recovery = {
        "issues": [
            {"key": "never_paired"},
            {"key": "missing_sink"},
        ]
    }
    diagnostics = {
        "ma_integration": {"configured": True, "connected": False},
        "adapters": [],
    }
    causes = classify_likely_causes(recovery_snapshot=recovery, diagnostics=diagnostics)
    assert causes, "expected matches across all four rules"
    for cause in causes:
        assert "action_key" in cause, f"cause missing action_key: {cause}"
        assert "action_url" not in cause, f"cause still uses deprecated action_url: {cause}"
        assert cause["action_key"] in {
            "open_bluetooth_devices",
            "open_bluetooth_adapters",
            "open_ma_settings",
        }, f"unknown action_key {cause['action_key']!r}; frontend dispatcher won't route it"


def test_classify_auto_disabled_never_paired_also_matches_never_paired_rule():
    """A device auto-disabled because it never paired should still surface
    the same likely_cause as the never_paired card."""
    causes = classify_likely_causes(
        recovery_snapshot=_recovery_with_issue("auto_disabled_never_paired"),
        diagnostics={},
    )
    assert any(c["code"] == "never_paired_device" for c in causes)


def test_classify_audio_sink_missing_match():
    causes = classify_likely_causes(
        recovery_snapshot=_recovery_with_issue("missing_sink"),
        diagnostics={},
    )
    assert any(c["code"] == "audio_sink_missing" for c in causes)


def test_classify_ma_not_connected_match():
    causes = classify_likely_causes(
        recovery_snapshot={},
        diagnostics={"ma": {"configured": True, "connected": False}},
    )
    assert any(c["code"] == "ma_not_connected" for c in causes)


def test_classify_ma_not_connected_silent_when_not_configured():
    """No MA URL set → MA not connected is expected, not a likely cause."""
    causes = classify_likely_causes(
        recovery_snapshot={},
        diagnostics={"ma": {"configured": False, "connected": False}},
    )
    assert not any(c["code"] == "ma_not_connected" for c in causes)


def test_classify_no_bluetooth_adapter_match():
    causes = classify_likely_causes(
        recovery_snapshot={},
        diagnostics={"bluetooth_adapters": []},
    )
    assert any(c["code"] == "no_bluetooth_adapter" for c in causes)
    # Must be medium-confidence — transient D-Bus hiccup can show the same shape
    no_adapter = next(c for c in causes if c["code"] == "no_bluetooth_adapter")
    assert no_adapter["confidence"] == "medium"


def test_classify_no_bluetooth_adapter_silent_when_adapters_present():
    causes = classify_likely_causes(
        recovery_snapshot={},
        diagnostics={"bluetooth_adapters": [{"mac": "AA:BB:CC:DD:EE:FF"}]},
    )
    assert not any(c["code"] == "no_bluetooth_adapter" for c in causes)


def test_classify_returns_empty_when_no_rules_match():
    """No recovery issues + MA happy + adapters present → no causes surfaced.
    UI then renders the un-gated bug-report form."""
    causes = classify_likely_causes(
        recovery_snapshot={"issues": []},
        diagnostics={
            "ma": {"configured": True, "connected": True},
            "bluetooth_adapters": [{"mac": "AA:BB:CC:DD:EE:FF"}],
        },
    )
    assert causes == []


def test_classify_dedupes_by_code():
    """Multiple recovery issues that map to the same code must not produce
    duplicate likely_causes."""
    snapshot = {
        "issues": [
            {"key": "never_paired"},
            {"key": "auto_disabled_never_paired"},
        ]
    }
    causes = classify_likely_causes(recovery_snapshot=snapshot, diagnostics={})
    never_paired_causes = [c for c in causes if c["code"] == "never_paired_device"]
    assert len(never_paired_causes) == 1


def test_classify_handles_missing_inputs_gracefully():
    """Both inputs None must not raise; just return empty list."""
    assert classify_likely_causes(recovery_snapshot=None, diagnostics=None) == []


def test_classify_handles_malformed_recovery_payload():
    """Recovery snapshot without `issues` key, or with wrong type, must not crash."""
    assert classify_likely_causes(recovery_snapshot={}, diagnostics={}) == []
    assert classify_likely_causes(recovery_snapshot={"issues": "not a list"}, diagnostics={}) == []
