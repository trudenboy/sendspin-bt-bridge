from __future__ import annotations

from types import SimpleNamespace

from services.onboarding_assistant import build_onboarding_assistant_snapshot


def test_onboarding_assistant_reports_missing_prerequisites():
    snapshot = build_onboarding_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [], "PULSE_LATENCY_MSEC": 200},
        preflight={
            "audio": {"system": "unknown", "sinks": 0},
            "bluetooth": {"controller": False, "paired_devices": 0},
        },
        devices=[],
        ma_connected=False,
        runtime_mode="production",
    )

    checks = {check.key: check for check in snapshot.checks}
    assert checks["runtime_access"].status == "ok"
    assert checks["bluetooth"].status == "error"
    assert checks["audio"].status == "error"
    assert checks["bridge_control"].status == "ok"
    assert checks["sink_verification"].status == "warning"
    assert checks["ma_auth"].status == "warning"
    assert snapshot.next_steps
    assert snapshot.checklist is not None
    assert snapshot.checklist.current_step_key == "bluetooth"
    assert snapshot.checklist.primary_action is not None
    assert snapshot.checklist.primary_action.key == "open_bluetooth_settings"
    assert snapshot.checklist.progress_percent == 14
    assert snapshot.checklist.journey_key == "first_speaker"
    assert snapshot.checklist.phases[0].key == "foundation"
    assert snapshot.checklist.phases[0].status == "current"
    assert snapshot.checklist.checkpoints[0].reached is False
    assert snapshot.checklist.steps[0].key == "runtime_access"
    assert snapshot.checklist.steps[0].stage == "complete"
    assert snapshot.checklist.steps[1].key == "bluetooth"
    assert snapshot.checklist.steps[1].stage == "current"
    assert snapshot.checklist.steps[2].stage == "upcoming"
    assert checks["ma_auth"].details["auto_discovery_available"] is True


def test_onboarding_assistant_blocks_lower_layers_when_runtime_access_fails():
    snapshot = build_onboarding_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 200},
        preflight={
            "dbus": False,
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 1},
        },
        devices=[],
        ma_connected=False,
        runtime_mode="production",
    )

    assert snapshot.checklist is not None
    assert snapshot.checklist.current_step_key == "runtime_access"
    runtime_step = snapshot.checklist.steps[0]
    assert runtime_step.key == "runtime_access"
    assert runtime_step.stage == "current"
    assert runtime_step.recommended_action is not None
    assert runtime_step.recommended_action.key == "open_diagnostics"
    assert snapshot.checklist.steps[1].stage == "upcoming"
    assert snapshot.checklist.steps[2].stage == "upcoming"


def test_onboarding_assistant_reports_connected_bridge_readiness():
    devices = [
        SimpleNamespace(
            player_name="Kitchen",
            bluetooth_connected=True,
            has_sink=True,
            static_delay_ms=-500.0,
        ),
        SimpleNamespace(
            player_name="Office",
            bluetooth_connected=True,
            has_sink=True,
            static_delay_ms=-400.0,
        ),
    ]

    snapshot = build_onboarding_assistant_snapshot(
        config={
            "BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}],
            "PULSE_LATENCY_MSEC": 300,
            "MA_API_URL": "http://ma.local",
            "MA_API_TOKEN": "token",
        },
        preflight={
            "audio": {"system": "pulseaudio", "sinks": 2},
            "bluetooth": {"controller": True, "paired_devices": 2},
        },
        devices=devices,
        ma_connected=True,
        runtime_mode="production",
    )

    checks = {check.key: check for check in snapshot.checks}
    assert checks["runtime_access"].status == "ok"
    assert checks["bluetooth"].status == "ok"
    assert checks["audio"].status == "ok"
    assert checks["bridge_control"].status == "ok"
    assert checks["sink_verification"].status == "ok"
    assert checks["ma_auth"].status == "ok"
    assert checks["latency"].status == "ok"
    assert snapshot.counts["sink_ready_devices"] == 2
    assert snapshot.checklist is not None
    assert snapshot.checklist.overall_status == "ok"
    assert snapshot.checklist.current_step_key is None
    assert snapshot.checklist.progress_percent == 100
    assert snapshot.checklist.journey_key == "multi_room"
    assert all(phase.status == "complete" for phase in snapshot.checklist.phases)
    assert all(step.stage == "complete" for step in snapshot.checklist.steps)
    assert all(checkpoint.reached for checkpoint in snapshot.checklist.checkpoints)


def test_onboarding_assistant_recommends_scanning_for_unpaired_speakers():
    snapshot = build_onboarding_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 200},
        preflight={
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 0},
        },
        devices=[],
        ma_connected=False,
        runtime_mode="production",
    )

    assert snapshot.checklist is not None
    bluetooth_step = next(step for step in snapshot.checklist.steps if step.key == "bluetooth")
    assert bluetooth_step.title == "Pair or rediscover a speaker"
    assert bluetooth_step.recommended_action is not None
    assert bluetooth_step.recommended_action.key == "scan_devices"
    assert "pairing mode" in bluetooth_step.actions[0].lower()


