from __future__ import annotations

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
