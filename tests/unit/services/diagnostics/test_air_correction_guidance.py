"""``air_correction_inactive``: a loop-mode speaker that runs without its air correction (stage B, B11)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sendspin_bridge.services.diagnostics.guidance_issue_registry import issue_definition


def _device(playout=None, air_sensor=None, **extra):
    return SimpleNamespace(
        player_name="ENEBY20",
        bluetooth_connected=True,
        has_sink=True,
        server_connected=True,
        health_summary={},
        playout=playout,
        air_sensor=air_sensor,
        **extra,
    )


def _loop(**kw):
    return {"mode": "loop", "warm_s": 120.0, "air_bias_ms": None, "air_bias_state": "none", **kw}


def _issues(devs):
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    return [i for i in _build_device_issues(devs) if i.key == "air_correction_inactive"]


def _reasons(dev):
    (issue,) = _issues([dev])
    return issue.reason_codes


def test_the_issue_is_registered_next_to_the_air_sensor_issue():
    d = issue_definition("air_correction_inactive")
    sensor = issue_definition("air_sensor_unavailable")
    assert d is not None and sensor is not None
    assert d.layer == "audio" and d.severity == "info"
    assert d.default_reason_codes == ("air_not_effective", "air_unavailable", "air_not_locked")
    assert abs(d.priority - sensor.priority) <= 2


def test_switch_off_raises_nothing():
    assert _issues([_device(_loop(), air_sensor=None)]) == []


def test_a_sensor_that_is_off_is_not_effective():
    assert _reasons(_device(_loop(), air_sensor={"state": "off", "reason": "not_bluetooth"})) == ["air_not_effective"]


@pytest.mark.parametrize("state", ["unavailable", "unsupported_codec", "unsupported_input"])
def test_an_unusable_sensor_is_unavailable(state):
    assert _reasons(_device(_loop(), air_sensor={"state": state})) == ["air_unavailable"]


def test_an_internal_error_of_the_bias_is_unavailable():
    dev = _device(_loop(air_bias_reason="internal_error"), air_sensor={"state": "locked"})
    assert _reasons(dev) == ["air_unavailable"]


def test_seeding_that_never_locks_is_not_locked():
    dev = _device(_loop(air_bias_state="seeding", warm_s=61), air_sensor={"state": "acquiring"})
    assert _reasons(dev) == ["air_not_locked"]


def test_learning_that_never_locks_is_not_locked():
    dev = _device(_loop(air_bias_reason="no_target", warm_s=61), air_sensor={"state": "no_lock"})
    assert _reasons(dev) == ["air_not_locked"]


def test_the_order_puts_off_before_unavailable_before_not_locked():
    dev = _device(_loop(air_bias_reason="internal_error"), air_sensor={"state": "acquiring"})
    assert _reasons(dev) == ["air_unavailable"]
    dev = _device(_loop(), air_sensor={"state": "off"})
    assert _reasons(dev) == ["air_not_effective"]


@pytest.mark.parametrize(
    ("playout", "air"),
    [
        (_loop(air_bias_state="seeding", warm_s=59), {"state": "acquiring"}),  # not warm long enough
        (_loop(air_bias_reason="no_target"), {"state": "locked"}),  # learning while locked
        (_loop(air_bias_reason="no_target"), {"state": "holding"}),
        (_loop(air_bias_state="seeding", air_bias_ms=12.0), {"state": "acquiring"}),  # provisional
        (_loop(air_bias_state="held", air_bias_ms=3.0), {"state": "no_lock"}),
        (_loop(air_bias_state="active", air_bias_ms=3.0), {"state": "locked"}),
        ({"mode": "measure", "warm_s": 120.0}, {"state": "acquiring"}),
        ({"mode": "measure", "warm_s": 120.0}, None),
        ({"mode": "off"}, None),
        (None, None),
    ],
)
def test_no_issue_when_the_correction_runs_or_is_not_expected(playout, air):
    assert _issues([_device(playout, air_sensor=air)]) == []


def test_a_released_speaker_raises_nothing():
    dev = _device(_loop(), air_sensor=None, bt_management_enabled=False)
    assert _issues([dev]) == []


def test_the_text_says_it_plays_as_in_measure_mode():
    (issue,) = _issues([_device(_loop(), air_sensor={"state": "off", "reason": "not_bluetooth"})])
    assert issue.severity == "info" and issue.device_name == "ENEBY20"
    assert "plays as in measure mode" in issue.summary


def test_the_normalized_state_carries_the_playout_to_the_producer():
    from sendspin_bridge.services.diagnostics.recovery_assistant import NormalizedRecoveryDevice
    from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state
    from sendspin_bridge.services.lifecycle.status_snapshot import DeviceSnapshot

    snap = DeviceSnapshot(
        player_name="ENEBY20",
        bluetooth_connected=True,
        has_sink=True,
        extra={"playout": _loop(air_bias_state="seeding", warm_s=90), "air_sensor": {"state": "acquiring"}},
    )
    state = build_normalized_device_state(snap)
    dev = NormalizedRecoveryDevice(
        player_name=state.player_name, state_model=state.to_dict(), health_summary={}, recent_events=[]
    )
    assert _reasons(dev) == ["air_not_locked"]
