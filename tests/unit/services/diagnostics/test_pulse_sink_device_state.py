"""A ``pulse_sink`` player is judged by its output, never as a Bluetooth speaker.

Each layer that describes a device to an operator is driven with the same
snapshot the bridge builds: health, transfer readiness, recovery issues and
the guidance cards.  "Connected" for a local output means its sink is present;
a missing one is its own state, with its own remedy — never a Bluetooth
reconnect or re-pair, which answer 409 for this backend.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.bluetooth.device_health_state import compute_device_health_state
from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot
from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot
from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state
from sendspin_bridge.services.lifecycle.status_snapshot import DeviceSnapshot, _build_transfer_readiness

SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"
MAC = "AA:BB:CC:DD:EE:01"

# Keys and actions that only mean something for a Bluetooth speaker.
BLUETOOTH_ISSUES = {"disconnected", "repair_required", "never_paired", "missing_sink", "transport_down"}
BLUETOOTH_ACTIONS = {
    "reconnect_device",
    "reconnect_devices",
    "pair_device",
    "toggle_bt_management",
    "toggle_bt_management_devices",
}


@pytest.fixture(autouse=True)
def _config_writable(monkeypatch):
    import sendspin_bridge.services.diagnostics.recovery_assistant as module

    monkeypatch.setattr(module, "collect_preflight_status", lambda: {"config_writable": {"status": "ok"}})


def _pulse(
    *, present: bool = True, daemon: bool = True, match_status: str | None = None, streaming: bool = False
) -> DeviceSnapshot:
    """A pulse_sink player as ``build_device_snapshot`` leaves it."""
    status = match_status or ("ok" if present else "sink_missing")
    device = DeviceSnapshot(
        connected=daemon,
        server_connected=daemon,
        bluetooth_connected=False,
        player_name="Desk DAC",
        player_id="dac-1",
        has_sink=present,
        sink_name=SINK if present else None,
        playing=streaming,
        extra={
            "audio_streaming": streaming,
            "backend_type": "pulse_sink",
            "audio_sink": SINK,
            "sink_missing": not present,
            "sink_match_status": status,
            "server_connected": daemon,
        },
    )
    return _finish(device)


def _bluetooth(*, connected: bool) -> DeviceSnapshot:
    device = DeviceSnapshot(
        connected=True,
        server_connected=connected,
        bluetooth_connected=connected,
        bluetooth_mac=MAC,
        player_name="Kitchen",
        has_sink=connected,
        sink_name="bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink" if connected else None,
        extra={"bluetooth_paired": True},
    )
    return _finish(device)


def _finish(device: DeviceSnapshot) -> DeviceSnapshot:
    device.transfer_readiness = _build_transfer_readiness(device=device, status=device.extra, recent_events=[])
    device.health_summary = compute_device_health_state(device).to_dict()
    device.state_model = build_normalized_device_state(device).to_dict()
    return device


def _recovery(devices):
    return build_recovery_assistant_snapshot(
        config={},
        devices=devices,
        onboarding_assistant={"checklist": {"overall_status": "ok"}},
        startup_progress={},
        local_sinks=[],
    )


def _guidance(devices):
    recovery = _recovery(devices).to_dict()
    snapshot = build_operator_guidance_snapshot(
        config={"players": [{"id": "x"}] * len(devices)},
        onboarding_assistant={"checklist": {"overall_status": "ok"}, "counts": {"configured_devices": len(devices)}},
        recovery_assistant=recovery,
        startup_progress={"status": "ready"},
        devices=devices,
    )
    return {group.key: group for group in snapshot.issue_groups}


def _actions(item) -> set[str]:
    primary = item.primary_action
    keys = {primary.key} if primary is not None else set()
    return keys | {action.key for action in item.secondary_actions}


# ── Health ───────────────────────────────────────────────────────────────


def test_a_present_output_is_ready_not_an_offline_bluetooth_speaker():
    health = compute_device_health_state(_pulse())

    assert health.state == "ready"
    assert "bluetooth_disconnected" not in health.reasons
    assert "Bluetooth" not in health.summary


def test_a_present_output_plays_as_streaming():
    assert compute_device_health_state(_pulse(streaming=True)).state == "streaming"


def test_a_missing_output_is_its_own_state():
    health = compute_device_health_state(_pulse(present=False, daemon=False))

    assert health.state == "offline"
    assert health.reasons[0] == "output_missing"
    assert health.summary == "Audio output missing"
    assert "bluetooth_disconnected" not in health.reasons


def test_a_disconnected_bluetooth_speaker_is_still_offline_for_bluetooth():
    health = compute_device_health_state(_bluetooth(connected=False))

    assert health.state == "offline"
    assert health.reasons[0] == "bluetooth_disconnected"
    assert health.summary == "Bluetooth speaker disconnected"


# ── Transfer readiness ───────────────────────────────────────────────────


def test_a_present_output_is_ready_for_transfer():
    readiness = _pulse().transfer_readiness

    assert readiness["ready"] is True
    assert readiness["reason"] == "ready"


def test_a_missing_output_is_not_ready_because_the_output_is_missing():
    readiness = _pulse(present=False, daemon=False).transfer_readiness

    assert readiness["ready"] is False
    assert readiness["reason"] == "output_missing"


def test_a_disconnected_bluetooth_speaker_is_not_ready_because_of_bluetooth():
    assert _bluetooth(connected=False).transfer_readiness["reason"] == "bluetooth_disconnected"


# ── Recovery issues ──────────────────────────────────────────────────────


def test_a_ready_output_raises_no_recovery_issue():
    assert _recovery([_pulse()]).issues == []


def test_a_missing_output_says_to_plug_it_in_or_pick_another_output():
    (issue,) = _recovery([_pulse(present=False, daemon=False)]).issues

    assert issue.key == "output_missing"
    assert issue.device_name == "Desk DAC"
    assert SINK in issue.summary
    assert "plug" in issue.summary.lower()
    assert "another output" in issue.summary
    assert not _actions(issue) & BLUETOOTH_ACTIONS
    assert issue.primary_action is not None and issue.primary_action.key == "open_devices_settings"


@pytest.mark.parametrize("match_status", ["sink_in_use", "sink_ambiguous"])
def test_an_output_that_cannot_be_told_apart_or_is_taken_is_reported_as_missing(match_status):
    (issue,) = _recovery([_pulse(present=False, daemon=False, match_status=match_status)]).issues

    assert issue.key == "output_missing"
    assert not _actions(issue) & BLUETOOTH_ACTIONS


def test_a_present_output_without_its_daemon_is_never_a_bluetooth_reconnect():
    issues = _recovery([_pulse(daemon=False)]).issues

    assert {issue.key for issue in issues}.isdisjoint(BLUETOOTH_ISSUES)
    for issue in issues:
        assert not _actions(issue) & BLUETOOTH_ACTIONS


def test_a_disconnected_bluetooth_speaker_still_gets_a_reconnect():
    (issue,) = _recovery([_bluetooth(connected=False)]).issues

    assert issue.key == "disconnected"
    assert issue.primary_action is not None and issue.primary_action.key == "reconnect_device"


# ── Guidance cards ───────────────────────────────────────────────────────


def test_a_ready_output_is_in_no_guidance_group():
    assert _guidance([_pulse()]) == {}


def test_a_missing_output_has_its_own_guidance_group_without_bluetooth_actions():
    groups = _guidance([_pulse(present=False, daemon=False)])

    assert set(groups) == {"output_missing"}
    group = groups["output_missing"]
    assert group.device_names == ["Desk DAC"]
    assert not _actions(group) & BLUETOOTH_ACTIONS


def test_a_present_output_without_its_daemon_is_in_no_bluetooth_group():
    groups = _guidance([_pulse(daemon=False)])

    assert set(groups).isdisjoint(BLUETOOTH_ISSUES | {"disconnected", "repair_needed", "never_paired"})
    for group in groups.values():
        assert not _actions(group) & BLUETOOTH_ACTIONS


def test_a_disconnected_bluetooth_speaker_next_to_a_ready_output_is_the_only_one_disconnected():
    groups = _guidance([_bluetooth(connected=False), _pulse()])

    assert set(groups) == {"disconnected"}
    assert groups["disconnected"].device_names == ["Kitchen"]


def test_the_header_counts_a_present_output_as_connected():
    standby = _bluetooth(connected=False)
    standby.extra["bt_standby"] = True  # parked on purpose: no issue card
    devices = [standby, _pulse()]
    snapshot = build_operator_guidance_snapshot(
        config={"players": [{"id": "a"}, {"id": "b"}]},
        onboarding_assistant={"checklist": {"overall_status": "ok"}, "counts": {"configured_devices": 2}},
        recovery_assistant={"summary": {}, "issues": []},
        startup_progress={"status": "ready"},
        devices=devices,
    )

    assert snapshot.header_status is not None
    assert snapshot.header_status.summary == "1 connected · 1 with sinks ready."
