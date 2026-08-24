"""D-Bus utility functions for BlueZ device interaction.

Thin wrappers around dbus-python for reading device properties, battery
levels, and calling Device1 methods (Connect/Disconnect).  The ``dbus``
module is imported once at module level; on systems where dbus-python is
unavailable the functions return safe defaults.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

try:
    import dbus
except ImportError:
    dbus = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# dbus-python's ``SystemBus()`` hands out one process-wide connection, and
# concurrent blocking calls on it are only safe once dbus-python's thread
# support has been initialised — which this process never does.  The helpers
# below are called from the Bluetooth executor, the event loop and Flask
# worker threads, so each thread gets its own private connection instead:
# no global initialisation, and no two threads sharing a socket.
_thread_state = threading.local()


def _bus():
    """Return this thread's private system-bus connection, or ``None``."""
    if dbus is None:
        return None
    bus = getattr(_thread_state, "bus", None)
    if bus is not None:
        return bus
    try:
        bus = dbus.SystemBus(private=True)
    except Exception as exc:
        logger.debug("Private system bus unavailable: %s", exc)
        return None
    _thread_state.bus = bus
    return bus


def _reset_thread_buses() -> None:
    """Drop this thread's cached connection (tests, and after a bus error)."""
    bus = getattr(_thread_state, "bus", None)
    if bus is not None:
        try:
            bus.close()
        except Exception as exc:
            logger.debug("Closing the thread's system bus failed: %s", exc)
    _thread_state.bus = None


def _dbus_get_device_property(device_path: str | None, property_name: str):
    """Read a single BlueZ Device1 property synchronously via dbus-python.

    Falls back to None on any error (D-Bus unavailable, device not registered, etc.).
    This is ~10× faster than spawning a bluetoothctl subprocess.
    """
    if not device_path or dbus is None:
        return None
    try:
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
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
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
        device = bus.get_object("org.bluez", device_path)
        props = dbus.Interface(device, "org.freedesktop.DBus.Properties")
        return int(props.Get("org.bluez.Battery1", "Percentage"))
    except Exception as exc:
        logger.debug("D-Bus battery read failed: %s", exc)
        return None


def _dbus_get_adapter_address(hci_name: str) -> str | None:
    """Read org.bluez.Adapter1.Address for a given hci name (e.g. "hci0").

    The BlueZ object path for an adapter is always ``/org/bluez/<hci_name>``,
    which matches the kernel hci index exactly — unlike ``bluetoothctl list``
    output order, which reflects BlueZ's registration order and is not
    guaranteed to match ascending hci-index order (e.g. after hot-plugging a
    second adapter). This is the unambiguous way to resolve hciN -> MAC.
    """
    if not hci_name or dbus is None:
        return None
    try:
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
        adapter = bus.get_object("org.bluez", f"/org/bluez/{hci_name}")
        props = dbus.Interface(adapter, "org.freedesktop.DBus.Properties")
        return str(props.Get("org.bluez.Adapter1", "Address"))
    except Exception as exc:
        logger.debug("D-Bus adapter address read failed for %s: %s", hci_name, exc)
        return None


def _dbus_call_device_method(device_path: str | None, method_name: str) -> bool:
    """Call a BlueZ Device1 method synchronously via dbus-python.

    Returns True on success, False on error.
    """
    if not device_path or dbus is None:
        return False
    try:
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
        device = bus.get_object("org.bluez", device_path)
        iface = dbus.Interface(device, "org.bluez.Device1")
        getattr(iface, method_name)()
        return True
    except Exception as e:
        logger.debug("D-Bus %s failed: %s", method_name, e)
        return False


# A2DP Sink service class (audio output into the peer). Stable across BlueZ.
A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
HANDSFREE_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HEADSET_UUID = "00001108-0000-1000-8000-00805f9b34fb"

# Any one of these advertised by the peer is enough to treat it as an audio
# device worth connecting. Used for the post-pair audio-profile sanity check.
AUDIO_SINK_UUIDS = frozenset({A2DP_SINK_UUID, HANDSFREE_UUID, HEADSET_UUID})


def _dbus_get_device_uuids(device_path: str | None) -> list[str]:
    """Return Device1.UUIDs as a list of lowercase strings, or [] on error."""
    raw = _dbus_get_device_property(device_path, "UUIDs")
    if raw is None:
        return []
    try:
        return [str(u).lower() for u in raw]
    except TypeError:
        logger.debug("Device1.UUIDs had unexpected shape: %r", raw)
        return []


def _dbus_wait_services_resolved(
    device_path: str | None,
    *,
    is_connected_check: Callable[[], bool],
    wait_with_cancel: Callable[[float], bool],
    timeout: float = 10.0,
    poll_interval: float = 0.5,
) -> bool | None:
    """Poll ``Device1.ServicesResolved`` until True or timeout.

    Bails out early if ``is_connected_check`` reports the device has
    disconnected. ``wait_with_cancel(sec)`` must follow the bridge-wide
    convention: return ``True`` after waiting ``sec`` seconds uninterrupted,
    ``False`` if the caller requested cancellation during the wait (e.g.
    a pending release/stop). This matches ``BluetoothManager._wait_with_cancel``.

    Tri-state return:
    - ``True`` — ServicesResolved reached True within the timeout.
    - ``False`` — we polled and either timed out, got cancelled, or the
      device disconnected. Caller may log a "did not reach True" warning.
    - ``None`` — we could not check at all (dbus-python not installed, or
      no ``device_path``). Callers MUST treat this as "proceed silently"
      and skip the misleading-timeout warning.
    """
    if not device_path or dbus is None:
        return None
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        resolved = _dbus_get_device_property(device_path, "ServicesResolved")
        if bool(resolved):
            return True
        if not is_connected_check():
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        wait_for = min(poll_interval, remaining)
        if not wait_with_cancel(wait_for):
            return False


