"""Regression tests for default Bluetooth controller identity."""

from types import SimpleNamespace
from unittest.mock import Mock

from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
from sendspin_bridge.bluetooth.bluez import Outcome

DEFAULT_MAC = "88:A2:9E:A9:64:25"
OTHER_MAC = "AA:BB:CC:DD:EE:FF"
DEVICE_MAC = "E8:07:BF:E3:A4:B4"


def _transport():
    bluez = Mock()
    bluez.show.return_value = SimpleNamespace(
        outcome=Outcome.OK,
        present=True,
        mac=DEFAULT_MAC,
    )
    bluez.hci_map.return_value = {
        "AABBCCDDEEFF": "hci0",
        "88A29EA96425": "hci1",
    }
    return bluez


def test_default_adapter_resolves_actual_controller():
    bluez = _transport()
    handle = AdapterHandle(adapter="", bluez=bluez)

    assert handle.hci_name == "hci1"

    assert handle.dbus_device_path(DEVICE_MAC) == ("/org/bluez/hci1/dev_E8_07_BF_E3_A4_B4")


def test_default_adapter_keeps_default_command_scope():
    handle = AdapterHandle(adapter="", bluez=_transport())

    assert handle.hci_name == "hci1"
    assert handle.adapter == ""
    assert handle.adapter_mac == ""
    assert handle.scope.is_default


def test_default_adapter_does_not_guess_missing_mapping():
    bluez = _transport()
    bluez.hci_map.return_value = {
        "AABBCCDDEEFF": "hci0",
    }

    handle = AdapterHandle(adapter="", bluez=bluez)

    assert handle.hci_name == ""
    assert handle.dbus_device_path(DEVICE_MAC) is None


def test_default_adapter_retries_when_controller_appears():
    bluez = _transport()
    bluez.hci_map.return_value = {}

    handle = AdapterHandle(adapter="", bluez=bluez)

    assert handle.hci_name == ""

    bluez.hci_map.return_value = {
        "88A29EA96425": "hci0",
    }

    assert handle.hci_name == "hci0"


def test_default_adapter_handles_unavailable_controller():
    bluez = _transport()
    bluez.show.return_value.present = False

    handle = AdapterHandle(adapter="", bluez=bluez)

    assert handle.hci_name == ""


def test_explicit_adapter_does_not_query_default():
    bluez = _transport()
    handle = AdapterHandle(adapter=OTHER_MAC, bluez=bluez)

    assert handle.hci_name == "hci0"
    bluez.show.assert_not_called()


def test_explicit_hci_name_remains_unchanged():
    bluez = _transport()
    handle = AdapterHandle(adapter="hci2", bluez=bluez)

    assert handle.hci_name == "hci2"
    bluez.show.assert_not_called()


def test_default_adapter_follows_controller_changes():
    bluez = _transport()
    handle = AdapterHandle(adapter="", bluez=bluez)

    assert handle.hci_name == "hci1"

    bluez.show.return_value.mac = OTHER_MAC

    assert handle.hci_name == "hci0"
    assert handle.dbus_device_path(DEVICE_MAC) == ("/org/bluez/hci0/dev_E8_07_BF_E3_A4_B4")


def test_default_adapter_does_not_keep_stale_mapping():
    bluez = _transport()
    handle = AdapterHandle(adapter="", bluez=bluez)

    assert handle.hci_name == "hci1"

    bluez.show.return_value.present = False

    assert handle.hci_name == ""
    assert handle.dbus_device_path(DEVICE_MAC) is None
