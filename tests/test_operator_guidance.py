from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from services.operator_guidance import build_operator_guidance_snapshot


def test_operator_guidance_reports_empty_state_onboarding():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [], "BLUETOOTH_DEVICES": []},
        onboarding_assistant={
            "checklist": {
                "headline": "Setup checklist",
                "summary": "Add adapters and speakers to begin.",
                "overall_status": "warning",
                "completed_steps": 0,
                "total_steps": 5,
                "progress_percent": 0,
                "checkpoints": [],
                "steps": [],
                "primary_action": {"key": "open_bluetooth_settings", "label": "Open Bluetooth settings"},
            },
            "counts": {"configured_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "No devices configured yet."}},
        startup_progress={"status": "idle"},
        devices=[],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "empty_state"
    assert data["header_status"]["label"] == "First run"
    assert data["onboarding_card"]["preference_key"] == "sendspin-ui:show-onboarding-guidance"
    assert "banner" not in data
    assert data["visibility_keys"]["recovery"] == "sendspin-ui:show-recovery-guidance"


def test_operator_guidance_keeps_onboarding_when_adapter_exists_but_no_devices():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": []},
        onboarding_assistant={
            "checklist": {
                "headline": "Setup checklist",
                "summary": "No bridge devices are configured yet.",
                "overall_status": "warning",
                "completed_steps": 1,
                "total_steps": 5,
                "progress_percent": 20,
                "checkpoints": [],
                "steps": [],
                "primary_action": {"key": "scan_devices", "label": "Scan for devices"},
            },
            "counts": {"configured_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "No bridge devices are configured yet."}},
        startup_progress={"status": "idle"},
        devices=[],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "empty_state"
    assert data["header_status"]["label"] == "Add first speaker"
    assert data["onboarding_card"]["primary_action"]["key"] == "scan_devices"
    assert data["onboarding_card"]["preference_key"] == "sendspin-ui:show-onboarding-guidance"


def test_operator_guidance_exposes_pending_onboarding_card_for_progress_state():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {
                "headline": "Setup checklist",
                "summary": "Finish the remaining setup steps.",
                "overall_status": "warning",
                "completed_steps": 3,
                "total_steps": 5,
                "progress_percent": 60,
                "checkpoints": [],
                "steps": [],
                "primary_action": {"key": "open_ma_settings", "label": "Open Music Assistant settings"},
            },
            "counts": {"configured_devices": 1, "connected_devices": 1, "sink_ready_devices": 1},
        },
        recovery_assistant={"summary": {"summary": "Setup is still in progress."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
            )
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "progress"
    assert data["header_status"]["label"] == "Setup 60%"
    assert data["onboarding_card"]["summary"] == "Finish the remaining setup steps."
    assert data["onboarding_card"]["primary_action"]["key"] == "open_ma_settings"


def test_operator_guidance_keeps_onboarding_card_available_in_healthy_mode():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {
                "headline": "Setup checklist",
                "summary": "Bridge setup is complete.",
                "overall_status": "ok",
                "completed_steps": 5,
                "total_steps": 5,
                "progress_percent": 100,
                "checkpoints": [],
                "steps": [],
                "primary_action": {"key": "open_diagnostics", "label": "Open diagnostics"},
            },
            "counts": {"configured_devices": 1, "connected_devices": 1, "sink_ready_devices": 1},
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
            )
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "healthy"
    assert data["header_status"]["label"] == "1/1 active devices ready"
    assert data["onboarding_card"]["summary"] == "Bridge setup is complete."
    assert data["onboarding_card"]["primary_action"]["key"] == "open_diagnostics"


def test_operator_guidance_groups_disconnected_devices_into_bulk_reconnect():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}]},
        onboarding_assistant={
            "checklist": {"overall_status": "ok", "progress_percent": 100},
            "counts": {"configured_devices": 2, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "Devices need reconnection."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
            ),
            SimpleNamespace(
                player_name="Office",
                bt_management_enabled=True,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
            ),
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "attention"
    assert data["banner"]["preference_key"] == "sendspin-ui:show-recovery-guidance"
    assert data["banner"]["primary_action"]["key"] == "reconnect_devices"
    assert data["issue_groups"][0]["key"] == "disconnected"
    assert data["issue_groups"][0]["count"] == 2
    assert data["issue_groups"][0]["primary_action"]["device_names"] == ["Kitchen", "Office"]


def test_operator_guidance_issue_groups_include_machine_readable_context():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {"overall_status": "ok", "progress_percent": 100},
            "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "Devices need reconnection."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
            )
        ],
    )

    group = snapshot.to_dict()["issue_groups"][0]
    assert group["context"]["layer"] == "sink_verification"
    assert group["context"]["priority"] >= 0
    assert "bluetooth_disconnected" in group["context"]["reason_codes"]
    assert group["context"]["device_names"] == ["Kitchen"]


