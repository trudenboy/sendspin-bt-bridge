"""The hardware use cases: the read model plus the config it is joined with."""

from __future__ import annotations

import json

import pytest

from sendspin_bridge.application import hardware as hardware_uc
from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.services.hardware import inventory

ADAPTER = "C0:FB:F9:62:D7:D6"
DEVICE = "6C:5C:3D:35:17:99"
UNPAIRED = "AA:BB:CC:DD:EE:FF"

INFO = (
    f"Device {DEVICE} (public)\n"
    "\tName: ENEBY Portable\n"
    "\tAlias: ENEBY Portable\n"
    "\tPaired: yes\n"
    "\tBonded: yes\n"
    "\tTrusted: yes\n"
    "\tBlocked: no\n"
    "\tConnected: no\n"
    "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
    "\tModalias: bluetooth:v0059p0001d0001\n"
)


@pytest.fixture(autouse=True)
def _configured(monkeypatch, tmp_config):
    inventory.clear_cache()
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "PulseAudio")
    monkeypatch.setattr(inventory, "probe_capture", lambda: True)
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": "eneby",
                        "name": "ENEBY",
                        "backend": {"type": "bluetooth_a2dp", "mac": DEVICE, "adapter": "hci0"},
                    }
                ],
                "HA_ADAPTER_AREA_MAP": {ADAPTER: {"area_id": "living", "area_name": "Living room"}},
            }
        )
    )
    yield
    inventory.clear_cache()


async def test_snapshot_joins_player_id_and_adapter_area(installed_bluez):
    installed_bluez.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)

    snapshot = await hardware_uc.snapshot()

    controller = snapshot.controllers[0]
    assert controller.mac == ADAPTER
    assert controller.area is not None
    assert controller.area.id == "living"
    assert controller.area.name == "Living room"
    assert controller.device_count == 2

    by_mac = {device.mac: device for device in snapshot.bluetooth_devices}
    assert by_mac[DEVICE].player_id == "eneby"
    assert by_mac[DEVICE].battery_pct is None
    assert by_mac[DEVICE].rssi is None
    assert by_mac[DEVICE].source_phone is False
    assert by_mac[DEVICE].roles == []

    assert snapshot.capabilities.audio_server == "pulseaudio"
    assert snapshot.capabilities.dbus is False
    assert any(section.code == "dbus_unavailable" for section in snapshot.degraded)
    assert snapshot.stale is False
    assert snapshot.stale_after_s == 2


async def test_controller_detail_lists_devices_and_address_forms(installed_bluez):
    detail = await hardware_uc.controller("hci0")

    assert detail.id == "hci0"
    assert detail.id_synthetic is True
    assert {device.mac for device in detail.devices} == {DEVICE, "30:21:0E:0A:AE:5A"}
    assert detail.address_forms.colons == ADAPTER
    assert detail.address_forms.underscores == "C0_FB_F9_62_D7_D6"
    assert detail.address_forms.bare == "C0FBF962D7D6"


async def test_controller_detail_can_be_addressed_by_mac(installed_bluez):
    detail = await hardware_uc.controller(ADAPTER.lower())
    assert detail.id == "hci0"


async def test_unknown_controller_is_entity_not_found(installed_bluez):
    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.controller("hci9")

    assert exc.value.status == 404
    assert exc.value.code == "entity_not_found"


async def test_bluetooth_device_detail_has_uuids_manufacturer_and_bonds(installed_bluez):
    installed_bluez.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)

    detail = await hardware_uc.bluetooth_device(DEVICE.lower())

    assert detail.mac == DEVICE
    assert detail.player_id == "eneby"
    assert detail.uuids == ["0000110b-0000-1000-8000-00805f9b34fb"]
    assert detail.modalias == "bluetooth:v0059p0001d0001"
    assert detail.manufacturer == "Nordic Semiconductor"
    assert detail.bonds == [ADAPTER]
    assert detail.services_resolved is None


async def test_unknown_mac_is_entity_not_found(installed_bluez):
    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.bluetooth_device(UNPAIRED)

    assert exc.value.status == 404
    assert exc.value.code == "entity_not_found"


async def test_malformed_mac_is_entity_not_found(installed_bluez):
    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.bluetooth_device("not-a-mac")

    assert exc.value.status == 404
    assert exc.value.code == "entity_not_found"


async def test_a_host_that_cannot_answer_is_hardware_unavailable(installed_bluez):
    installed_bluez.fail("list")
    installed_bluez.fail("--version")

    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.snapshot()

    assert exc.value.status == 503
    assert exc.value.code == "hardware_unavailable"
