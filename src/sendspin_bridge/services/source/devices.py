"""Paired Bluetooth devices that can act as the Sendspin source.

The source feature is for a phone/tablet/laptop that streams *into* the bridge,
not for the speakers the bridge plays to.  BlueZ keeps every bond, so the list
is built from the paired devices that advertise the A2DP Source role and are
not configured as bridge players.

One ``GetManagedObjects`` read feeds the whole list (the hardware page's normal
inventory call); a short cache keeps the bridge status read from asking D-Bus
on every status frame.  Pairing and forgetting invalidate it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

from sendspin_bridge.bluetooth.uuids import A2DP_SOURCE_UUID
from sendspin_bridge.services.source.state import SourceDevice

logger = logging.getLogger(__name__)

__all__ = ["devices_from_inventory", "invalidate", "list_source_devices"]

_CACHE_TTL_S = 20.0
_UUID_SHORT = A2DP_SOURCE_UUID[:8]
_cache: tuple[SourceDevice, ...] = ()
_cache_at = 0.0
_lock = threading.Lock()


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
    global _cache, _cache_at
    now = time.monotonic()
    with _lock:
        if _cache and now - _cache_at < _CACHE_TTL_S:
            return _cache
    try:
        from sendspin_bridge.bluetooth.controller import get_controller

        inventory = get_controller().dbus.inventory()
    except Exception:
        logger.debug("Source devices: BlueZ inventory failed", exc_info=True)
        return ()
    devices = devices_from_inventory(inventory, excluded_macs=excluded_macs)
    with _lock:
        _cache = devices
        _cache_at = now
    return devices


def list_source_devices(
    *,
    active_mac: str | None = None,
    excluded_macs: Iterable[str] = (),
) -> tuple[SourceDevice, ...]:
    """Paired source devices, with the currently captured one marked active."""
    active = str(active_mac or "").upper()
    return tuple(replace(device, active=device.mac == active) for device in _base_devices(excluded_macs))


def invalidate() -> None:
    """Drop the cache after a bond is added, removed or changes state."""
    global _cache, _cache_at
    with _lock:
        _cache = ()
        _cache_at = 0.0
