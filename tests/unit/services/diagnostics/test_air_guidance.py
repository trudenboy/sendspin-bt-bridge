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


def test_pulse_sink_client_never_warns(client_factory):
    from sendspin_bridge.config.players import PULSE_SINK
    from sendspin_bridge.services.audio.sink_suspend_policy import SuspendPolicy

    c = client_factory()
    c.backend_type = PULSE_SINK
    c.set_suspend_policy(SuspendPolicy("pipewire", True, 5.0, "wireplumber"))
    assert not c.status.get("host_sink_suspends")


def test_bluetooth_client_warns_on_a_static_policy(client_factory):
    from sendspin_bridge.services.audio.sink_suspend_policy import SuspendPolicy

    c = client_factory()
    c.set_suspend_policy(SuspendPolicy("pipewire", True, 5.0, "wireplumber"))
    assert c.status["host_sink_suspends"]["warn"] is True


def test_attach_is_idempotent_and_hands_over_the_cached_policy(client_factory, monkeypatch):
    from sendspin_bridge.services.audio import sink_suspend_policy as m

    policy = m.SuspendPolicy("pulseaudio", True, 3.0, "module-suspend-on-idle")
    monkeypatch.setattr(m, "_last_policy", policy)

    class Monitor:
        def __init__(self):
            self.listeners = []

        def add_state_listener(self, fn):
            self.listeners.append(fn)

    mon = Monitor()
    c = client_factory()
    c._attach_suspend_listener(mon)
    c._attach_suspend_listener(mon)
    assert len(mon.listeners) == 1
    assert c.status["host_sink_suspends"]["reason"] == "module_suspend_on_idle"


@pytest.mark.asyncio
async def test_enter_power_save_mark_suppresses_an_observed_suspend(client_factory, monkeypatch):
    from sendspin_bridge.services.audio import pulse

    async def fake_suspend(sink, suspend):
        return True

    monkeypatch.setattr(pulse, "asuspend_sink", fake_suspend)
    c = client_factory()
    c.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
    c.bluetooth_sink_name = "bluez_sink.X"
    c._update_status({"bluetooth_connected": True})
    await c._enter_power_save()
    c._update_status({"bt_power_save": False})  # only the 2 s mark is left to tell it is ours
    c._on_sink_state_change("AA:BB:CC:DD:EE:FF", "running", "suspended", "bluez_sink.X")
    assert (c.status.get("host_sink_suspends") or {}).get("observed", 0) == 0


def test_recovery_assistant_skips_disconnected_or_released_devices():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_sink_suspend_issues

    facts = {"warn": True, "reason": "observed_suspend"}
    off = _device(host_sink_suspends=facts)
    off.bluetooth_connected = False
    released = _device(host_sink_suspends=facts, bt_management_enabled=False)
    assert _build_sink_suspend_issues([off, released]) == []


def test_real_normalized_state_carries_the_warning_to_the_producer():
    from sendspin_bridge.services.diagnostics.recovery_assistant import (
        NormalizedRecoveryDevice,
        _build_sink_suspend_issues,
    )
    from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state

    status = {
        "player_name": "ENEBY20",
        "bluetooth_connected": True,
        "has_sink": True,
        "host_sink_suspends": {"warn": True, "reason": "observed_suspend"},
    }
    state = build_normalized_device_state(status)
    dev = NormalizedRecoveryDevice(
        player_name=state.player_name, state_model=state.to_dict(), health_summary={}, recent_events=[]
    )
    assert [i.key for i in _build_sink_suspend_issues([dev])] == ["bt_sink_suspend_in_gaps"]
