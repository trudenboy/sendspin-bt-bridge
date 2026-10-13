"""Guidance for a pulse_sink player whose local output is muted, at zero or unplugged."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sendspin_bridge.services.audio.sink_inventory import LocalSink
from sendspin_bridge.services.diagnostics.guidance_issue_registry import ISSUE_REGISTRY
from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot

SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"
OUTPUT_KEYS = ("output_muted", "output_volume_zero", "output_port_unavailable")


@pytest.fixture(autouse=True)
def _config_writable(monkeypatch):
    import sendspin_bridge.services.diagnostics.recovery_assistant as module

    monkeypatch.setattr(module, "collect_preflight_status", lambda: {"config_writable": {"status": "ok"}})


def _sink(*, muted=False, volume=100, availability=None) -> LocalSink:
    return LocalSink(SINK, "USB DAC", "idle", "s16le 2ch 48000Hz", muted, volume, "analog-output", availability, {})


def _device(name="Desk DAC", *, backend="pulse_sink", sink=SINK, enabled=True, missing=False) -> SimpleNamespace:
    return SimpleNamespace(
        player_name=name,
        bt_management_enabled=True,
        bluetooth_connected=backend != "pulse_sink",
        has_sink=True,
        server_connected=True,
        static_delay_ms=0.0,
        enabled=enabled,
        recent_events=[],
        health_summary={"state": "ready", "severity": "ok", "summary": ""},
        extra={"backend_type": backend, "audio_sink": sink, "sink_missing": missing},
    )


def _issues(devices, local_sinks):
    snapshot = build_recovery_assistant_snapshot(
        config={},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok"}},
        startup_progress={},
        local_sinks=local_sinks,
    )
    return {issue.key: issue for issue in snapshot.issues if issue.key in OUTPUT_KEYS}


def test_a_muted_output_says_music_assistant_volume_cannot_raise_it():
    issues = _issues([_device()], [_sink(muted=True)])

    issue = issues["output_muted"]
    assert issue.severity == "warning"
    assert issue.device_name == "Desk DAC"
    assert "Music Assistant" in issue.summary
    assert "cannot" in issue.summary
    assert SINK in issue.summary


def test_a_volume_of_zero_is_reported():
    issues = _issues([_device()], [_sink(volume=0)])

    assert set(issues) == {"output_volume_zero"}
    assert "0 %" in issues["output_volume_zero"].summary


def test_an_unplugged_port_is_reported():
    issues = _issues([_device()], [_sink(availability="not available")])

    assert set(issues) == {"output_port_unavailable"}


@pytest.mark.parametrize("availability", ["availability unknown", "available", None])
def test_an_unknown_or_available_port_is_no_issue(availability):
    assert _issues([_device()], [_sink(availability=availability)]) == {}


def test_a_healthy_output_is_no_issue():
    assert _issues([_device()], [_sink()]) == {}


def test_bluetooth_players_get_none_of_these():
    device = _device("Kitchen", backend="bluetooth_a2dp", sink=SINK)

    assert _issues([device], [_sink(muted=True, volume=0, availability="not available")]) == {}


def test_a_disabled_player_and_an_absent_sink_are_no_issue():
    sink = _sink(muted=True)

    assert _issues([_device(enabled=False)], [sink]) == {}
    assert _issues([_device(sink="some.other.sink")], [sink]) == {}
    assert _issues([_device(sink=None)], [sink]) == {}


def test_no_inventory_is_no_issue():
    assert _issues([_device()], None) == {}
    assert _issues([_device()], []) == {}


def test_every_output_issue_is_registered():
    for key in OUTPUT_KEYS:
        assert ISSUE_REGISTRY[key].severity == "warning"
        assert ISSUE_REGISTRY[key].layer == "sink_verification"


def test_operator_guidance_shows_the_output_issue_as_a_card():
    from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot

    devices = [_device("Desk DAC"), _device("Hall DAC")]
    recovery = build_recovery_assistant_snapshot(
        config={},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok"}},
        startup_progress={},
        local_sinks=[_sink(muted=True)],
    ).to_dict()

    data = build_operator_guidance_snapshot(
        config={},
        onboarding_assistant={"checklist": {"overall_status": "ok", "progress_percent": 100}, "counts": {}},
        recovery_assistant=recovery,
        startup_progress={"status": "complete"},
        devices=devices,
    ).to_dict()

    group = next(g for g in data["issue_groups"] if g["key"] == "output_muted")
    assert group["severity"] == "warning"
    assert group["device_names"] == ["Desk DAC", "Hall DAC"]


def _state_for(devices):
    from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model

    return build_bridge_state_model(
        config={}, preflight=None, devices=devices, ma_connected=False, runtime_mode="production"
    )


def test_the_payload_reads_the_inventory_once_and_only_for_pulse_sink_players(monkeypatch):
    from sendspin_bridge.application import diagnostics

    reads: list[int] = []

    def fake_read():
        reads.append(1)
        return [_sink(muted=True)]

    monkeypatch.setattr("sendspin_bridge.application.audio.read_local_sinks_recent", fake_read)
    onboarding = {"checklist": {"overall_status": "ok"}}

    def payload(devices):
        return diagnostics._build_recovery_assistant_payload(
            config={},
            devices=devices,
            onboarding_assistant=onboarding,
            startup_progress={},
            bridge_state=_state_for(devices),
        )

    bluetooth_only = payload([_device("Kitchen", backend="bluetooth_a2dp")])
    assert reads == []
    assert not [i for i in bluetooth_only["issues"] if i["key"] in OUTPUT_KEYS]

    with_output = payload([_device("Desk DAC"), _device("Hall DAC"), _device("Kitchen", backend="bluetooth_a2dp")])
    assert reads == [1]
    flagged = [i["device_name"] for i in with_output["issues"] if i["key"] == "output_muted"]
    assert flagged == ["Desk DAC", "Hall DAC"]