def test_onboarding_assistant_recommends_device_scan_when_no_devices_configured():
    snapshot = build_onboarding_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [], "PULSE_LATENCY_MSEC": 200},
        preflight={
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 1},
        },
        devices=[],
        ma_connected=False,
        runtime_mode="production",
    )

    assert snapshot.checklist is not None
    sink_step = next(step for step in snapshot.checklist.steps if step.key == "sink_verification")
    assert sink_step.recommended_action is not None
    assert sink_step.recommended_action.key == "scan_devices"
    assert "pairing mode" in sink_step.actions[0].lower()


def test_onboarding_assistant_surfaces_bridge_control_before_sink_checks_for_disabled_devices():
    snapshot = build_onboarding_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA", "enabled": False}], "PULSE_LATENCY_MSEC": 200},
        preflight={
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 1},
        },
        devices=[],
        ma_connected=False,
        runtime_mode="production",
    )

    assert snapshot.checklist is not None
    assert snapshot.checklist.current_step_key == "bridge_control"
    bridge_step = next(step for step in snapshot.checklist.steps if step.key == "bridge_control")
    sink_step = next(step for step in snapshot.checklist.steps if step.key == "sink_verification")
    assert bridge_step.stage == "current"
    assert bridge_step.recommended_action is not None
    assert bridge_step.recommended_action.key == "open_devices_settings"
    assert sink_step.stage == "upcoming"


def test_onboarding_assistant_explains_disabled_plus_unpaired_mixed_state():
    snapshot = build_onboarding_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA", "enabled": False}], "PULSE_LATENCY_MSEC": 200},
        preflight={
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 0},
        },
        devices=[],
        ma_connected=False,
        runtime_mode="production",
    )

    assert snapshot.checklist is not None
    assert snapshot.checklist.current_step_key == "bluetooth"
    assert snapshot.checklist.current_step_title == "Pair or rediscover a speaker"
    assert snapshot.checklist.primary_action is not None
    assert snapshot.checklist.primary_action.key == "scan_devices"
    bluetooth_step = next(step for step in snapshot.checklist.steps if step.key == "bluetooth")
    bridge_step = next(step for step in snapshot.checklist.steps if step.key == "bridge_control")
    assert bluetooth_step.stage == "current"
    assert bluetooth_step.title == "Pair or rediscover a speaker"
    assert "saved speaker is disabled" in bluetooth_step.summary.lower()
    assert "re-enable it" in bluetooth_step.actions[-1].lower()
    assert bridge_step.stage == "upcoming"


def test_onboarding_assistant_points_latency_step_to_general_latency_setting():
    devices = [
        SimpleNamespace(
            player_name="Kitchen",
            bluetooth_connected=True,
            has_sink=True,
            static_delay_ms=-400.0,
        ),
        SimpleNamespace(
            player_name="Office",
            bluetooth_connected=True,
            has_sink=True,
            static_delay_ms=-350.0,
        ),
    ]

    snapshot = build_onboarding_assistant_snapshot(
        config={
            "BLUETOOTH_DEVICES": [{"mac": "AA"}, {"mac": "BB"}],
            "PULSE_LATENCY_MSEC": 1000,
            "MA_API_URL": "http://ma.local",
            "MA_API_TOKEN": "token",
        },
        preflight={
            "audio": {"system": "pulseaudio", "sinks": 2},
            "bluetooth": {"controller": True, "paired_devices": 2},
        },
        devices=devices,
        ma_connected=True,
        runtime_mode="production",
    )

    assert snapshot.checklist is not None
    latency_step = next(step for step in snapshot.checklist.steps if step.key == "latency")
    assert latency_step.recommended_action is not None
    assert latency_step.recommended_action.key == "apply_latency_recommended"
    assert latency_step.recommended_action.label == "Apply 600 ms latency"
    assert latency_step.recommended_action.value == 600


def test_onboarding_assistant_recommends_discovery_when_ma_url_missing():
    snapshot = build_onboarding_assistant_snapshot(
        config={"BLUETOOTH_DEVICES": [{"mac": "AA"}], "PULSE_LATENCY_MSEC": 200},
        preflight={
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 1},
        },
        devices=[
            SimpleNamespace(
                player_name="Kitchen",
                bluetooth_connected=True,
                has_sink=True,
                static_delay_ms=0.0,
            )
        ],
        ma_connected=False,
        runtime_mode="production",
    )

    assert snapshot.checklist is not None
    ma_step = next(step for step in snapshot.checklist.steps if step.key == "ma_auth")
    assert ma_step.recommended_action is not None
    assert ma_step.recommended_action.key == "retry_ma_discovery"
    assert ma_step.recommended_action.label == "Discover Music Assistant"
