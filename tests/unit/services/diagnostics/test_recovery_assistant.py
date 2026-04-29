from __future__ import annotations

from types import SimpleNamespace

import pytest

from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot


@pytest.fixture(autouse=True)
def _isolate_config_writable_check(monkeypatch):
    """Default tests in this file don't supply a preflight payload and
    don't mock CONFIG_DIR, so the strict missing-dir check would
    surface a ``config_dir_not_writable`` card on dev machines where
    ``/config`` is unreachable.  Force the issue builder to no-op
    here; tests that exercise the card explicitly mock
    ``collect_preflight_status`` themselves and bypass this fixture
    via direct monkeypatch ordering."""
    import sendspin_bridge.services.diagnostics.recovery_assistant as recovery_module

    monkeypatch.setattr(
        recovery_module,
        "collect_preflight_status",
        lambda: {"config_writable": {"status": "ok"}},
    )


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


def test_recovery_assistant_surfaces_config_writable_failure_as_issue(monkeypatch):
    """Issue #190 — when preflight reports ``config_writable.status =
    degraded``, the recovery snapshot must include an issue card with
    key ``config_dir_not_writable``, the chown remediation in the
    summary, and an error severity.  This is what makes the failure
    visible in the Diagnostics panel without operators reading
    container logs."""
    import sendspin_bridge.services.diagnostics.recovery_assistant as recovery_module

    monkeypatch.setattr(
        recovery_module,
        "collect_preflight_status",
        lambda: {
            "status": "degraded",
            "config_writable": {
                "status": "degraded",
                "writable": False,
                "config_dir": "/config",
                "uid": 1000,
                "remediation": "chown -R 1000:1000 <bind-mount target for /config>",
                "error": {"code": "permission_denied"},
            },
        },
    )

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 250},
        devices=[],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    keys = [issue["key"] for issue in data["issues"]]
    assert "config_dir_not_writable" in keys
    card = next(issue for issue in data["issues"] if issue["key"] == "config_dir_not_writable")
    assert card["severity"] == "error"
    assert "/config" in card["title"]
    assert "chown" in card["summary"].lower()
    assert "1000" in card["summary"]
    # Recovery snapshot summary must escalate to "error"
    assert data["summary"]["highest_severity"] == "error"


def test_recovery_assistant_omits_config_writable_card_when_ok(monkeypatch):
    """Happy path: when preflight is clean, the recovery snapshot
    must not introduce noise.  Pin this so a future refactor doesn't
    accidentally always-render the card."""
    import sendspin_bridge.services.diagnostics.recovery_assistant as recovery_module

    monkeypatch.setattr(
        recovery_module,
        "collect_preflight_status",
        lambda: {
            "status": "ok",
            "config_writable": {"status": "ok", "writable": True, "config_dir": "/config", "uid": 1000},
        },
    )

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 250},
        devices=[],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    keys = [issue["key"] for issue in data["issues"]]
    assert "config_dir_not_writable" not in keys


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


def test_recovery_assistant_ignores_transport_down_while_audio_is_streaming():
    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 250},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=False,
                playing=True,
                static_delay_ms=0.0,
                recent_events=[],
                health_summary={"state": "streaming", "severity": "info", "summary": "Streaming audio"},
                extra={"audio_streaming": True},
            )
        ],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert data["issues"] == []
    assert data["summary"]["highest_severity"] == "ok"


def test_recovery_assistant_ignores_transport_down_during_planned_ma_reconnect():
    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 250},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=False,
                playing=False,
                static_delay_ms=0.0,
                recent_events=[],
                health_summary={
                    "state": "recovering",
                    "severity": "warning",
                    "summary": "Refreshing Music Assistant connection",
                },
                extra={"ma_reconnecting": True},
            )
        ],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert data["issues"] == []
    assert data["summary"]["highest_severity"] == "ok"


def test_recovery_assistant_transport_down_with_connection_error_suggests_port_check():
    """When transport is down due to connection error, guidance should mention SENDSPIN_PORT."""
    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 250},
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bt_management_enabled=True,
                bluetooth_connected=True,
                has_sink=True,
                server_connected=False,
                playing=False,
                static_delay_ms=0.0,
                recent_events=[],
                health_summary={
                    "state": "degraded",
                    "severity": "error",
                    "summary": "Cannot connect to Sendspin server at ws://192.168.1.10:9000/sendspin. Check that SENDSPIN_PORT matches your Music Assistant Sendspin port.",
                },
                extra={"last_error": "Cannot connect to Sendspin server at ws://192.168.1.10:9000/sendspin"},
            )
        ],
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert len(data["issues"]) == 1
    issue = data["issues"][0]
    assert issue["key"] == "sendspin_port_unreachable"
    assert "SENDSPIN_PORT" in issue["summary"]
    assert issue["primary_action"]["key"] == "open_config"


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


def test_recovery_assistant_flags_sink_system_muted():
    """When PA sink is muted at system level but app is not muted, flag it."""
    devices = [
        SimpleNamespace(
            player_name="Bedroom",
            bt_management_enabled=True,
            bluetooth_connected=True,
            has_sink=True,
            server_connected=True,
            static_delay_ms=0.0,
            recent_events=[],
            health_summary={"state": "degraded", "severity": "warning", "summary": "Audio sink muted at system level"},
            extra={"sink_muted": True, "muted": False},
        ),
    ]

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "CC"}], "PULSE_LATENCY_MSEC": 300},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert len(data["issues"]) == 1
    assert data["issues"][0]["key"] == "sink_system_muted"
    assert data["issues"][0]["title"] == "Bedroom audio sink is muted at system level"
    assert data["issues"][0]["primary_action"]["key"] == "unmute_sink"
    assert data["issues"][0]["recommended_action"]["key"] == "unmute_sink"


