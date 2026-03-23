from __future__ import annotations

from types import SimpleNamespace

from services.recovery_assistant import build_recovery_assistant_snapshot


def test_recovery_assistant_flags_sink_and_disconnect_issues():
    devices = [
        SimpleNamespace(
            player_name="Kitchen",
            bt_management_enabled=True,
            bluetooth_connected=True,
            has_sink=False,
            server_connected=True,
            static_delay_ms=0.0,
            recent_events=[
                {
                    "at": "2026-03-20T10:00:00+00:00",
                    "level": "warning",
                    "event_type": "sink_missing",
                    "message": "No sink after reconnect",
                }
            ],
            health_summary={"state": "degraded", "severity": "error", "summary": "Sink is missing after reconnect."},
        ),
        SimpleNamespace(
            player_name="Office",
            bt_management_enabled=True,
            bluetooth_connected=False,
            has_sink=False,
            server_connected=False,
            static_delay_ms=0.0,
            recent_events=[],
            health_summary={"state": "recovering", "severity": "warning", "summary": "Speaker is offline."},
        ),
    ]

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}], "PULSE_LATENCY_MSEC": 300},
        devices=devices,
        onboarding_assistant={
            "checklist": {
                "overall_status": "warning",
                "current_step_title": "Connect Music Assistant",
                "summary": "Music Assistant still needs to be linked.",
                "current_step_key": "ma_auth",
                "primary_action": {"key": "open_ma_settings", "label": "Open Music Assistant settings"},
                "checkpoints": [
                    {"key": "bluetooth_connected", "reached": True, "summary": "At least one speaker is connected."},
                    {"key": "sink_ready", "reached": False, "summary": "No sink is stable yet."},
                    {"key": "ma_visible", "reached": False, "summary": "MA is not visible yet."},
                ],
            }
        },
        startup_progress={
            "status": "running",
            "phase": "startup",
            "message": "Waiting for devices to stabilize.",
            "updated_at": "2026-03-20T10:01:00+00:00",
        },
    )

    data = snapshot.to_dict()
    assert data["summary"]["open_issue_count"] == 3
    assert data["summary"]["highest_severity"] == "error"
    assert data["issues"][0]["title"] == "Kitchen is missing a sink"
    assert data["issues"][0]["primary_action"]["key"] == "reconnect_device"
    assert data["issues"][0]["recommended_action"]["key"] == "reconnect_device"
    assert data["issues"][0]["secondary_actions"][0]["key"] == "rerun_safe_check"
    assert data["issues"][0]["secondary_actions"][0]["check_key"] == "sink_verification"
    assert data["safe_actions"][2]["key"] == "rerun_safe_check"
    assert data["safe_actions"][2]["check_key"] == "runtime_access"
    assert data["traces"][0]["label"] == "Bridge startup"
    assert data["latency_assistant"]["tone"] == "warning"
    assert data["latency_assistant"]["presets"][0]["value"] == 300
    assert data["timeline"]["summary"]["entry_count"] >= 2
    assert data["known_good_test_path"]["steps"][0]["reached"] is True
    assert data["known_good_test_path"]["steps"][1]["reached"] is False


def test_recovery_assistant_reports_healthy_single_device_setup():
    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 250},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=True,
                static_delay_ms=0.0,
                recent_events=[],
                health_summary={"state": "ready", "severity": "ok", "summary": "Healthy."},
            )
        ],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert data["summary"]["highest_severity"] == "ok"
    assert data["issues"] == []
    assert data["latency_assistant"]["tone"] == "ok"
    assert "Single-device" in data["latency_assistant"]["summary"]
    assert data["timeline"]["summary"]["entry_count"] == 1


def test_recovery_assistant_prefers_repair_for_unpaired_device():
    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 250},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                static_delay_ms=0.0,
                recent_events=[],
                health_summary={"state": "recovering", "severity": "warning", "summary": ""},
                extra={"bluetooth_paired": False, "reconnect_attempt": 3, "max_reconnect_fails": 5},
            )
        ],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert data["issues"][0]["title"] == "Kitchen needs re-pairing"
    assert data["issues"][0]["primary_action"]["key"] == "pair_device"
    assert data["issues"][0]["recommended_action"]["key"] == "pair_device"
    assert data["issues"][0]["secondary_actions"][0]["key"] == "toggle_bt_management"
    assert data["issues"][0]["secondary_actions"][1]["key"] == "open_diagnostics"
    assert "3/5" in data["issues"][0]["summary"]


def test_recovery_assistant_only_flags_auto_released_devices():
    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}], "PULSE_LATENCY_MSEC": 250},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=False,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                static_delay_ms=0.0,
                recent_events=[],
                health_summary={"state": "released", "severity": "warning", "summary": ""},
                extra={"bt_released_by": "user"},
            ),
            SimpleNamespace(
                player_name="Office",
                bt_management_enabled=False,
                bluetooth_connected=False,
                has_sink=False,
                server_connected=False,
                static_delay_ms=0.0,
                recent_events=[],
                health_summary={"state": "released", "severity": "warning", "summary": ""},
                extra={"bt_released_by": "auto"},
            ),
        ],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert len(data["issues"]) == 1
    assert data["issues"][0]["title"] == "Office was auto-released"
    assert data["issues"][0]["primary_action"]["key"] == "toggle_bt_management"
    assert data["issues"][0]["recommended_action"]["key"] == "toggle_bt_management"
    assert data["issues"][0]["secondary_actions"][0]["key"] == "open_diagnostics"
