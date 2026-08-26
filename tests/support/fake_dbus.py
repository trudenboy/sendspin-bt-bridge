"""A BlueZ that answers on a bus, without a bus.

The device module talks to `org.bluez` over D-Bus: it finds a speaker through
`ObjectManager`, reads `Device1` properties, listens for `PropertiesChanged`
and calls `ConnectProfile`.  This substitutes the bus underneath it, so a test
can say what BlueZ knows and what it does next, and nothing spawns, connects
or waits.

The seam is the bus, not the module: callers of the module see the real thing
in tests, which is the point — a device that answers wrongly is a device that
answers wrongly for `manager.py` too.
"""

from __future__ import annotations

from typing import Any


class FakeVariant:
    """dbus_fast hands property values over as variants."""

    __slots__ = ("value",)

    def __init__(self, value: Any):
        self.value = value


class FakeBlueZ:
    """What BlueZ knows, and what it did when we asked it to do something."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, dict[str, Any]]] = {}
        self.calls: list[tuple[str, str, tuple]] = []
        self.connected = True
        self.fail: dict[str, Exception] = {}
        self._subscribers: dict[str, list] = {}

    # -- what BlueZ knows ----------------------------------------------

    def add_device(
        self,
        path: str,
        address: str,
        *,
        connected: bool = False,
        paired: bool = True,
        services_resolved: bool = False,
        uuids: tuple[str, ...] = (),
        battery: int | None = None,
        **extra: Any,
    ) -> None:
        """Register a device object the way ObjectManager reports one."""
        interfaces: dict[str, dict[str, Any]] = {
            "org.bluez.Device1": {
                "Address": address,
                "Connected": connected,
                "Paired": paired,
                "ServicesResolved": services_resolved,
                "UUIDs": list(uuids),
                **extra,
            }
        }
        if battery is not None:
            interfaces["org.bluez.Battery1"] = {"Percentage": battery}
        self.objects[path] = interfaces

    def add_transport(self, path: str, state: str) -> None:
        """A MediaTransport1 under a device — what A2DP is doing."""
        self.objects[path] = {"org.bluez.MediaTransport1": {"State": state}}

    def add_media_endpoint(self, path: str) -> None:
        self.objects[path] = {"org.bluez.MediaEndpoint1": {"UUID": "0000110b-0000-1000-8000-00805f9b34fb"}}

    def remove(self, path: str) -> None:
        self.objects.pop(path, None)

    # -- what the module sees ------------------------------------------

    def set_property(self, path: str, name: str, value: Any, *, interface: str = "org.bluez.Device1") -> None:
        """Change a property and tell whoever is listening, as BlueZ does."""
        self.objects.setdefault(path, {}).setdefault(interface, {})[name] = value
        for handler in self._subscribers.get(path, []):
            handler(interface, {name: FakeVariant(value)}, [])

    def subscribe(self, path: str, handler) -> None:
        self._subscribers.setdefault(path, []).append(handler)

    def unsubscribe(self, path: str, handler) -> None:
        handlers = self._subscribers.get(path, [])
        if handler in handlers:
            handlers.remove(handler)

    @property
    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._subscribers.values())

    # -- the bus the module is given -----------------------------------

    def bus(self) -> FakeBus:
        return FakeBus(self)


class FakeBus:
    """Enough of `dbus_fast.aio.MessageBus` for the device module."""

    def __init__(self, bluez: FakeBlueZ):
        self._bluez = bluez
        self.disconnected = False

    @property
    def connected(self) -> bool:
        return self._bluez.connected and not self.disconnected

    async def connect(self) -> FakeBus:
        if not self._bluez.connected:
            raise ConnectionError("system bus unavailable")
        return self

    def disconnect(self) -> None:
        self.disconnected = True

    async def introspect(self, _service: str, path: str):
        if path not in self._bluez.objects and path != "/":
            raise LookupError(f"no such object: {path}")
        return path

    def get_proxy_object(self, _service: str, path: str, _introspection):
        return FakeProxy(self._bluez, path)


class FakeProxy:
    def __init__(self, bluez: FakeBlueZ, path: str):
        self._bluez = bluez
        self._path = path

    def get_interface(self, name: str):
        if name == "org.freedesktop.DBus.ObjectManager":
            return FakeObjectManager(self._bluez)
        if name == "org.freedesktop.DBus.Properties":
            return FakeProperties(self._bluez, self._path)
        return FakeDeviceInterface(self._bluez, self._path)


class FakeObjectManager:
    def __init__(self, bluez: FakeBlueZ):
        self._bluez = bluez

    async def call_get_managed_objects(self) -> dict:
        if "GetManagedObjects" in self._bluez.fail:
            raise self._bluez.fail["GetManagedObjects"]
        return {
            path: {iface: {k: FakeVariant(v) for k, v in props.items()} for iface, props in ifaces.items()}
            for path, ifaces in self._bluez.objects.items()
        }


class FakeProperties:
    def __init__(self, bluez: FakeBlueZ, path: str):
        self._bluez = bluez
        self._path = path

    async def call_get(self, interface: str, name: str):
        props = self._bluez.objects.get(self._path, {}).get(interface, {})
        if name not in props:
            raise LookupError(f"no such property: {name}")
        return FakeVariant(props[name])

    async def call_get_all(self, interface: str) -> dict:
        props = self._bluez.objects.get(self._path, {}).get(interface, {})
        return {k: FakeVariant(v) for k, v in props.items()}

    def on_properties_changed(self, handler) -> None:
        self._bluez.subscribe(self._path, handler)

    def off_properties_changed(self, handler) -> None:
        self._bluez.unsubscribe(self._path, handler)


class FakeDeviceInterface:
    def __init__(self, bluez: FakeBlueZ, path: str):
        self._bluez = bluez
        self._path = path

    async def call_connect_profile(self, uuid: str) -> None:
        self._bluez.calls.append((self._path, "ConnectProfile", (uuid,)))
        if "ConnectProfile" in self._bluez.fail:
            raise self._bluez.fail["ConnectProfile"]

    async def call_disconnect_profile(self, uuid: str) -> None:
        self._bluez.calls.append((self._path, "DisconnectProfile", (uuid,)))
        if "DisconnectProfile" in self._bluez.fail:
            raise self._bluez.fail["DisconnectProfile"]
