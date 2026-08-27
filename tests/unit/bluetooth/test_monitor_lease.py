"""The monitor drives the controller through a named lease.

The reconnect ladder used to take a bare module-level lock, so nothing could
say *who* was holding the adapter when a scan was refused, and the release
lived in a `finally` that had no way to tell its own lock from somebody
else's.  It now takes a lease from the manager's adapter handle.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bluetooth import monitor as monitor_mod
from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
from sendspin_bridge.bluetooth.manager import BluetoothManager

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture()
def bt_manager(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address=MAC, device_name="TestSpeaker")
    mgr._running = True
    mgr.management_enabled = True
    return mgr


def test_manager_exposes_its_adapter_handle(bt_manager):
    assert isinstance(bt_manager.adapter_handle, AdapterHandle)


def test_reconnect_lease_names_its_holder(bt_manager):
    lease = bt_manager.adapter_handle.try_lease("reconnect TestSpeaker")
    try:
        assert AdapterHandle.current_holder() == "reconnect TestSpeaker"
    finally:
        lease.release()


def test_monitor_releases_the_lease_when_the_connect_ladder_raises(bt_manager, monkeypatch):
    """A failing reconnect must not strand the adapter."""

    class _Host:
        def get_status_value(self, key):
            return False

        def update_status(self, updates):
            return None

        def is_subprocess_running(self):
            return False

        async def stop_subprocess(self):
            return None

        async def start_subprocess(self):
            return None

        async def send_subprocess_command(self, cmd):
            return None

    bt_manager.host = _Host()
    monkeypatch.setattr(bt_manager, "is_device_connected", lambda: False)
    monkeypatch.setattr(bt_manager, "is_device_paired", lambda: True)
    monkeypatch.setattr(bt_manager, "_handle_reconnect_failure", lambda attempt: False)

    def _boom():
        raise RuntimeError("adapter wedged")

    monkeypatch.setattr(bt_manager, "connect_device", _boom)

    async def _one_pass():
        # The loop logs and keeps going, so stop it after the first poll.
        task = asyncio.ensure_future(monitor_mod._monitor_dbus(bt_manager))
        await asyncio.sleep(0.2)
        bt_manager._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_one_pass())

    assert AdapterHandle.current_holder() is None
    lease = bt_manager.adapter_handle.try_lease("after")
    assert lease is not None
    lease.release()
