"""What A2DP is doing, as the audio fast-path gate reads it.

Backs the issue #269 fix: the cached-sink fast path consults the speaker's
`MediaTransport1.State` before skipping the A2DP settle delay, because taking
it while the peer already has the transport active races the anti-pop mute.

These used to reload the D-Bus module with a hand-built `dbus` stand-in; the
question they ask now goes to the speaker's device module, over the fake bus.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.device import BluetoothDevice
from tests.support.fake_dbus import FakeBlueZ

ADDRESS = DeviceAddress.require("AA:BB:CC:DD:EE:FF")
PATH = f"/org/bluez/hci0/{ADDRESS.dbus_node}"


def _device(bluez: FakeBlueZ) -> BluetoothDevice:
    return BluetoothDevice(ADDRESS, controller="hci0", bus_factory=bluez.bus)


def _bluez_with_device() -> FakeBlueZ:
    bluez = FakeBlueZ()
    bluez.add_device(PATH, ADDRESS.colons, connected=True)
    return bluez


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "idle", "pending"])
async def test_the_state_of_the_speaker_s_transport_is_reported(state):
    bluez = _bluez_with_device()
    bluez.add_transport(f"{PATH}/sep1/fd0", state, device=PATH)

    assert await _device(bluez).transport_state() == state


@pytest.mark.asyncio
async def test_a_speaker_with_no_transport_reports_nothing():
    assert await _device(_bluez_with_device()).transport_state() is None


@pytest.mark.asyncio
async def test_another_speaker_s_transport_is_not_ours():
    """The filter that keeps one speaker's A2DP state out of another's gate."""
    other = "/org/bluez/hci0/dev_11_22_33_44_55_66"
    bluez = _bluez_with_device()
    bluez.add_device(other, "11:22:33:44:55:66", connected=True)
    bluez.add_transport(f"{other}/sep1/fd0", "active", device=other)

    assert await _device(bluez).transport_state() is None


@pytest.mark.asyncio
async def test_a_bus_that_raises_reports_nothing_rather_than_failing():
    """The gate is a timing hint; it must not take the connect down with it."""
    bluez = _bluez_with_device()
    bluez.fail["GetManagedObjects"] = RuntimeError("boom")

    assert await _device(bluez).transport_state() is None


@pytest.mark.asyncio
async def test_a_speaker_bluez_does_not_know_reports_nothing():
    assert await _device(FakeBlueZ()).transport_state() is None