def _dbus_get_media_transport_state(device_path: str | None) -> str | None:
    """Return ``MediaTransport1.State`` for *device_path* or None.

    Enumerates BlueZ ObjectManager and finds the first
    ``org.bluez.MediaTransport1`` whose ``Device`` property matches
    *device_path*. State values per BlueZ docs:
    ``"idle" | "pending" | "active" | "suspending"``.

    Returns ``None`` when dbus-python is unavailable, no transport
    object exists for the device yet (peer has not negotiated a stream
    endpoint — common right after Connect), or the lookup raised a
    D-Bus error. Callers MUST treat ``None`` as "transport state
    unknown — proceed without the gate".
    """
    if not device_path or dbus is None:
        return None
    try:
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
        root = bus.get_object("org.bluez", "/")
        om = dbus.Interface(root, "org.freedesktop.DBus.ObjectManager")
        managed = om.GetManagedObjects()
        for _path, ifaces in managed.items():
            transport = ifaces.get("org.bluez.MediaTransport1")
            if not transport:
                continue
            if str(transport.get("Device", "")) != device_path:
                continue
            state = transport.get("State")
            return str(state) if state is not None else None
        return None
    except Exception as exc:
        logger.debug("D-Bus MediaTransport1.State read failed: %s", exc)
        return None


def _dbus_has_media_endpoint(device_path: str | None) -> bool | None:
    """Return whether BlueZ has negotiated a local ``MediaEndpoint1`` for *device_path*.

    BlueZ creates ``<device_path>/sepN`` child objects implementing
    ``org.bluez.MediaEndpoint1`` once it matches the peer's advertised AVDTP
    stream endpoints against a *locally registered* media endpoint — normally
    registered by PipeWire/WirePlumber's bluez5 SPA plugin or PulseAudio's
    module-bluetooth-discover, via ``Media1.RegisterEndpoint``. If no audio
    backend on the host has ever registered one, BlueZ has nothing to match
    against and these objects never appear for that device, regardless of
    which audio server or WirePlumber version is involved. This makes it a
    much more direct, audio-server-agnostic signal than probing specific
    config files for known headless-PipeWire footguns.

    Returns ``True``/``False`` once BlueZ has had a chance to negotiate
    (i.e. after ``Device1.ServicesResolved`` is true), ``None`` when
    dbus-python is unavailable or the lookup fails — callers MUST treat
    ``None`` as "unknown", not "no endpoint".
    """
    if not device_path or dbus is None:
        return None
    try:
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
        root = bus.get_object("org.bluez", "/")
        om = dbus.Interface(root, "org.freedesktop.DBus.ObjectManager")
        managed = om.GetManagedObjects()

        device_ifaces = next((ifaces for path, ifaces in managed.items() if str(path) == device_path), None)
        if not device_ifaces:
            return None
        device_props = device_ifaces.get("org.bluez.Device1")
        if not device_props or not bool(device_props.get("ServicesResolved", False)):
            return None

        for path, ifaces in managed.items():
            parent, _, child = str(path).rpartition("/")
            if parent == device_path and child.startswith("sep") and "org.bluez.MediaEndpoint1" in ifaces:
                return True
        return False
    except Exception as exc:
        logger.debug("D-Bus MediaEndpoint1 lookup failed: %s", exc)
        return None


def _dbus_get_media_transport_snapshot(device_path: str | None):
    """Return the best MediaTransport snapshot for *device_path*.

    The helper deliberately returns an empty typed snapshot on failure.  A
    missing ``Delay`` is a supported and common capability outcome, not an
    operational error.
    """
    from sendspin_bridge.services.bluetooth.transport_telemetry import (
        BluetoothTransportSnapshot,
        select_transport_snapshot,
    )

    if not device_path or dbus is None:
        return BluetoothTransportSnapshot()
    try:
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
        root = bus.get_object("org.bluez", "/")
        om = dbus.Interface(root, "org.freedesktop.DBus.ObjectManager")
        return select_transport_snapshot(om.GetManagedObjects(), device_path)
    except Exception as exc:
        logger.debug("D-Bus MediaTransport1 telemetry read failed: %s", exc)
        return BluetoothTransportSnapshot()


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
        bus = _bus()
        if bus is None:
            raise RuntimeError("system bus unavailable")
        device = bus.get_object("org.bluez", device_path)
        iface = dbus.Interface(device, "org.bluez.Device1")
        iface.ConnectProfile(uuid)
        return True, ""
    except Exception as exc:
        reason = getattr(exc, "get_dbus_name", lambda: "")() or str(exc) or exc.__class__.__name__
        logger.debug("D-Bus ConnectProfile(%s) failed: %s", uuid, reason)
        return False, reason
