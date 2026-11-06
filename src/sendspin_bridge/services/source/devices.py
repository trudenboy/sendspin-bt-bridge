"""Paired Bluetooth devices that can act as the Sendspin source.

The source feature is for a phone/tablet/laptop that streams *into* the bridge,
not for the speakers the bridge plays to.  BlueZ keeps every bond, so the list
is built from the paired devices that advertise the A2DP Source role and are
not configured as bridge players.

One ``GetManagedObjects`` read feeds the whole list (the hardware page's normal
inventory call), cached by the process-wide hardware reader. Pairing and
forgetting expire that reader while retaining the last known facts.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.uuids import A2DP_SOURCE_UUID
from sendspin_bridge.services.source.state import SourceDevice

__all__ = ["devices_from_inventory", "invalidate", "list_source_devices"]

_UUID_SHORT = A2DP_SOURCE_UUID[:8]


def _uuid_matches(raw: Iterable[str]) -> bool:
    for line in raw:
        if "uuid" not in line.lower():
            continue
        if _UUID_SHORT in line.lower() or A2DP_SOURCE_UUID in line.lower():
            return True
    return False


def _field(info: Any, name: str) -> str:
    fields = getattr(info, "fields", None) or {}
    value = fields.get(name)
    return str(value).strip() if value is not None else ""


def devices_from_inventory(
    inventory: Iterable[Any] | None,
    *,
    excluded_macs: Iterable[str] = (),
) -> tuple[SourceDevice, ...]:
    """The paired A2DP-source devices in *inventory*, without the player bonds."""
    excluded = {str(mac).upper() for mac in excluded_macs if mac}
    found: dict[str, SourceDevice] = {}
    for adapter in inventory or ():
        for info in getattr(adapter, "device_infos", ()) or ():
            mac = str(getattr(info, "mac", "") or "").upper()
            if not mac or mac in excluded:
                continue
            if not _uuid_matches(getattr(info, "raw", ()) or ()):
                continue
            name = _field(info, "alias") or _field(info, "name") or mac
            connected = _field(info, "connected").lower() in {"yes", "true", "1"}
            current = found.get(mac)
            if current is None or (connected and not current.connected):
                found[mac] = SourceDevice(mac=mac, name=name, connected=connected)
    return tuple(sorted(found.values(), key=lambda device: (device.name.lower(), device.mac)))


def _base_devices(excluded_macs: Iterable[str]) -> tuple[SourceDevice, ...]:
    from sendspin_bridge.services.hardware.inventory import HardwareUnavailable, read_inventory
    from sendspin_bridge.services.source.read_model import devices_from_read
    from sendspin_bridge.services.source.state import SourceState

    try:
        read = read_inventory()
    except HardwareUnavailable:
        return ()
    config = {"players": [{"backend": {"type": "bluetooth_a2dp", "mac": mac}} for mac in excluded_macs]}
    return devices_from_read(config, SourceState(), read)


def list_source_devices(
    *,
    active_mac: str | None = None,
    excluded_macs: Iterable[str] = (),
) -> tuple[SourceDevice, ...]:
    """Compatibility query through the one shared two-second inventory reader."""
    address = DeviceAddress.parse(active_mac)
    active = address.colons if address else None
    return tuple(replace(device, active=device.mac == active) for device in _base_devices(excluded_macs))


def invalidate() -> None:
    """Expire the shared inventory after a bond change, retaining its last good facts."""
    from sendspin_bridge.services.hardware.inventory import invalidate_cache

    invalidate_cache()
