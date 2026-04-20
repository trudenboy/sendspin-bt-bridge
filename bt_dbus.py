"""D-Bus utility functions for BlueZ device interaction.

Thin wrappers around dbus-python for reading device properties, battery
levels, and calling Device1 methods (Connect/Disconnect).  The ``dbus``
module is imported once at module level; on systems where dbus-python is
unavailable the functions return safe defaults.
"""

from __future__ import annotations

import logging

try:
    import dbus
except ImportError:
    dbus = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _dbus_get_device_property(device_path: str | None, property_name: str, adapter_hci: str = "hci0"):
    """Read a single BlueZ Device1 property synchronously via dbus-python.

    Falls back to None on any error (D-Bus unavailable, device not registered, etc.).
    This is ~10× faster than spawning a bluetoothctl subprocess.
    """
    if not device_path or dbus is None:
        return None
    try:
        bus = dbus.SystemBus()
        device = bus.get_object("org.bluez", device_path)
        props = dbus.Interface(device, "org.freedesktop.DBus.Properties")
        return props.Get("org.bluez.Device1", property_name)
    except Exception as exc:
        logger.debug("D-Bus property read failed for %s: %s", property_name, exc)
        return None


def _dbus_get_battery_level(device_path: str | None) -> int | None:
    """Read battery percentage via org.bluez.Battery1, or None if unsupported."""
    if not device_path or dbus is None:
        return None
    try:
        bus = dbus.SystemBus()
        device = bus.get_object("org.bluez", device_path)
        props = dbus.Interface(device, "org.freedesktop.DBus.Properties")
        return int(props.Get("org.bluez.Battery1", "Percentage"))
    except Exception as exc:
        logger.debug("D-Bus battery read failed: %s", exc)
        return None


def _dbus_call_device_method(device_path: str | None, method_name: str) -> bool:
    """Call a BlueZ Device1 method synchronously via dbus-python.

    Returns True on success, False on error.
    """
    if not device_path or dbus is None:
        return False
    try:
        bus = dbus.SystemBus()
        device = bus.get_object("org.bluez", device_path)
        iface = dbus.Interface(device, "org.bluez.Device1")
        getattr(iface, method_name)()
        return True
    except Exception as e:
        logger.debug("D-Bus %s failed: %s", method_name, e)
        return False


# A2DP Sink service class (audio output into the peer). Stable across BlueZ.
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"


def _dbus_connect_profile(device_path: str | None, uuid: str) -> tuple[bool, str]:
    """Call BlueZ Device1.ConnectProfile(uuid) explicitly.

    Used as a workaround for bluez/bluez#1922 (5.86 dual-role regression) where
    the generic Connect() fails to register A2DP Sink. ConnectProfile targets a
    specific profile UUID, bypassing the broken auto-selection logic.

    Returns ``(True, "")`` on success or ``(False, reason)`` — reason is the
    D-Bus error name (e.g. ``org.bluez.Error.NotFound``) for caller diagnostics.
    """
    if not device_path or dbus is None:
        return False, "dbus unavailable"
    try:
        bus = dbus.SystemBus()
        device = bus.get_object("org.bluez", device_path)
        iface = dbus.Interface(device, "org.bluez.Device1")
        iface.ConnectProfile(uuid)
        return True, ""
    except Exception as exc:
        reason = getattr(exc, "get_dbus_name", lambda: "")() or str(exc) or exc.__class__.__name__
        logger.debug("D-Bus ConnectProfile(%s) failed: %s", uuid, reason)
        return False, reason