def test_operator_guidance_promotes_ma_auth_attention():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {
                "overall_status": "warning",
                "current_step_key": "ma_auth",
                "summary": "Music Assistant token is still missing.",
                "progress_percent": 75,
                "primary_action": {"key": "open_ma_settings", "label": "Open Music Assistant settings"},
            },
            "counts": {"configured_devices": 1, "connected_devices": 1, "sink_ready_devices": 1},
        },
        recovery_assistant={
            "safe_actions": [{"key": "retry_ma_discovery", "label": "Retry discovery"}],
            "summary": {"summary": "Music Assistant needs attention."},
        },
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
            )
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "attention"
    assert data["header_status"]["tone"] == "warning"
    assert data["issue_groups"][0]["key"] == "ma_auth"
    assert data["banner"]["headline"] == "Music Assistant needs attention"
    assert data["banner"]["primary_action"]["key"] == "open_ma_settings"


def test_operator_guidance_surfaces_latency_issue_group_with_apply_action():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}]},
        onboarding_assistant={
            "checklist": {
                "overall_status": "warning",
                "current_step_key": "latency",
                "summary": "Latency tuning still needs attention.",
                "progress_percent": 86,
                "primary_action": {"key": "apply_latency_recommended", "label": "Apply 600 ms latency", "value": 600},
            },
            "counts": {"configured_devices": 2, "connected_devices": 2, "sink_ready_devices": 2},
        },
        recovery_assistant={
            "summary": {"summary": "Latency guidance recommends another pass."},
            "latency_assistant": {
                "summary": "Per-device delay tuning exists, but the global PulseAudio latency is still high.",
                "safe_actions": [
                    {"key": "apply_latency_recommended", "label": "Apply 600 ms latency", "value": 600},
                    {"key": "open_devices_settings", "label": "Review latency settings"},
                ],
            },
        },
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
            ),
            SimpleNamespace(
                player_name="Office",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
            ),
        ],
    )

    data = snapshot.to_dict()
    latency_group = next(group for group in data["issue_groups"] if group["key"] == "latency")
    assert latency_group["primary_action"]["key"] == "apply_latency_recommended"
    assert latency_group["primary_action"]["value"] == 600
    assert latency_group["secondary_actions"][0]["key"] == "open_devices_settings"


def test_operator_guidance_prefers_device_settings_for_multi_device_latency_without_static_delays():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}]},
        onboarding_assistant={
            "checklist": {
                "overall_status": "warning",
                "current_step_key": "latency",
                "summary": "Latency tuning still needs attention.",
                "progress_percent": 86,
                "primary_action": {"key": "open_devices_settings", "label": "Open device settings"},
            },
            "counts": {"configured_devices": 2, "connected_devices": 2, "sink_ready_devices": 2},
        },
        recovery_assistant={
            "summary": {"summary": "Latency guidance recommends another pass."},
            "latency_assistant": {
                "summary": "Multi-device setup detected without per-device static delays.",
                "safe_actions": [
                    {"key": "open_devices_settings", "label": "Tune device delays"},
                    {"key": "apply_latency_recommended", "label": "Apply 600 ms latency", "value": 600},
                ],
            },
        },
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
            ),
            SimpleNamespace(
                player_name="Office",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
            ),
        ],
    )

    data = snapshot.to_dict()
    latency_group = next(group for group in data["issue_groups"] if group["key"] == "latency")
    assert latency_group["summary"] == "Multi-device setup detected without per-device static delays."
    assert latency_group["primary_action"]["key"] == "open_devices_settings"
    assert latency_group["secondary_actions"][0]["key"] == "apply_latency_recommended"


def test_operator_guidance_prefers_repair_for_unpaired_reconnecting_device():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {"overall_status": "ok", "progress_percent": 100},
            "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "Repair is required."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                extra={"bluetooth_paired": False, "reconnect_attempt": 3, "max_reconnect_fails": 5},
            )
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "attention"
    assert data["issue_groups"][0]["key"] == "repair_required"
    assert data["issue_groups"][0]["primary_action"]["key"] == "pair_device"
    assert data["issue_groups"][0]["secondary_actions"][0]["key"] == "toggle_bt_management"
    assert data["issue_groups"][0]["secondary_actions"][1]["key"] == "open_diagnostics"
    assert "3/5" in data["issue_groups"][0]["summary"]
    assert "2 attempts remain" in data["issue_groups"][0]["summary"]


