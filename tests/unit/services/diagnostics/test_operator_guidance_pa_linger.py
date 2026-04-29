"""Operator guidance: targeted remediation for headless PipeWire (issue #151).

When the audio preflight reports ``reason_code=pa_socket_refused`` *and* the
bridge is not running as a HA add-on, the guidance panel should surface a
linger-specific issue group instead of the generic "Audio backend unavailable"
card. HA add-ons must keep the generic path — Supervisor owns audio there.
"""

from __future__ import annotations


def _audio_unreachable_onboarding() -> dict:
    return {
        "checks": [
            {
                "key": "audio",
                "status": "error",
                "summary": "Audio socket is mounted but the server refused the connection.",
                "details": {
                    "system": "unreachable",
                    "socket": "unix:/run/user/1000/pulse/native",
                    "socket_exists": True,
                    "socket_reachable": False,
                    "last_error": "Connection refused",
                    "reason_code": "pa_socket_refused",
                },
                "actions": ["existing action"],
            }
        ],
        "checklist": {
            "overall_status": "error",
            "progress_percent": 28,
            "headline": "Next recommended step: Verify audio backend",
            "summary": "The bridge cannot reach its audio backend right now.",
            "current_step_key": "audio",
            "current_step_title": "Verify audio backend",
            "primary_action": {"key": "open_diagnostics", "label": "Open diagnostics"},
            "checkpoints": [],
            "steps": [
                {"key": "runtime_access", "title": "Verify runtime host access", "status": "ok", "stage": "complete"},
                {"key": "bluetooth", "title": "Check Bluetooth access", "status": "ok", "stage": "complete"},
                {
                    "key": "audio",
                    "title": "Verify audio backend",
                    "status": "error",
                    "stage": "current",
                    "summary": "Audio socket refused connection.",
                    "details": {"reason_code": "pa_socket_refused"},
                    "actions": ["existing action"],
                },
                {
                    "key": "bridge_control",
                    "title": "Make a speaker available",
                    "status": "warning",
                    "stage": "upcoming",
                },
                {
                    "key": "sink_verification",
                    "title": "Attach your first speaker",
                    "status": "warning",
                    "stage": "upcoming",
                },
            ],
        },
        "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
    }


def test_pa_socket_refused_emits_linger_issue_in_standalone(monkeypatch):
    import sendspin_bridge.services.diagnostics.operator_guidance as module
    from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot

    monkeypatch.setattr(module, "is_ha_addon_runtime", lambda: False)

    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant=_audio_unreachable_onboarding(),
        recovery_assistant={"summary": {"summary": "Audio backend unreachable."}},
        startup_progress={"status": "complete"},
        devices=[],
    )

    data = snapshot.to_dict()
    audio_group = next((g for g in data["issue_groups"] if g["key"] == "pa_socket_refused"), None)
    assert audio_group is not None, f"issue_groups lacked pa_socket_refused: {data['issue_groups']}"
    assert audio_group["severity"] == "error"
    assert "linger" in audio_group["title"].lower()
    summary_text = audio_group["summary"].lower()
    assert "socket" in summary_text
    assert "refused" in summary_text
    # Primary action is still "open_diagnostics" by contract (no new action key).
    assert audio_group["primary_action"]["key"] == "open_diagnostics"


def test_pa_socket_refused_suppressed_in_ha_addon(monkeypatch):
    import sendspin_bridge.services.diagnostics.operator_guidance as module
    from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot

    monkeypatch.setattr(module, "is_ha_addon_runtime", lambda: True)

    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant=_audio_unreachable_onboarding(),
        recovery_assistant={"summary": {"summary": "Audio backend unreachable."}},
        startup_progress={"status": "complete"},
        devices=[],
    )

    data = snapshot.to_dict()
    keys = [g["key"] for g in data["issue_groups"]]
    assert "pa_socket_refused" not in keys, f"linger-specific issue must be suppressed in HA addon: {keys}"


def test_audio_unavailable_without_reason_code_uses_generic_issue(monkeypatch):
    """Regression: pre-existing audio_unavailable behaviour unchanged."""
    import sendspin_bridge.services.diagnostics.operator_guidance as module
    from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot

    monkeypatch.setattr(module, "is_ha_addon_runtime", lambda: False)

    onboarding = _audio_unreachable_onboarding()
    # Scrub the pa_socket_refused reason_code to force the generic path.
    onboarding["checks"][0]["details"].pop("reason_code", None)
    onboarding["checklist"]["steps"][2]["details"].pop("reason_code", None)

    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant=onboarding,
        recovery_assistant={"summary": {"summary": "Audio backend unreachable."}},
        startup_progress={"status": "complete"},
        devices=[],
    )

    data = snapshot.to_dict()
    keys = [g["key"] for g in data["issue_groups"]]
    assert "pa_socket_refused" not in keys
    assert "audio_unavailable" in keys
