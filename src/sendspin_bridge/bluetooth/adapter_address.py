"""The address of the controller BlueZ has at ``/org/bluez/hciN``.

The last synchronous dbus-python reader in the tree, and the only one the
device module does not cover: it asks about a *controller*, not a speaker.
Controllers are the next candidate — connect, disconnect, trust, power and
this — and it moves onto the async transport with them.

The per-thread private connection is the reason this file still exists at
all: ``dbus.SystemBus()`` hands out a process-wide connection that is only
safe once thread support is initialised, which this codebase never did,
while the callers arrive from three kinds of thread.
"""

from __future__ import annotations

import logging
import threading

try:
    import dbus
except ImportError:
    dbus = None  # type: ignore[assignment]


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
