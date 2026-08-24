"""Connection callbacks must arrive in the order the state changed.

The state write was serialised, but the callbacks fired outside the lock — so
two threads flipping the link could run them in the opposite order and leave
an MPRIS player registered for a speaker that had already disconnected, which
is the inverse of the duplicate-registration bug the lock was added to fix.
"""

from __future__ import annotations

import threading

import pytest

from sendspin_bridge.bluetooth.manager import BluetoothManager

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


def _manager(installed_bluez, events, *, on_connect_delay: float = 0.0):
    installed_bluez.on("show", stdout="")

    def _connected():
        if on_connect_delay:
            threading.Event().wait(on_connect_delay)
        events.append("connected")

    return BluetoothManager(
        mac_address=MAC,
        device_name="TestSpeaker",
        on_connected=_connected,
        on_disconnected=lambda: events.append("disconnected"),
    )


def test_transitions_fire_once_per_change(installed_bluez):
    events: list[str] = []
    mgr = _manager(installed_bluez, events)

    mgr.apply_connected_state(True)
    mgr.apply_connected_state(True)
    mgr.apply_connected_state(False)

    assert events == ["connected", "disconnected"]


def test_a_slow_connect_callback_cannot_land_after_a_later_disconnect(installed_bluez):
    """The bounce: a slow on_connected must not outlive the disconnect."""
    events: list[str] = []
    mgr = _manager(installed_bluez, events, on_connect_delay=0.15)

    connect_thread = threading.Thread(target=mgr.apply_connected_state, args=(True,))
    connect_thread.start()
    threading.Event().wait(0.03)  # let the connect callback start
    mgr.apply_connected_state(False)
    connect_thread.join(timeout=5)

    assert mgr.connected is False
    assert events[-1] != "connected", f"a connect callback landed after the disconnect: {events}"


def test_the_final_state_always_matches_the_last_callback(installed_bluez):
    events: list[str] = []
    mgr = _manager(installed_bluez, events, on_connect_delay=0.05)

    threads = [
        threading.Thread(target=mgr.apply_connected_state, args=(True,)),
        threading.Thread(target=mgr.apply_connected_state, args=(False,)),
        threading.Thread(target=mgr.apply_connected_state, args=(True,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    if events:
        expected = "connected" if mgr.connected else "disconnected"
        assert events[-1] == expected, f"final state {mgr.connected} disagrees with callbacks {events}"


def test_a_raising_callback_does_not_wedge_later_transitions(installed_bluez):
    installed_bluez.on("show", stdout="")
    events: list[str] = []

    def _boom():
        raise RuntimeError("callback exploded")

    mgr = BluetoothManager(
        mac_address=MAC,
        device_name="TestSpeaker",
        on_connected=_boom,
        on_disconnected=lambda: events.append("disconnected"),
    )

    mgr.apply_connected_state(True)
    mgr.apply_connected_state(False)

    assert events == ["disconnected"]
