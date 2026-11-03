"""Paired Bluetooth devices that can feed the source."""

from __future__ import annotations

from dataclasses import dataclass

from sendspin_bridge.services.source import devices

PHONE = "34:C7:E9:25:A3:A3"
SPEAKER = "FC:58:FA:EB:08:6C"
A2DP_SOURCE = "0000110a-0000-1000-8000-00805f9b34fb"
A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"


@dataclass
class Info:
    mac: str
    fields: dict[str, str]
    raw: tuple[str, ...]


@dataclass
class Adapter:
    device_infos: tuple[Info, ...]


def test_only_paired_a2dp_source_devices_are_listed():
    inventory = [
        Adapter(
            (
                Info(PHONE, {"alias": "Pixel", "connected": "yes"}, (f"UUID: {A2DP_SOURCE}",)),
                Info(SPEAKER, {"alias": "ENEBY", "connected": "yes"}, (f"UUID: {A2DP_SINK}",)),
            )
        )
    ]

    found = devices.devices_from_inventory(inventory)

    assert [(device.mac, device.name, device.connected) for device in found] == [(PHONE, "Pixel", True)]


def test_player_bonds_are_not_source_devices():
    inventory = [Adapter((Info(SPEAKER, {"alias": "ENEBY"}, (f"UUID: {A2DP_SOURCE}",)),))]

    assert devices.devices_from_inventory(inventory, excluded_macs=[SPEAKER]) == ()


def test_a_device_with_both_roles_prefers_the_connected_entry():
    inventory = [
        Adapter((Info(PHONE, {"alias": "Pixel", "connected": "no"}, (f"UUID: {A2DP_SOURCE}",)),)),
        Adapter((Info(PHONE, {"alias": "Pixel", "connected": "yes"}, (f"UUID: {A2DP_SOURCE}",)),)),
    ]

    found = devices.devices_from_inventory(inventory)

    assert len(found) == 1
    assert found[0].connected is True


def test_active_flag_is_added_without_refetching(monkeypatch):
    monkeypatch.setattr(devices, "_base_devices", lambda excluded: (devices.SourceDevice(PHONE, "Pixel", True),))

    found = devices.list_source_devices(active_mac=PHONE.lower())

    assert found[0].active is True
