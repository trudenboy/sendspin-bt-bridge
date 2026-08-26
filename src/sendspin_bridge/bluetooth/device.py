"""One speaker's life on the BlueZ D-Bus.

A device's D-Bus life used to be twelve free functions taking an object path:
read a property, call a method, wait for services, ask about a transport.
Fifty-one call sites used them, twenty-seven from the Bluetooth manager alone,
and every one had to know the path — which it built itself from a controller
name and an address — the property name, and what an empty answer meant.

This is that as a module.  It finds the speaker through ``ObjectManager``
rather than guessing a path, on the controller its adapter handle named; it
owns the bus connection and the ``PropertiesChanged`` subscription; and it
answers named questions, so no caller needs to know what BlueZ calls things.

What it does not do is pair, connect, trust or remove a speaker.  Those are
the controller's verbs and they live behind the bluetoothctl transport.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.bluetooth.address import DeviceAddress

logger = logging.getLogger(__name__)

__all__ = ["BluetoothDevice", "DeviceState"]

BLUEZ = "org.bluez"
DEVICE_INTERFACE = "org.bluez.Device1"
BATTERY_INTERFACE = "org.bluez.Battery1"
TRANSPORT_INTERFACE = "org.bluez.MediaTransport1"
ENDPOINT_INTERFACE = "org.bluez.MediaEndpoint1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"

#: Consecutive failed connections after which the transport is called down.
#: The same three that used to drop the monitor into bluetoothctl polling.
UNAVAILABLE_AFTER_FAILURES = 3

#: How long a synchronous caller waits for the loop before calling it unknown.
#: A Waitress worker must not be pinned by a wedged bus.
_BLOCKING_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Everything the module knows about a speaker, read together.

    Callers that need several of these at once take this rather than asking
    four questions in a row and stitching an answer that was never true at any
    single moment.
    """

    connected: bool
    paired: bool | None
    services_resolved: bool | None
    uuids: tuple[str, ...]
    battery_level: int | None
    transport_state: str | None
    has_media_endpoint: bool
    object_path: str | None


def _unwrap(value: Any) -> Any:
    """D-Bus hands values over wrapped in variants."""
    return value.value if hasattr(value, "value") else value


