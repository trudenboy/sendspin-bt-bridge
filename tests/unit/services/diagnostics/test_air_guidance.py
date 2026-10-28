from types import SimpleNamespace

import pytest

from sendspin_bridge.services.diagnostics.guidance_issue_registry import issue_definition


def test_suspend_issue_is_registered_as_audio_warning():
    d = issue_definition("bt_sink_suspend_in_gaps")
    assert d is not None and d.layer == "audio" and d.severity == "warning"
    assert set(d.default_reason_codes) >= {"observed_suspend", "module_suspend_on_idle", "wireplumber_suspend_timeout"}
    assert d.priority == 34


def _device(**extra):
    # the recovery assistant reads device snapshots by attribute (see test_recovery_assistant.py)
    return SimpleNamespace(
        player_name="ENEBY20",
        bluetooth_connected=True,
        has_sink=True,
        server_connected=True,
        health_summary={},
        **extra,
    )


def test_recovery_assistant_reports_a_device_with_observed_suspends():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    dev = _device(host_sink_suspends={"observed": 2, "warn": True, "reason": "observed_suspend", "policy": None})
    assert "bt_sink_suspend_in_gaps" in [i.key for i in _build_device_issues([dev])]


def test_recovery_assistant_is_quiet_without_a_warning():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    dev = _device(host_sink_suspends={"observed": 0, "warn": False, "reason": None, "policy": None})
    assert "bt_sink_suspend_in_gaps" not in [i.key for i in _build_device_issues([dev])]
    assert "bt_sink_suspend_in_gaps" not in [i.key for i in _build_device_issues([_device()])]


def test_recovery_assistant_reads_the_normalized_audio_state():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    audio = {"has_sink": True, "host_sink_suspends": {"warn": True, "reason": "wireplumber_suspend_timeout"}}
    dev = SimpleNamespace(
        player_name="X",
        state_model={"bluetooth": {"connected": True}, "audio": audio},
        health_summary={},
    )
    assert "bt_sink_suspend_in_gaps" in [i.key for i in _build_device_issues([dev])]


@pytest.fixture
def client_factory():
    from sendspin_bridge.bridge.client import SendspinClient

    def make():
        return SendspinClient("Test Player", "localhost", 9000)

    return make


def test_client_marks_its_own_suspend_requests(client_factory):
    c = client_factory()
    assert c._suspend_requested_at is None  # created in __init__
    c._mark_suspend_requested(now=100.0)
    assert c._bridge_requested_suspend(now=101.5) is True
    assert c._bridge_requested_suspend(now=103.0) is False


def test_client_listener_publishes_host_suspends(client_factory):
    c = client_factory()
    c.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
    c._update_status({"bluetooth_connected": True})
    c._on_sink_state_change("AA:BB:CC:DD:EE:FF", "idle", "suspended", "bluez_sink.X")
    c._on_sink_state_change("11:22:33:44:55:66", "idle", "suspended", "bluez_sink.Y")  # not ours
    assert c.status["host_sink_suspends"]["observed"] == 1


def test_client_listener_ignores_its_own_power_save(client_factory):
    c = client_factory()
    c.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
    c._update_status({"bluetooth_connected": True, "bt_power_save": True})
    c._on_sink_state_change("AA:BB:CC:DD:EE:FF", "running", "suspended", "bluez_sink.X")
    assert (c.status.get("host_sink_suspends") or {}).get("observed", 0) == 0