def test_recovery_assistant_no_sink_muted_issue_when_app_muted():
    """When user muted explicitly, sink_muted is expected — no recovery issue."""
    devices = [
        SimpleNamespace(
            player_name="Bedroom",
            bt_management_enabled=True,
            bluetooth_connected=True,
            has_sink=True,
            server_connected=True,
            static_delay_ms=0.0,
            recent_events=[],
            health_summary={"state": "ready", "severity": "info", "summary": "Connected and ready"},
            extra={"sink_muted": True, "muted": True},
        ),
    ]

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "CC"}], "PULSE_LATENCY_MSEC": 300},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "complete", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    assert data["summary"]["open_issue_count"] == 0


def test_recovery_assistant_surfaces_samsung_cod_filter_card():
    """When ``pair_device`` writes ``pair_failure_kind=samsung_cod_filter``
    onto the device's status, recovery_assistant must surface a
    targeted card directing the operator to Settings → Bluetooth — not
    the generic ``repair_required`` / ``disconnected`` card, which
    would tell them to re-pair (action that won't help, since the
    soundbar will reject the next attempt the same way).

    The adapter MAC captured at pair time is included in the summary
    so multi-adapter deployments can tell which controller needs the
    override."""
    devices = [
        SimpleNamespace(
            player_name="Q910B",
            bt_management_enabled=True,
            bluetooth_connected=False,
            has_sink=False,
            server_connected=False,
            static_delay_ms=0.0,
            recent_events=[],
            health_summary={
                "state": "degraded",
                "severity": "error",
                "summary": "Pairing rejected by speaker.",
            },
            extra={
                "pair_failure_kind": "samsung_cod_filter",
                "pair_failure_adapter_mac": "F4:4E:FC:C1:E0:31",
                "bluetooth_paired": False,
            },
        ),
    ]

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "1C:86:9A:71:E0:F5"}], "PULSE_LATENCY_MSEC": 300},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "running", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    samsung_issues = [i for i in data["issues"] if i["key"] == "samsung_cod_filter"]
    assert len(samsung_issues) == 1, "Samsung CoD filter card should fire exactly once"
    issue = samsung_issues[0]
    assert issue["severity"] == "error"
    assert issue["title"] == "Q910B pair rejected by Class of Device filter"
    # The adapter MAC must be visible — without it the operator can't
    # tell which row to touch on a multi-adapter host.
    assert "F4:4E:FC:C1:E0:31" in issue["summary"]
    # The recommended next click is the Bluetooth tab, where the
    # ``device_class`` dropdown lives.  Re-pair sits in secondary
    # actions because it's the post-fix verification step, not the
    # primary action.
    assert issue["primary_action"]["key"] == "open_bt_settings"
    secondary_keys = [a["key"] for a in issue["secondary_actions"]]
    assert "pair_device" in secondary_keys


def test_recovery_assistant_suppresses_samsung_cod_filter_when_device_connected():
    """Defence-in-depth: even if a stale ``pair_failure_kind`` lingers
    on the device (e.g. operator hot-fixed the CoD on the host without
    waiting for the bridge to clear the fingerprint, then the speaker
    reconnected), the card must NOT cover a device that is currently
    connected.  Surfacing it would tell operators their working setup
    is broken — exactly the kind of false positive that erodes trust
    in the recovery banner."""
    devices = [
        SimpleNamespace(
            player_name="Q910B",
            bt_management_enabled=True,
            bluetooth_connected=True,  # <- now connected; old fingerprint must NOT fire
            has_sink=True,
            server_connected=True,
            static_delay_ms=0.0,
            recent_events=[],
            health_summary={"state": "ready", "severity": "info", "summary": "Connected and ready."},
            extra={
                "pair_failure_kind": "samsung_cod_filter",
                "pair_failure_adapter_mac": "F4:4E:FC:C1:E0:31",
            },
        ),
    ]

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "1C:86:9A:71:E0:F5"}], "PULSE_LATENCY_MSEC": 300},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "running", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    issue_keys = [i["key"] for i in data["issues"]]
    assert "samsung_cod_filter" not in issue_keys, (
        "Samsung CoD filter card must not surface for a currently-connected device — the past failure has been resolved"
    )


def test_recovery_assistant_samsung_cod_filter_takes_precedence_over_repair():
    """An unpaired device that ALSO carries the Samsung CoD-filter
    fingerprint must surface the specific card, not the generic
    ``repair_required``.  The two failure modes have different fixes
    (set device_class vs. re-pair) and the bridge should not let the
    operator chase the wrong remediation."""
    devices = [
        SimpleNamespace(
            player_name="Q910B",
            bt_management_enabled=True,
            bluetooth_connected=False,
            has_sink=False,
            server_connected=False,
            static_delay_ms=0.0,
            recent_events=[],
            health_summary={"state": "degraded", "severity": "error", "summary": ""},
            extra={
                "pair_failure_kind": "samsung_cod_filter",
                "pair_failure_adapter_mac": "F4:4E:FC:C1:E0:31",
                "bluetooth_paired": False,
            },
        ),
    ]

    snapshot = build_recovery_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "1C:86:9A:71:E0:F5"}], "PULSE_LATENCY_MSEC": 300},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok", "checkpoints": []}},
        startup_progress={"status": "running", "message": "Startup complete."},
    )

    data = snapshot.to_dict()
    issue_keys = [i["key"] for i in data["issues"]]
    assert "samsung_cod_filter" in issue_keys
    assert "repair_required" not in issue_keys, (
        "Samsung CoD filter card must replace, not coexist with, repair_required — "
        "the operator should see exactly one actionable card per device"
    )
