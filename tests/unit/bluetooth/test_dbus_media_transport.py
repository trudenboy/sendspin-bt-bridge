"""Tests for MediaTransport1.State lookup used by the audio fast-path gate.

Backs the issue #269 fix where the LAST_SINKS fast-path now consults
``org.bluez.MediaTransport1.State`` before skipping the A2DP delay.
"""

from __future__ import annotations

import importlib
import sys
import types


def _install_fake_dbus(monkeypatch, managed_objects):
    """Install a minimal fake ``dbus`` module exposing a ManagedObjects payload."""
    fake_dbus = types.ModuleType("dbus")

    class _Interface:
        def __init__(self, obj, iface):
            self._obj = obj
            self._iface = iface

        def GetManagedObjects(self):
            return managed_objects

    class _Object:
        def __init__(self, path):
            self._path = path

    class _Bus:
        def get_object(self, _service, path):
            return _Object(path)

    fake_dbus.SystemBus = lambda: _Bus()
    fake_dbus.Interface = _Interface
    monkeypatch.setitem(sys.modules, "dbus", fake_dbus)


def _reload_bt_dbus():
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    importlib.reload(bt_dbus)
    return bt_dbus


def test_media_transport_state_active(monkeypatch):
    """Returns 'active' when a MediaTransport1 for the device is active."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    managed = {
        f"{device_path}/sep1/fd0": {
            "org.bluez.MediaTransport1": {
                "Device": device_path,
                "State": "active",
            }
        }
    }
    _install_fake_dbus(monkeypatch, managed)
    bt_dbus = _reload_bt_dbus()

    assert bt_dbus._dbus_get_media_transport_state(device_path) == "active"


def test_media_transport_state_idle(monkeypatch):
    """Returns 'idle' when a MediaTransport1 for the device is idle."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    managed = {
        f"{device_path}/sep1/fd0": {
            "org.bluez.MediaTransport1": {
                "Device": device_path,
                "State": "idle",
            }
        }
    }
    _install_fake_dbus(monkeypatch, managed)
    bt_dbus = _reload_bt_dbus()

    assert bt_dbus._dbus_get_media_transport_state(device_path) == "idle"


def test_media_transport_state_no_transport(monkeypatch):
    """Returns None when no MediaTransport1 exists for the device."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    _install_fake_dbus(monkeypatch, {})
    bt_dbus = _reload_bt_dbus()

    assert bt_dbus._dbus_get_media_transport_state(device_path) is None


def test_media_transport_state_filters_by_device(monkeypatch):
    """Ignores MediaTransport1 objects that belong to a different device."""
    our_device = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    other_device = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    managed = {f"{other_device}/sep1/fd0": {"org.bluez.MediaTransport1": {"Device": other_device, "State": "active"}}}
    _install_fake_dbus(monkeypatch, managed)
    bt_dbus = _reload_bt_dbus()

    assert bt_dbus._dbus_get_media_transport_state(our_device) is None


def test_media_transport_state_returns_none_on_dbus_error(monkeypatch):
    """When GetManagedObjects raises, the helper degrades to None."""
    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    fake_dbus = types.ModuleType("dbus")

    class _Interface:
        def __init__(self, *_args, **_kwargs):
            pass

        def GetManagedObjects(self):
            raise RuntimeError("boom")

    class _Bus:
        def get_object(self, *_args, **_kwargs):
            return object()

    fake_dbus.SystemBus = lambda: _Bus()
    fake_dbus.Interface = _Interface
    monkeypatch.setitem(sys.modules, "dbus", fake_dbus)
    bt_dbus = _reload_bt_dbus()

    assert bt_dbus._dbus_get_media_transport_state(device_path) is None


def test_media_transport_state_handles_missing_path():
    """Returns None gracefully when device_path is None or empty."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    assert bt_dbus._dbus_get_media_transport_state(None) is None
    assert bt_dbus._dbus_get_media_transport_state("") is None
