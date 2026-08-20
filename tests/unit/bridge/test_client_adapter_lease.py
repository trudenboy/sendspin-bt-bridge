"""Standby and startup must go through the adapter lease like everyone else.

Three paths in the client drove the controller without taking the lease and
without leaving the event loop: entering standby disconnected inline, waking
fired a fire-and-forget connect, and the startup connect read the link state
inline.  Each could run a full ``bluetoothctl`` ladder concurrently with a
UI-initiated scan — precisely the collision the lease exists to prevent — and
stalled every device's IPC and the status stream while it did.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from sendspin_bridge.bluetooth.adapter_session import AdapterHandle, LinkState
from sendspin_bridge.bridge.client import SendspinClient


class _FakeBtManager:
    """Records which controller work ran, and on which thread."""

    def __init__(self):
        self.adapter_handle = AdapterHandle()
        self.calls: list[tuple[str, str]] = []
        self.mac_address = "AA:BB:CC:DD:EE:FF"
        self.device_name = "TestSpeaker"
        self.connected = False

    def _record(self, name):
        self.calls.append((name, threading.current_thread().name))

    def disconnect_device(self):
        self._record("disconnect")
        return True

    def connect_device(self):
        self._record("connect")
        return True

    def link_state(self):
        self._record("link_state")
        return LinkState.DISCONNECTED

    def is_device_connected(self):
        self._record("is_device_connected")
        return False

    def allow_reconnect(self):
        return None

    def signal_standby_wake(self):
        return None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")

    cl = SendspinClient.__new__(SendspinClient)
    cl.player_name = "TestSpeaker"
    cl.player_id = "player-test"
    cl.bt_manager = _FakeBtManager()
    return cl


def _names(bt) -> list[str]:
    return [name for name, _thread in bt.calls]


def test_standby_disconnect_runs_off_the_event_loop_under_a_lease(client, monkeypatch):
    bt = client.bt_manager
    seen_holder: list[str | None] = []

    original = bt.disconnect_device

    def _spy():
        seen_holder.append(AdapterHandle.current_holder())
        return original()

    monkeypatch.setattr(bt, "disconnect_device", _spy)

    async def _run():
        loop_thread = threading.current_thread().name
        await client._disconnect_bt_for_standby()
        return loop_thread

    loop_thread = asyncio.run(_run())

    assert _names(bt) == ["disconnect"]
    assert bt.calls[0][1] != loop_thread, "the disconnect blocked the event loop"
    assert seen_holder and seen_holder[0] is not None, "the disconnect ran without an adapter lease"
    assert AdapterHandle.current_holder() is None, "the lease outlived the standby disconnect"


def test_standby_wake_connect_is_awaited_under_a_lease(client, monkeypatch):
    bt = client.bt_manager
    seen_holder: list[str | None] = []

    original = bt.connect_device

    def _spy():
        seen_holder.append(AdapterHandle.current_holder())
        return original()

    monkeypatch.setattr(bt, "connect_device", _spy)

    async def _run():
        loop_thread = threading.current_thread().name
        await client._connect_bt_for_wake()
        return loop_thread

    loop_thread = asyncio.run(_run())

    assert _names(bt) == ["connect"]
    assert bt.calls[0][1] != loop_thread
    assert seen_holder and seen_holder[0] is not None, "the wake connect ran without an adapter lease"
    assert AdapterHandle.current_holder() is None


def test_standby_paths_defer_while_the_adapter_is_leased(client):
    bt = client.bt_manager
    held = bt.adapter_handle.try_lease("scan")
    try:
        asyncio.run(client._disconnect_bt_for_standby())
    finally:
        held.release()

    assert _names(bt) == [], "standby drove the controller while a scan held it"


def test_release_does_not_raise_while_the_adapter_is_leased(client, monkeypatch):
    """Releasing a speaker must survive a busy controller, not blow up."""
    bt = client.bt_manager
    client.bt_management_enabled = True
    client.status = {}
    monkeypatch.setattr(client, "_update_status", lambda updates: client.status.update(updates))
    monkeypatch.setattr(client, "is_running", lambda: False)
    bt.cancel_reconnect = lambda: None
    client._daemon_proc = None

    held = bt.adapter_handle.try_lease("scan")
    try:
        client.set_bt_management_enabled(False)
    finally:
        held.release()

    assert _names(bt) == [], "release drove the controller while a scan held it"
    assert client.status["bt_management_enabled"] is False
