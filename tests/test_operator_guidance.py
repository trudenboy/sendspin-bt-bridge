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
    assert data["header_status"]["label"] == "Finalizing startup"
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


def test_operator_guidance_treats_user_released_devices_as_neutral():
    snapshot = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], "BLUETOOTH_DEVICES": [{"mac": "AA"}]},
        onboarding_assistant={
            "checklist": {"overall_status": "warning", "progress_percent": 0, "summary": "No active devices."},
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
