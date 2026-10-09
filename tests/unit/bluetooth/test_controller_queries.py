"""The controller's questions, answered from BlueZ's object tree.

`bluetoothctl list`, `show`, `devices` and `info` each spawned a process and
parsed a transcript. With a dashboard tab open the host probe asked three of
them every few seconds. BlueZ already publishes every one of those answers in
`ObjectManager.GetManagedObjects`; these read it, and hand back the same value
types the transcript parsers do, so no caller can tell which way it was asked.

`None` means "the bus could not answer" — the one answer that sends a caller
to bluetoothctl instead. Everything else is an answer, including "no such
device".
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter
from sendspin_bridge.bluetooth.controller import DbusController
from tests.support.fake_dbus import FakeBlueZ

HCI0 = "/org/bluez/hci0"
HCI1 = "/org/bluez/hci1"
HCI0_MAC = "C0:FB:F9:62:D6:9D"
HCI1_MAC = "C0:FB:F9:62:D7:D6"
ENEBY = "6C:5C:3D:35:17:99"
LENCO = "30:21:0E:0A:AE:5A"
TV = "E4:7D:BD:B3:27:B6"


@pytest.fixture
def bluez() -> FakeBlueZ:
    fake = FakeBlueZ()
    fake.add_adapter(HCI0, HCI0_MAC, powered=True, Name="haos", Alias="haos")
    fake.add_adapter(HCI1, HCI1_MAC, powered=False, Name="haos #2", Alias="Kitchen radio")
    fake.add_device(
        f"{HCI1}/dev_6C_5C_3D_35_17_99",
        ENEBY,
        connected=True,
        paired=True,
        uuids=("0000110b-0000-1000-8000-00805f9b34fb", "0000110e-0000-1000-8000-00805f9b34fb"),
        Name="ENEBY Portable",
        Alias="ENEBY Portable",
        Class=0x240404,
        Icon="audio-headset",
        Bonded=True,
        Trusted=True,
        Blocked=False,
        RSSI=-48,
    )
    fake.add_device(f"{HCI0}/dev_30_21_0E_0A_AE_5A", LENCO, paired=True, Name="Lenco LS-500", Alias="Lenco")
    fake.add_device(f"{HCI0}/dev_E4_7D_BD_B3_27_B6", TV, paired=False, Name="[TV] Samsung", Alias="[TV] Samsung")
    return fake


@pytest.fixture
def controller(bluez) -> DbusController:
    return DbusController(bus_factory=bluez.bus)


def test_controllers_are_listed_in_kernel_order_with_the_default_first(controller):
    adapters = controller.list_adapters()

    assert [(a.mac, a.name, a.is_default) for a in adapters] == [
        (HCI0_MAC, "haos", True),
        (HCI1_MAC, "Kitchen radio", False),
    ]


def test_show_answers_for_the_controller_the_scope_names(controller):
    by_name = controller.show(Adapter.select("hci1"))
    by_address = controller.show(Adapter.addressed(HCI1_MAC))
    default = controller.show(Adapter.DEFAULT)

    assert (by_name.mac, by_name.alias, by_name.name, by_name.powered, by_name.present) == (
        HCI1_MAC,
        "Kitchen radio",
        "haos #2",
        False,
        True,
    )
    assert by_address == by_name
    assert default.mac == HCI0_MAC and default.powered is True


def test_show_of_an_unknown_controller_is_an_answer_not_an_outage(controller):
    info = controller.show(Adapter.select("hci7"))

    assert info is not None
    assert info.present is False


def test_show_with_no_controller_at_all_says_so(bluez, controller):
    bluez.remove(HCI0)
    bluez.remove(HCI1)

    assert controller.show(Adapter.DEFAULT).no_default is True


def test_devices_are_listed_per_controller_and_filtered_like_bluetoothctl(controller):
    paired_hci0 = controller.list_devices(Adapter.select("hci0"))
    every_hci0 = controller.list_devices(Adapter.select("hci0"), filter="")
    paired_hci1 = controller.list_devices(Adapter.select(HCI1_MAC))

    assert [(d.mac, d.name) for d in paired_hci0] == [(LENCO, "Lenco")]
    assert sorted(d.mac for d in every_hci0) == sorted([LENCO, TV])
    assert [(d.mac, d.name) for d in paired_hci1] == [(ENEBY, "ENEBY Portable")]


def test_device_info_carries_the_fields_and_the_lines_the_ui_shows(controller):
    from sendspin_bridge.services.bluetooth import classify_audio_capability

    info = controller.device_info(ENEBY, Adapter.select("hci1"))

    assert info.present is True
    assert (info.paired, info.bonded, info.trusted, info.blocked, info.connected) == (True, True, True, False, True)
    assert (info.name, info.alias, info.icon) == ("ENEBY Portable", "ENEBY Portable", "audio-headset")
    assert info.device_class == "0x00240404"
    assert info.rssi == -48
    assert info.raw[0] == f"Device {ENEBY} (public)"
    assert "UUID: 0000110b-0000-1000-8000-00805f9b34fb" in info.raw
    assert classify_audio_capability(info) == (True, "audio_class_of_device")


def test_device_info_for_a_device_the_controller_does_not_know(controller):
    info = controller.device_info(ENEBY, Adapter.select("hci0"))

    assert info.present is False
    assert info.not_available is True
    assert info.paired is None


def test_every_question_says_none_when_the_bus_cannot_answer(bluez, controller):
    bluez.connected = False

    assert controller.list_adapters() is None
    assert controller.show(Adapter.DEFAULT) is None
    assert controller.list_devices(Adapter.DEFAULT) is None
    assert controller.device_info(ENEBY, Adapter.DEFAULT) is None


# ---------------------------------------------------------------------------
# Discovery, without a bluetoothctl session
# ---------------------------------------------------------------------------

JBL = "AA:BB:CC:11:22:33"


def test_a_scan_runs_classic_discovery_on_the_controllers_asked_for(bluez, controller):
    bluez.add_in_range(HCI1, f"{HCI1}/dev_AA_BB_CC_11_22_33", JBL, Name="JBL Flip", Alias="JBL Flip", RSSI=-61)

    transcript = controller.scan([HCI1_MAC], window_s=0.05)

    calls = [(path, name) for path, name, _args in bluez.calls]
    assert (HCI1, "SetDiscoveryFilter") in calls
    assert (HCI1, "StartDiscovery") in calls
    assert (HCI1, "StopDiscovery") in calls
    assert (HCI0, "StartDiscovery") not in calls
    filters = [args[0] for path, name, args in bluez.calls if name == "SetDiscoveryFilter"]
    assert filters[0]["Transport"].value == "bredr"
    assert JBL in transcript.seen_macs
    assert transcript.names[JBL] == "JBL Flip"
    assert transcript.device_adapter[JBL] == HCI1_MAC
    assert transcript.rssi_by_mac[JBL] == -61


def test_a_controller_that_refuses_discovery_is_reported_not_silent(bluez, controller):
    bluez.fail["StartDiscovery"] = RuntimeError("org.bluez.Error.InProgress")

    transcript = controller.scan([HCI0_MAC], window_s=0.01)

    assert transcript.discovery_errors == ("org.bluez.Error.InProgress",)
