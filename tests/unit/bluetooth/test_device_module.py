"""One speaker's life on the BlueZ D-Bus, behind one interface.

A device's D-Bus life used to be twelve free functions taking an object path,
called from fifty-one places, each of which had to know the path, the property
name and what an empty answer meant. This is that as a module: it resolves the
speaker through `ObjectManager` on a named controller, owns the bus and the
subscription, and answers named questions.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.device import BluetoothDevice
from tests.support.fake_dbus import FakeBlueZ

ADDRESS = DeviceAddress.require("FC:58:FA:EB:08:6C")
PATH = "/org/bluez/hci0/dev_FC_58_FA_EB_08_6C"


def _bluez(**device) -> FakeBlueZ:
    bluez = FakeBlueZ()
    bluez.add_device(PATH, ADDRESS.colons, **device)
    return bluez


def _device(bluez: FakeBlueZ, *, controller: str = "hci0") -> BluetoothDevice:
    return BluetoothDevice(ADDRESS, controller=controller, bus_factory=bluez.bus)


# ── finding the speaker ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_speaker_is_found_by_address_not_by_a_path_we_guessed():
    """A path built from a template is a path that is wrong after a re-pair."""
    bluez = FakeBlueZ()
    bluez.add_device("/org/bluez/hci0/dev_SOMETHING_ELSE", ADDRESS.colons, connected=True)

    device = _device(bluez)

    assert await device.is_connected() is True
    assert device.object_path == "/org/bluez/hci0/dev_SOMETHING_ELSE"


@pytest.mark.asyncio
async def test_only_the_named_controller_answers_for_the_speaker():
    """Paired on two controllers is two objects; the handle picks which."""
    bluez = FakeBlueZ()
    bluez.add_device("/org/bluez/hci0/dev_FC_58_FA_EB_08_6C", ADDRESS.colons, connected=False)
    bluez.add_device("/org/bluez/hci1/dev_FC_58_FA_EB_08_6C", ADDRESS.colons, connected=True)

    on_hci1 = _device(bluez, controller="hci1")

    assert await on_hci1.is_connected() is True
    assert on_hci1.object_path.startswith("/org/bluez/hci1/")


@pytest.mark.asyncio
async def test_a_speaker_bluez_does_not_know_answers_nothing():
    device = _device(FakeBlueZ())

    assert await device.is_connected() is False
    assert await device.is_paired() is None
    assert await device.uuids() == []
    assert device.object_path is None


# ── the named questions ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_it_answers_the_questions_callers_used_to_spell_out():
    bluez = _bluez(
        connected=True, paired=True, services_resolved=True, uuids=("0000110b-0000-1000-8000-00805f9b34fb",), battery=88
    )

    device = _device(bluez)

    assert await device.is_connected() is True
    assert await device.is_paired() is True
    assert await device.services_resolved() is True
    assert await device.uuids() == ["0000110b-0000-1000-8000-00805f9b34fb"]
    assert await device.battery_level() == 88


@pytest.mark.asyncio
async def test_a_speaker_without_a_battery_service_says_so():
    device = _device(_bluez(connected=True))

    assert await device.battery_level() is None


@pytest.mark.asyncio
async def test_the_transport_state_comes_from_the_speaker_s_own_transport():
    bluez = _bluez(connected=True)
    bluez.add_transport(f"{PATH}/fd0", "active")

    assert await _device(bluez).transport_state() == "active"


@pytest.mark.asyncio
async def test_a_media_endpoint_is_reported_when_an_audio_backend_registered_one():
    bluez = _bluez(connected=True)
    bluez.add_media_endpoint(f"{PATH}/sep1")

    assert await _device(bluez).has_media_endpoint() is True


@pytest.mark.asyncio
async def test_no_media_endpoint_is_a_no_not_a_shrug():
    """The distinction #314 diagnostics rest on."""
    assert await _device(_bluez(connected=True)).has_media_endpoint() is False


# ── one consistent picture ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_answers_everything_at_once():
    bluez = _bluez(connected=True, paired=True, services_resolved=True, battery=42)
    bluez.add_transport(f"{PATH}/fd0", "pending")

    state = await _device(bluez).state()

    assert state.connected is True
    assert state.paired is True
    assert state.services_resolved is True
    assert state.battery_level == 42
    assert state.transport_state == "pending"
    assert state.object_path == PATH


@pytest.mark.asyncio
async def test_state_of_an_unknown_speaker_is_all_unknown_not_an_error():
    state = await _device(FakeBlueZ()).state()

    assert state.connected is False
    assert state.paired is None
    assert state.object_path is None


# ── waiting ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_waiting_for_services_returns_as_soon_as_they_resolve():
    bluez = _bluez(connected=True, services_resolved=False)
    device = _device(bluez)
    await device.is_connected()  # resolve the object first

    async def _resolve_soon():
        await asyncio.sleep(0.02)
        bluez.set_property(PATH, "ServicesResolved", True)

    asyncio.ensure_future(_resolve_soon())

    assert await device.wait_for_services(timeout=1.0) is True


@pytest.mark.asyncio
async def test_waiting_for_services_gives_up_at_the_timeout():
    device = _device(_bluez(connected=True, services_resolved=False))

    assert await device.wait_for_services(timeout=0.05) is False


# ── the one operation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connecting_a_profile_reaches_the_speaker():
    bluez = _bluez(connected=True)

    assert await _device(bluez).connect_profile("0000110b-0000-1000-8000-00805f9b34fb") is True
    assert bluez.calls == [(PATH, "ConnectProfile", ("0000110b-0000-1000-8000-00805f9b34fb",))]


@pytest.mark.asyncio
async def test_a_refused_profile_reports_false_and_keeps_the_reason():
    """The one caller tells `AlreadyConnected` from a bluez#1922 suspicion."""
    bluez = _bluez(connected=True)
    bluez.fail["ConnectProfile"] = RuntimeError("org.bluez.Error.AlreadyConnected")
    device = _device(bluez)

    assert await device.connect_profile("0000110b-0000-1000-8000-00805f9b34fb") is False
    assert "AlreadyConnected" in (device.last_error or "")


@pytest.mark.asyncio
async def test_a_successful_operation_clears_the_previous_reason():
    bluez = _bluez(connected=True)
    bluez.fail["ConnectProfile"] = RuntimeError("nope")
    device = _device(bluez)
    await device.connect_profile("uuid")

    bluez.fail.clear()
    await device.connect_profile("uuid")

    assert device.last_error is None


# ── the verb that belongs to the device ──────────────────────────────────


@pytest.mark.asyncio
async def test_disconnecting_reaches_the_speaker():
    """`Disconnect` is a Device1 method: the speaker's own verb, not the controller's."""
    bluez = _bluez(connected=True)

    assert await _device(bluez).disconnect() is True
    assert bluez.calls == [(PATH, "Disconnect", ())]


@pytest.mark.asyncio
async def test_a_refused_disconnect_reports_false_and_keeps_the_reason():
    bluez = _bluez(connected=True)
    bluez.fail["Disconnect"] = RuntimeError("org.bluez.Error.NotConnected")
    device = _device(bluez)

    assert await device.disconnect() is False
    assert "NotConnected" in (device.last_error or "")


@pytest.mark.asyncio
async def test_disconnecting_a_speaker_bluez_does_not_know_is_false_not_an_error():
    """The caller falls back to the controller's own disconnect."""
    device = _device(FakeBlueZ())

    assert await device.disconnect() is False
