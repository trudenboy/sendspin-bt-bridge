"""A transport failure is not a disconnect.

``is_device_connected`` used to collapse "BlueZ timed out", "the transport is
gone" and "the D-Bus call raised" into ``False``, then apply that as a
confirmed disconnect: the speaker's MPRIS player was torn down, the reconnect
counter advanced and churn auto-disable moved closer — all because a
subprocess did not answer in five seconds.
"""

from __future__ import annotations

import contextlib

import pytest

from sendspin_bridge.bluetooth.adapter_session import LinkState
from sendspin_bridge.bluetooth.manager import BluetoothManager
from tests.support.fake_dbus import silent, unreachable

MAC = "AA:BB:CC:DD:EE:FF"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture()
def transitions():
    return {"connected": [], "disconnected": []}


@pytest.fixture()
def bt_manager(installed_bluez, transitions):
    installed_bluez.on("show", stdout="")
    return BluetoothManager(
        mac_address=MAC,
        device_name="TestSpeaker",
        on_connected=lambda: transitions["connected"].append(True),
        on_disconnected=lambda: transitions["disconnected"].append(True),
    )


@contextlib.contextmanager
def _no_dbus(manager):
    """BlueZ cannot resolve the device object — the fallback's condition."""
    silent(manager)
    yield


def test_timed_out_transport_leaves_the_link_state_unknown(bt_manager, installed_bluez):
    installed_bluez.timeout(f"info {MAC}")

    with _no_dbus(bt_manager):
        assert bt_manager.link_state() is LinkState.UNKNOWN


def test_timed_out_transport_does_not_fire_a_disconnect(bt_manager, installed_bluez, transitions):
    bt_manager.connected = True
    installed_bluez.timeout(f"info {MAC}")

    with _no_dbus(bt_manager):
        assert bt_manager.is_device_connected() is True

    assert bt_manager.connected is True
    assert transitions["disconnected"] == []


def test_unavailable_transport_does_not_fire_a_disconnect(bt_manager, installed_bluez, transitions):
    bt_manager.connected = True
    installed_bluez.fail(f"info {MAC}")

    with _no_dbus(bt_manager):
        assert bt_manager.is_device_connected() is True

    assert bt_manager.connected is True
    assert transitions["disconnected"] == []


def test_a_raising_dbus_probe_falls_back_to_the_transport(bt_manager, installed_bluez, transitions):
    bt_manager.connected = True
    installed_bluez.timeout(f"info {MAC}")

    unreachable(bt_manager)
    assert bt_manager.is_device_connected() is True

    assert bt_manager.connected is True
    assert transitions["disconnected"] == []


def test_a_real_disconnect_is_still_applied(bt_manager, installed_bluez, transitions):
    bt_manager.connected = True
    installed_bluez.on(
        f"info {MAC}",
        stdout=f"Device {MAC} (public)\n\tPaired: yes\n\tConnected: no\n",
    )

    with _no_dbus(bt_manager):
        assert bt_manager.is_device_connected() is False

    assert bt_manager.connected is False
    assert transitions["disconnected"] == [True]


def test_a_real_connect_is_still_applied(bt_manager, installed_bluez, transitions):
    installed_bluez.on(
        f"info {MAC}",
        stdout=f"Device {MAC} (public)\n\tPaired: yes\n\tConnected: yes\n",
    )

    with _no_dbus(bt_manager):
        assert bt_manager.is_device_connected() is True

    assert bt_manager.connected is True
    assert transitions["connected"] == [True]


def test_a_device_bluez_does_not_know_counts_as_disconnected(bt_manager, installed_bluez):
    bt_manager.connected = True

    with _no_dbus(bt_manager):
        assert bt_manager.is_device_connected() is False

    assert bt_manager.connected is False


def test_the_dbus_path_appears_once_the_controller_resolves(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address=MAC, adapter="C0:FB:F9:62:D7:D6", device_name="TestSpeaker")

    assert mgr.adapter_hci_name == ""
    assert mgr._dbus_device_path is None

    installed_bluez.on(
        "hciconfig",
        stdout="hci1:\tType: Primary  Bus: USB\n\tBD Address: C0:FB:F9:62:D7:D6  ACL MTU: 310:10\n",
    )

    assert mgr.adapter_hci_name == "hci1"
    assert mgr._dbus_device_path == f"/org/bluez/hci1/dev_{MAC.replace(':', '_')}"