class BluetoothDevice:
    """One speaker, on one controller, over D-Bus."""

    def __init__(
        self,
        address: DeviceAddress,
        *,
        controller: str,
        bus_factory: Callable[[], Any] | None = None,
    ):
        self.address = address
        self.controller = controller
        self._bus_factory = bus_factory
        self._bus: Any = None
        self._object_path: str | None = None
        self._props_iface: Any = None
        self._props_handler: Callable | None = None
        self._watchers: list[Callable[[str, Any], None]] = []
        self._failures = 0
        self._errors = threading.local()
        self._lock = asyncio.Lock()

    # -- what the caller can see without asking BlueZ --------------------

    @property
    def object_path(self) -> str | None:
        """Where this speaker was found, once it has been.

        Read-only and only for the telling: a bug report that names the
        controller an operation ran against answers a question the logs
        otherwise cannot (issue #340).
        """
        return self._object_path

    @property
    def transport_available(self) -> bool:
        """Whether the bus has answered us recently enough to be trusted."""
        return self._failures < UNAVAILABLE_AFTER_FAILURES

    @property
    def last_error(self) -> str | None:
        """Why the last operation *this thread* asked for did not happen.

        Per-thread on purpose: the module is driven from the bridge loop, from
        Waitress workers and from D-Bus callbacks, and a single field would be
        a value that can change between the call and the read.
        """
        return getattr(self._errors, "message", None)

    # -- the named questions ---------------------------------------------

    async def is_connected(self) -> bool:
        return bool(await self._property("Connected", default=False))

    async def is_paired(self) -> bool | None:
        value = await self._property("Paired")
        return None if value is None else bool(value)

    async def services_resolved(self) -> bool | None:
        value = await self._property("ServicesResolved")
        return None if value is None else bool(value)

    async def uuids(self) -> list[str]:
        value = await self._property("UUIDs")
        return [str(u) for u in value] if isinstance(value, (list, tuple)) else []

    async def battery_level(self) -> int | None:
        value = await self._property("Percentage", interface=BATTERY_INTERFACE)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def transport_state(self) -> str | None:
        """``idle`` / ``pending`` / ``active`` for this speaker's A2DP transport."""
        objects = await self._managed_objects()
        path = self._object_path
        if not objects or not path:
            return None
        for candidate, interfaces in objects.items():
            if candidate.startswith(f"{path}/") and TRANSPORT_INTERFACE in interfaces:
                state = _unwrap(interfaces[TRANSPORT_INTERFACE].get("State"))
                return str(state) if state is not None else None
        return None

    async def has_media_endpoint(self) -> bool:
        """Whether a local audio backend registered an endpoint for this speaker.

        The distinction the sink-not-found guidance rests on: no endpoint means
        the audio server never offered A2DP, not that the speaker is silent.
        """
        objects = await self._managed_objects()
        path = self._object_path
        if not objects or not path:
            return False
        return any(
            candidate.startswith(f"{path}/") and ENDPOINT_INTERFACE in interfaces
            for candidate, interfaces in objects.items()
        )

    async def state(self) -> DeviceState:
        """One consistent answer for callers that need several facts."""
        objects = await self._managed_objects()
        path = self._object_path
        device = (objects.get(path) or {}).get(DEVICE_INTERFACE, {}) if objects and path else {}
        battery = (objects.get(path) or {}).get(BATTERY_INTERFACE, {}) if objects and path else {}
        transport_state: str | None = None
        endpoint = False
        for candidate, interfaces in (objects or {}).items():
            if not path or not candidate.startswith(f"{path}/"):
                continue
            if TRANSPORT_INTERFACE in interfaces:
                raw = _unwrap(interfaces[TRANSPORT_INTERFACE].get("State"))
                transport_state = str(raw) if raw is not None else None
            if ENDPOINT_INTERFACE in interfaces:
                endpoint = True

        paired = _unwrap(device.get("Paired")) if "Paired" in device else None
        resolved = _unwrap(device.get("ServicesResolved")) if "ServicesResolved" in device else None
        raw_uuids = _unwrap(device.get("UUIDs")) or []
        raw_battery = _unwrap(battery.get("Percentage")) if "Percentage" in battery else None
        return DeviceState(
            connected=bool(_unwrap(device.get("Connected", False))),
            paired=None if paired is None else bool(paired),
            services_resolved=None if resolved is None else bool(resolved),
            uuids=tuple(str(u) for u in raw_uuids),
            battery_level=int(raw_battery) if isinstance(raw_battery, int) else None,
            transport_state=transport_state,
            has_media_endpoint=endpoint,
            object_path=path,
        )

    async def wait_for_services(self, timeout: float = 10.0) -> bool:
        """Wait until BlueZ says this speaker's services are resolved.

        Configuring audio before that is the race that made a fast reconnect
        land on a sink the speaker had not published yet.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if await self.services_resolved():
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.2)

    # -- the one operation -----------------------------------------------

    async def connect_profile(self, uuid: str) -> bool:
        """Ask BlueZ to start one profile, working around bluez/bluez#1922."""
        interface = await self._device_interface()
        if interface is None:
            self._remember("device not on the bus")
            return False
        try:
            await interface.call_connect_profile(uuid)
        except Exception as exc:
            self._remember(str(exc))
            return False
        self._remember(None)
        return True

    # -- the same questions, for callers with no loop of their own --------
    #
    # Thirty-six of the call sites this module replaces are plain synchronous
    # functions: the manager is driven from Waitress workers and from D-Bus
    # callbacks.  They hand the work to the bridge loop and wait, so "which
    # thread am I on" is answered here rather than at each of them.

    def is_connected_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> bool:
        return bool(self._blocking(self.is_connected(), timeout=timeout, default=False))

    def is_paired_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> bool | None:
        return self._blocking(self.is_paired(), timeout=timeout, default=None)

    def services_resolved_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> bool | None:
        return self._blocking(self.services_resolved(), timeout=timeout, default=None)

    def uuids_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> list[str]:
        return self._blocking(self.uuids(), timeout=timeout, default=[]) or []

    def battery_level_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> int | None:
        return self._blocking(self.battery_level(), timeout=timeout, default=None)

    def transport_state_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> str | None:
        return self._blocking(self.transport_state(), timeout=timeout, default=None)

    def has_media_endpoint_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> bool | None:
        return self._blocking(self.has_media_endpoint(), timeout=timeout, default=None)

    def state_blocking(self, *, timeout: float = _BLOCKING_TIMEOUT_S) -> DeviceState | None:
        return self._blocking(self.state(), timeout=timeout, default=None)

    def wait_for_services_blocking(
        self,
        *,
        is_connected_check: Callable[[], bool],
        wait_with_cancel: Callable[[float], bool],
        timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> bool | None:
        """Wait for SDP resolution from a thread, three answers deep.

        ``True`` — resolved. ``False`` — we watched and it did not: timed out,
        the speaker went away, or the caller cancelled. ``None`` — we could not
        watch at all, and the caller should proceed without a warning it cannot
        act on. The caller supplies both the "is it still here" check and the
        cancellable wait, because both belong to whatever is driving the
        connect, not to this module.
        """
        if self.state_blocking() is None:
            return None
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self.services_resolved_blocking():
                return True
            if not is_connected_check():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if not wait_with_cancel(min(poll_interval, remaining)):
                return False

    def connect_profile_blocking(self, uuid: str, *, timeout: float = _BLOCKING_TIMEOUT_S) -> bool:
        return bool(self._blocking(self.connect_profile(uuid), timeout=timeout, default=False))

    def _blocking(self, coro: Any, *, timeout: float, default: Any) -> Any:
        """Run *coro* on the bridge loop and wait for it.

        Returns *default* when there is no loop to run it on — startup and
        shutdown both have such windows, and the callers this replaces have
        always read them as "don't know".  Raises when called *from* the loop:
        waiting there is a deadlock, and a deadlock is a programming error.
        """
        from sendspin_bridge.bridge import state as _state

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            coro.close()
            raise RuntimeError(
                f"{type(self).__name__}.*_blocking() called from the event loop — await the async method instead"
            )

        loop = _state.get_main_loop()
        if loop is None or not loop.is_running():
            coro.close()
            return default
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)
        except TimeoutError:
            logger.debug("[%s] blocking read timed out after %.1fs", self.address, timeout)
            return default
        except Exception as exc:
            logger.debug("[%s] blocking read failed: %s", self.address, exc)
            return default

    # -- signals ----------------------------------------------------------

    def watch(self, handler: Callable[[str, Any], None]) -> None:
        """Call *handler(property_name, value)* whenever BlueZ changes one."""
        self._watchers.append(handler)

    async def close(self) -> None:
        """Drop the subscription and the bus.

        Kept beside the subscribe so a reconnect cannot stack handlers — the
        monitor used to hold that pairing in a ``finally`` and every missed one
        made every signal fire twice.
        """
        async with self._lock:
            await self._unsubscribe()
            bus, self._bus = self._bus, None
            self._object_path = None
            if bus is not None:
                try:
                    bus.disconnect()
                except Exception as exc:
                    logger.debug("[%s] bus disconnect failed: %s", self.address, exc)

    # -- internals ---------------------------------------------------------

    def _remember(self, message: str | None) -> None:
        self._errors.message = message

    async def _connect_bus(self) -> Any:
        if self._bus is not None and getattr(self._bus, "connected", True):
            return self._bus
        factory = self._bus_factory or _system_bus
        try:
            bus = factory()
            self._bus = await bus.connect() if hasattr(bus, "connect") else bus
            self._failures = 0
        except Exception as exc:
            self._failures += 1
            self._bus = None
            logger.debug("[%s] system bus unavailable (%d): %s", self.address, self._failures, exc)
            return None
        return self._bus

    async def _managed_objects(self) -> dict[str, dict[str, dict[str, Any]]] | None:
        """Everything BlueZ knows, and where this speaker is in it."""
        bus = await self._connect_bus()
        if bus is None:
            return None
        try:
            introspection = await bus.introspect(BLUEZ, "/")
            proxy = bus.get_proxy_object(BLUEZ, "/", introspection)
            manager = proxy.get_interface(OBJECT_MANAGER_INTERFACE)
            objects = await manager.call_get_managed_objects()
        except Exception as exc:
            self._failures += 1
            logger.debug("[%s] ObjectManager unavailable: %s", self.address, exc)
            return None
        self._failures = 0
        self._object_path = self._find_path(objects)
        await self._subscribe()
        return objects

    def _find_path(self, objects: dict[str, dict[str, dict[str, Any]]]) -> str | None:
        """This speaker's object on our controller, by address rather than shape."""
        prefix = f"/org/bluez/{self.controller}/"
        for path, interfaces in objects.items():
            device = interfaces.get(DEVICE_INTERFACE)
            if not device or not path.startswith(prefix):
                continue
            from sendspin_bridge.bluetooth.address import DeviceAddress

            if DeviceAddress.parse(_unwrap(device.get("Address"))) == self.address:
                return path
        return None

    async def _device_interface(self, interface: str = DEVICE_INTERFACE) -> Any:
        await self._managed_objects()
        bus, path = self._bus, self._object_path
        if bus is None or not path:
            return None
        try:
            introspection = await bus.introspect(BLUEZ, path)
            proxy = bus.get_proxy_object(BLUEZ, path, introspection)
            return proxy.get_interface(interface)
        except Exception as exc:
            logger.debug("[%s] interface %s unavailable: %s", self.address, interface, exc)
            return None

    async def _property(self, name: str, *, interface: str = DEVICE_INTERFACE, default: Any = None) -> Any:
        objects = await self._managed_objects()
        path = self._object_path
        if not objects or not path:
            return default
        props = objects.get(path, {}).get(interface, {})
        if name not in props:
            return default
        return _unwrap(props[name])

    async def _subscribe(self) -> None:
        """Listen for property changes, once per resolved object."""
        if self._props_handler is not None or not self._object_path or self._bus is None:
            return
        try:
            introspection = await self._bus.introspect(BLUEZ, self._object_path)
            proxy = self._bus.get_proxy_object(BLUEZ, self._object_path, introspection)
            props = proxy.get_interface(PROPERTIES_INTERFACE)
        except Exception as exc:
            logger.debug("[%s] could not subscribe: %s", self.address, exc)
            return

        def _on_changed(interface_name: str, changed: dict, _invalidated: list) -> None:
            if interface_name not in (DEVICE_INTERFACE, BATTERY_INTERFACE):
                return
            for key, variant in changed.items():
                for watcher in list(self._watchers):
                    try:
                        watcher(key, _unwrap(variant))
                    except Exception as exc:
                        logger.debug("[%s] watcher failed on %s: %s", self.address, key, exc)

        props.on_properties_changed(_on_changed)
        self._props_iface = props
        self._props_handler = _on_changed

    async def _unsubscribe(self) -> None:
        props, handler = self._props_iface, self._props_handler
        self._props_iface = self._props_handler = None
        if props is None or handler is None:
            return
        try:
            props.off_properties_changed(handler)
        except Exception as exc:
            logger.debug("[%s] could not unsubscribe: %s", self.address, exc)


def _system_bus() -> Any:
    from dbus_fast import BusType  # type: ignore
    from dbus_fast.aio import MessageBus  # type: ignore

    return MessageBus(bus_type=BusType.SYSTEM)
