"""Which transport runs a controller verb, and when the other one gets a turn.

BlueZ's own bus is the better answer to every one of these verbs, but it is
not always there: a container without the system bus mounted, a bridge that
started before bluetoothd, a kernel that hands out no adapter at all.  The
rule is narrow on purpose — the subprocess is tried when the bus could not
answer, and never when it answered "no".  Retrying a refusal through a
second transport only asks the same speaker the same question twice.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter, Outcome
from sendspin_bridge.bluetooth.controller import DbusController, PreferredController
from tests.support.fake_bluez import FakeBluez
from tests.support.fake_dbus import FakeBlueZ

HCI1 = "/org/bluez/hci1"
HCI1_MAC = "C0:FB:F9:62:D7:D6"
ENEBY = "6C:5C:3D:35:17:99"
ENEBY_PATH = f"{HCI1}/dev_6C_5C_3D_35_17_99"


@pytest.fixture
def bluetoothctl() -> FakeBluez:
    return FakeBluez()


def _controller(dbus_bluez: FakeBlueZ | None, bluetoothctl: FakeBluez) -> PreferredController:
    bus_factory = dbus_bluez.bus if dbus_bluez is not None else None
    return PreferredController(DbusController(bus_factory=bus_factory), lambda: bluetoothctl.control)


def test_the_bus_answers_and_no_subprocess_runs(bluetoothctl):
    dbus_bluez = FakeBlueZ()
    dbus_bluez.add_adapter(HCI1, HCI1_MAC, powered=True)
    dbus_bluez.add_device(ENEBY_PATH, ENEBY)

    result = _controller(dbus_bluez, bluetoothctl).connect(ENEBY, Adapter.select("hci1"))

    assert result.ok is True
    assert bluetoothctl.commands == []


def test_a_refusal_from_the_bus_is_the_answer_not_a_reason_to_try_again(bluetoothctl):
    dbus_bluez = FakeBlueZ()
    dbus_bluez.add_adapter(HCI1, HCI1_MAC, powered=True)
    dbus_bluez.add_device(ENEBY_PATH, ENEBY)
    dbus_bluez.fail["Connect"] = RuntimeError("org.bluez.Error.Failed: br-connection-page-timeout")

    result = _controller(dbus_bluez, bluetoothctl).connect(ENEBY, Adapter.select("hci1"))

    assert result.outcome is Outcome.FAILED
    assert "br-connection-page-timeout" in result.detail
    assert bluetoothctl.commands == []


def test_without_a_bus_the_subprocess_runs_the_verb(bluetoothctl):
    dbus_bluez = FakeBlueZ()
    dbus_bluez.connected = False

    result = _controller(dbus_bluez, bluetoothctl).connect(ENEBY, Adapter.select(HCI1_MAC))

    assert result.ok is True
    assert [c.verb for c in bluetoothctl.commands] == ["connect"]
    assert bluetoothctl.commands[-1].adapter_selected == HCI1_MAC


def test_every_verb_falls_through_to_the_subprocess(bluetoothctl):
    dbus_bluez = FakeBlueZ()
    dbus_bluez.connected = False
    controller = _controller(dbus_bluez, bluetoothctl)

    controller.disconnect(ENEBY, Adapter.select(HCI1_MAC))
    controller.trust(ENEBY, Adapter.select(HCI1_MAC))
    controller.remove(ENEBY, Adapter.select(HCI1_MAC))
    controller.power(True, Adapter.select(HCI1_MAC))

    assert [c.verb for c in bluetoothctl.commands] == ["disconnect", "trust", "remove", "power"]


def test_a_controller_the_bus_does_not_know_is_asked_of_the_subprocess(bluetoothctl):
    # A bridge that started before bluetoothd registered the second
    # controller must not be told its speaker's controller does not exist.
    dbus_bluez = FakeBlueZ()
    dbus_bluez.add_adapter("/org/bluez/hci0", "C0:FB:F9:62:D6:9D", powered=True)

    result = _controller(dbus_bluez, bluetoothctl).power(True, Adapter.select(HCI1_MAC))

    assert result.changed is True
    assert [c.verb for c in bluetoothctl.commands] == ["power"]
