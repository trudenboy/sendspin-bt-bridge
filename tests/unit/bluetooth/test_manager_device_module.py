"""The manager reads its speaker through the device module.

Twenty-seven of the manager's reads went into free functions taking an object
path it built itself from the pinned controller. It now holds one module for
its speaker, built from the address and the controller its adapter handle
resolved, and reads through that.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.address import DeviceAddress
from tests.support.fake_dbus import FakeBlueZ

ADDRESS = "FC:58:FA:EB:08:6C"
PATH = "/org/bluez/hci0/dev_FC_58_FA_EB_08_6C"


@pytest.fixture
def manager(monkeypatch):
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    mgr = BluetoothManager(ADDRESS, "hci0", "Kitchen")
    monkeypatch.setattr(type(mgr), "adapter_hci_name", property(lambda _self: "hci0"))
    return mgr


def _bluez(**device) -> FakeBlueZ:
    bluez = FakeBlueZ()
    bluez.add_device(PATH, ADDRESS, **device)
    return bluez


def _wire(manager, bluez: FakeBlueZ) -> None:
    from sendspin_bridge.bluetooth.device import BluetoothDevice

    manager.device = BluetoothDevice(DeviceAddress.require(ADDRESS), controller="hci0", bus_factory=bluez.bus)


def test_the_manager_holds_one_module_for_its_speaker(manager):
    device = manager.device

    assert device is manager.device, "a new module per read would mean a new bus per read"
    assert device.address == DeviceAddress.require(ADDRESS)


def test_the_module_is_built_for_the_controller_the_handle_resolved(manager):
    assert manager.device.controller == "hci0"


@pytest.mark.asyncio
async def test_the_paired_check_reads_through_the_module(manager):
    _wire(manager, _bluez(paired=True))

    assert await manager.device.is_paired() is True


@pytest.mark.asyncio
async def test_a_speaker_bluez_cannot_resolve_is_unknown_not_unpaired(manager):
    """The distinction the reconnect path rests on."""
    _wire(manager, FakeBlueZ())

    assert await manager.device.is_paired() is None
