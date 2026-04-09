from __future__ import annotations

from services.guidance_issue_registry import ISSUE_REGISTRY


def test_sink_system_muted_issue_definition_exists():
    assert "sink_system_muted" in ISSUE_REGISTRY
    defn = ISSUE_REGISTRY["sink_system_muted"]
    assert defn.layer == "sink_verification"
    assert defn.severity == "warning"
    assert defn.priority == 42
    assert "sink_muted_at_system_level" in defn.default_reason_codes
