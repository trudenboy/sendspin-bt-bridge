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
    assert checks["bluetooth"].status == "error"
    assert checks["audio"].status == "error"
    assert checks["sink_verification"].status == "warning"
    assert checks["ma_auth"].status == "warning"
    assert snapshot.next_steps


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
    assert checks["bluetooth"].status == "ok"
    assert checks["audio"].status == "ok"
    assert checks["sink_verification"].status == "ok"
    assert checks["ma_auth"].status == "ok"
    assert checks["latency"].status == "ok"
    assert snapshot.counts["sink_ready_devices"] == 2