def test_operator_guidance_suppresses_problem_banner_during_startup_grace_period():
    completed_at = datetime.now(tz=timezone.utc).isoformat()
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {"overall_status": "ok", "progress_percent": 100},
            "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "Devices need reconnection."}},
        startup_progress={"status": "ready", "message": "Startup complete.", "completed_at": completed_at},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
            )
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "progress"
    assert "banner" not in data
    assert data["header_status"]["tone"] == "info"
    assert data["header_status"]["label"] == "Startup 90%"
    assert data["header_status"]["summary"] == "Finalizing Startup"
    assert data["issue_groups"][0]["key"] == "disconnected"


def test_operator_guidance_uses_shared_recovery_actions_for_auto_released_issue():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {"overall_status": "ok", "progress_percent": 100},
            "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "Reclaim Bluetooth to restore management."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=False,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                extra={"bt_released_by": "auto"},
            )
        ],
    )

    data = snapshot.to_dict()
    assert data["issue_groups"][0]["key"] == "auto_released"
    assert data["issue_groups"][0]["primary_action"]["key"] == "toggle_bt_management"
    assert data["issue_groups"][0]["secondary_actions"][0]["key"] == "open_diagnostics"
    assert data["onboarding_card"]["show_by_default"] is False


def test_operator_guidance_treats_user_released_devices_as_neutral():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {
                "overall_status": "warning",
                "progress_percent": 43,
                "headline": "Next recommended step: Make a speaker available",
                "summary": "All configured speakers are currently released from bridge management.",
                "current_step_key": "bridge_control",
                "current_step_title": "Make a speaker available",
                "primary_action": {"key": "open_devices_settings", "label": "Open device settings"},
                "checkpoints": [],
                "steps": [
                    {
                        "key": "runtime_access",
                        "title": "Verify runtime host access",
                        "status": "ok",
                        "stage": "complete",
                    },
                    {"key": "bluetooth", "title": "Check Bluetooth access", "status": "ok", "stage": "complete"},
                    {"key": "audio", "title": "Verify audio backend", "status": "ok", "stage": "complete"},
                    {
                        "key": "bridge_control",
                        "title": "Make a speaker available",
                        "status": "warning",
                        "stage": "current",
                        "summary": "All configured speakers are currently released from bridge management.",
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
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=False,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                extra={"bt_released_by": "user"},
            )
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "healthy"
    assert data["issue_groups"] == []
    assert data["header_status"]["tone"] == "neutral"
    assert data["header_status"]["label"] == "Bluetooth released"
    assert data["onboarding_card"]["show_by_default"] is True
    assert data["onboarding_card"]["headline"] == "Reclaim a speaker to resume playback"
    assert data["onboarding_card"]["summary"].startswith(
        "All configured Bluetooth devices are currently released from bridge management."
    )
    assert data["onboarding_card"]["primary_action"]["key"] == "toggle_bt_management"
    assert data["onboarding_card"]["primary_action"]["device_names"] == ["Kitchen"]
    assert data["onboarding_card"]["secondary_actions"][0]["key"] == "open_devices_settings"


def test_operator_guidance_uses_bulk_reclaim_onboarding_when_all_devices_are_released():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}]},
        onboarding_assistant={
            "checklist": {
                "overall_status": "warning",
                "progress_percent": 43,
                "completed_steps": 3,
                "total_steps": 7,
                "headline": "Next recommended step: Make a speaker available",
                "summary": "All configured speakers are currently released from bridge management.",
                "current_step_key": "bridge_control",
                "current_step_title": "Make a speaker available",
                "primary_action": {"key": "open_devices_settings", "label": "Open device settings"},
                "checkpoints": [],
                "steps": [
                    {
                        "key": "runtime_access",
                        "title": "Verify runtime host access",
                        "status": "ok",
                        "stage": "complete",
                    },
                    {"key": "bluetooth", "title": "Check Bluetooth access", "status": "ok", "stage": "complete"},
                    {"key": "audio", "title": "Verify audio backend", "status": "ok", "stage": "complete"},
                    {
                        "key": "bridge_control",
                        "title": "Make a speaker available",
                        "status": "warning",
                        "stage": "current",
                        "summary": "All configured speakers are currently released from bridge management.",
                        "details": {"configured_devices": 2},
                        "actions": ["Use Reclaim to hand at least one speaker back to the bridge."],
                        "recommended_action": {"key": "open_devices_settings", "label": "Open device settings"},
                    },
                    {
                        "key": "sink_verification",
                        "title": "Attach your first speaker",
                        "status": "warning",
                        "stage": "upcoming",
                    },
                ],
            },
            "counts": {"configured_devices": 2, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=False,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                extra={"bt_released_by": "user"},
            ),
            SimpleNamespace(
                player_name="Office",
                bt_management_enabled=False,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                extra={"bt_released_by": "user"},
            ),
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "healthy"
    assert data["issue_groups"] == []
    assert data["onboarding_card"]["show_by_default"] is True
    assert data["onboarding_card"]["primary_action"]["key"] == "toggle_bt_management_devices"
    assert data["onboarding_card"]["primary_action"]["device_names"] == ["Kitchen", "Office"]
    assert data["onboarding_card"]["checklist"]["current_step_title"] == "Reclaim a speaker"
    step = data["onboarding_card"]["checklist"]["steps"][3]
    assert step["title"] == "Reclaim a speaker"
    assert step["stage"] == "current"
    assert step["recommended_action"]["key"] == "toggle_bt_management_devices"
    assert step["recommended_action"]["device_names"] == ["Kitchen", "Office"]


def test_operator_guidance_reports_all_devices_disabled_as_neutral():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}]},
        onboarding_assistant={
            "checklist": {
                "overall_status": "warning",
                "progress_percent": 43,
                "completed_steps": 3,
                "total_steps": 7,
                "headline": "Next recommended step: Make a speaker available",
                "summary": "All configured Bluetooth speakers are globally disabled right now.",
                "current_step_key": "bridge_control",
                "current_step_title": "Make a speaker available",
                "primary_action": {"key": "open_devices_settings", "label": "Open device settings"},
                "checkpoints": [],
                "steps": [
                    {
                        "key": "runtime_access",
                        "title": "Verify runtime host access",
                        "status": "ok",
                        "stage": "complete",
                    },
                    {"key": "bluetooth", "title": "Check Bluetooth access", "status": "ok", "stage": "complete"},
                    {"key": "audio", "title": "Verify audio backend", "status": "ok", "stage": "complete"},
                    {
                        "key": "bridge_control",
                        "title": "Make a speaker available",
                        "status": "warning",
                        "stage": "current",
                        "summary": "All configured Bluetooth speakers are globally disabled right now.",
                        "details": {"configured_devices": 2},
                        "actions": ["Open Configuration → Devices and re-enable at least one speaker."],
                        "recommended_action": {"key": "open_devices_settings", "label": "Open device settings"},
                    },
                    {
                        "key": "sink_verification",
                        "title": "Attach your first speaker",
                        "status": "warning",
                        "stage": "upcoming",
                    },
                ],
            },
            "counts": {"configured_devices": 2, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[],
        disabled_devices=[
            {"player_name": "Kitchen", "enabled": False},
            {"player_name": "Office", "enabled": False},
        ],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "healthy"
    assert data["issue_groups"] == []
    assert data["header_status"]["tone"] == "neutral"
    assert data["header_status"]["label"] == "All devices disabled"
    assert data["onboarding_card"]["show_by_default"] is True
    assert data["onboarding_card"]["headline"] == "Re-enable a speaker to resume playback"
    assert data["onboarding_card"]["summary"].startswith("All configured Bluetooth devices are currently disabled.")
    assert data["onboarding_card"]["primary_action"]["key"] == "open_devices_settings"
    assert data["onboarding_card"]["checklist"]["current_step_title"] == "Re-enable a speaker"
    step = data["onboarding_card"]["checklist"]["steps"][3]
    assert step["title"] == "Re-enable a speaker"
    assert step["stage"] == "current"
    assert step["summary"] == "All configured speakers are globally disabled right now."


def test_operator_guidance_prioritizes_bluetooth_adapter_failure_over_disabled_devices():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checks": [
                {
                    "key": "bluetooth",
                    "status": "error",
                    "summary": "No Bluetooth controller detected by preflight checks.",
                    "details": {"paired_devices": 1},
                    "actions": ["Open Configuration → Bluetooth and confirm an adapter is available to the bridge."],
                }
            ],
            "checklist": {
                "overall_status": "error",
                "progress_percent": 14,
                "headline": "Next recommended step: Check Bluetooth access",
                "summary": "No Bluetooth controller detected by preflight checks.",
                "current_step_key": "bluetooth",
                "current_step_title": "Check Bluetooth access",
                "primary_action": {"key": "open_bluetooth_settings", "label": "Open Bluetooth settings"},
                "checkpoints": [],
                "steps": [
                    {
                        "key": "runtime_access",
                        "title": "Verify runtime host access",
                        "status": "ok",
                        "stage": "complete",
                    },
                    {
                        "key": "bluetooth",
                        "title": "Check Bluetooth access",
                        "status": "error",
                        "stage": "current",
                        "summary": "No Bluetooth controller detected by preflight checks.",
                        "details": {"paired_devices": 1},
                        "actions": ["Restore adapter access to the bridge."],
                        "recommended_action": {
                            "key": "open_bluetooth_settings",
                            "label": "Open Bluetooth settings",
                        },
                    },
                    {"key": "audio", "title": "Verify audio backend", "status": "ok", "stage": "complete"},
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
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[],
        disabled_devices=[{"player_name": "Kitchen", "enabled": False}],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "attention"
    assert data["header_status"]["tone"] == "error"
    assert data["header_status"]["label"] == "Bluetooth adapter unavailable"
    assert data["banner"]["headline"] == "Bluetooth adapter unavailable"
    assert data["issue_groups"][0]["key"] == "bluetooth_unavailable"
    assert data["issue_groups"][0]["primary_action"]["key"] == "open_bluetooth_settings"
    assert data["onboarding_card"]["show_by_default"] is True
    assert data["onboarding_card"]["headline"] == "Restore Bluetooth adapter access first"
    assert data["onboarding_card"]["primary_action"]["key"] == "open_bluetooth_settings"
    assert data["onboarding_card"]["secondary_actions"][0]["key"] == "open_devices_settings"
    assert data["onboarding_card"]["checklist"]["current_step_key"] == "bluetooth"
    step = data["onboarding_card"]["checklist"]["steps"][1]
    assert step["stage"] == "current"
    assert step["status"] == "error"
    assert "re-enable at least one speaker" in step["actions"][-1]


def test_operator_guidance_keeps_disabled_devices_neutral_when_bluetooth_check_is_healthy():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checks": [
                {
                    "key": "bluetooth",
                    "status": "ok",
                    "summary": "Bluetooth access is ready.",
                }
            ],
            "checklist": {
                "overall_status": "warning",
                "progress_percent": 43,
                "headline": "Next recommended step: Make a speaker available",
                "summary": "All configured Bluetooth speakers are globally disabled right now.",
                "current_step_key": "bridge_control",
                "current_step_title": "Make a speaker available",
                "primary_action": {"key": "open_devices_settings", "label": "Open device settings"},
                "checkpoints": [],
                "steps": [
                    {
                        "key": "runtime_access",
                        "title": "Verify runtime host access",
                        "status": "ok",
                        "stage": "complete",
                    },
                    {"key": "bluetooth", "title": "Check Bluetooth access", "status": "ok", "stage": "complete"},
                    {"key": "audio", "title": "Verify audio backend", "status": "ok", "stage": "complete"},
                    {
                        "key": "bridge_control",
                        "title": "Make a speaker available",
                        "status": "warning",
                        "stage": "current",
                        "summary": "All configured Bluetooth speakers are globally disabled right now.",
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
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[],
        disabled_devices=[{"player_name": "Kitchen", "enabled": False}],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "healthy"
    assert data["issue_groups"] == []
    assert data["header_status"]["label"] == "All devices disabled"
    assert data["onboarding_card"]["headline"] == "Re-enable a speaker to resume playback"


def test_operator_guidance_surfaces_mixed_disabled_and_unpaired_state():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checks": [
                {
                    "key": "bluetooth",
                    "status": "warning",
                    "summary": "No paired Bluetooth speakers are currently available, and the saved speaker is disabled.",
                    "details": {"paired_devices": 0, "configured_devices": 1, "disabled_devices": 1},
                    "actions": [
                        "Put a speaker in pairing mode, then open Bluetooth scan to pair or rediscover it.",
                        "After the speaker appears again, re-enable it in Configuration → Devices and restart the bridge.",
                    ],
                }
            ],
            "checklist": {
                "overall_status": "warning",
                "progress_percent": 14,
                "headline": "Next recommended step: Pair or rediscover a speaker",
                "summary": "No paired Bluetooth speakers are currently available, and the saved speaker is disabled.",
                "current_step_key": "bluetooth",
                "current_step_title": "Pair or rediscover a speaker",
                "primary_action": {"key": "scan_devices", "label": "Scan for speakers"},
                "checkpoints": [],
                "steps": [
                    {
                        "key": "runtime_access",
                        "title": "Verify runtime host access",
                        "status": "ok",
                        "stage": "complete",
                    },
                    {
                        "key": "bluetooth",
                        "title": "Pair or rediscover a speaker",
                        "status": "warning",
                        "stage": "current",
                        "summary": "No paired Bluetooth speakers are currently available, and the saved speaker is disabled.",
                        "details": {"paired_devices": 0, "configured_devices": 1, "disabled_devices": 1},
                        "actions": [
                            "Put a speaker in pairing mode, then open Bluetooth scan to pair or rediscover it.",
                            "After the speaker appears again, re-enable it in Configuration → Devices and restart the bridge.",
                        ],
                        "recommended_action": {"key": "scan_devices", "label": "Scan for speakers"},
                    },
                    {"key": "audio", "title": "Verify audio backend", "status": "ok", "stage": "upcoming"},
                    {
                        "key": "bridge_control",
                        "title": "Make a speaker available",
                        "status": "warning",
                        "stage": "upcoming",
                    },
                ],
            },
            "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[],
        disabled_devices=[{"player_name": "Kitchen", "enabled": False}],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "healthy"
    assert data["issue_groups"] == []
    assert data["header_status"]["tone"] == "warning"
    assert data["header_status"]["label"] == "No playable speaker available"
    assert "re-enable it" in data["header_status"]["summary"]
    assert data["onboarding_card"]["show_by_default"] is True
    assert data["onboarding_card"]["headline"] == "Pair or rediscover a speaker first"
    assert data["onboarding_card"]["primary_action"]["key"] == "scan_devices"
    assert data["onboarding_card"]["secondary_actions"][0]["key"] == "open_devices_settings"
    assert data["onboarding_card"]["checklist"]["current_step_title"] == "Pair or rediscover a speaker"
    step = data["onboarding_card"]["checklist"]["steps"][1]
    assert step["title"] == "Pair or rediscover a speaker"
    assert step["stage"] == "current"
    assert "re-enable it" in step["actions"][-1].lower()


def test_operator_guidance_prioritizes_runtime_access_over_disabled_devices():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checks": [
                {
                    "key": "runtime_access",
                    "status": "error",
                    "summary": "The bridge runtime cannot reach the host D-Bus services required for Bluetooth control.",
                    "details": {"dbus": False},
                    "actions": ["Open diagnostics and confirm D-Bus is reachable from this runtime."],
                }
            ],
            "checklist": {
                "overall_status": "error",
                "progress_percent": 0,
                "headline": "Finish setup: Verify runtime host access",
                "summary": "The bridge runtime cannot reach the host D-Bus services required for Bluetooth control.",
                "current_step_key": "runtime_access",
                "current_step_title": "Verify runtime host access",
                "primary_action": {"key": "open_diagnostics", "label": "Open diagnostics"},
                "checkpoints": [],
                "steps": [
                    {
                        "key": "runtime_access",
                        "title": "Verify runtime host access",
                        "status": "error",
                        "stage": "current",
                        "summary": "The bridge runtime cannot reach the host D-Bus services required for Bluetooth control.",
                        "details": {"dbus": False},
                        "actions": ["Open diagnostics and confirm D-Bus is reachable from this runtime."],
                        "recommended_action": {"key": "open_diagnostics", "label": "Open diagnostics"},
                    },
                    {"key": "bluetooth", "title": "Check Bluetooth access", "status": "warning", "stage": "upcoming"},
                    {"key": "audio", "title": "Verify audio backend", "status": "warning", "stage": "upcoming"},
                    {
                        "key": "bridge_control",
                        "title": "Make a speaker available",
                        "status": "warning",
                        "stage": "upcoming",
                    },
                ],
            },
            "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "No active recovery issues."}},
        startup_progress={"status": "complete"},
        devices=[],
        disabled_devices=[{"player_name": "Kitchen", "enabled": False}],
    )

    data = snapshot.to_dict()
    assert data["mode"] == "attention"
    assert data["header_status"]["label"] == "Host service access unavailable"
    assert data["issue_groups"][0]["key"] == "runtime_access"
    assert data["onboarding_card"]["headline"] == "Restore host service access first"
    assert data["onboarding_card"]["checklist"]["current_step_key"] == "runtime_access"
