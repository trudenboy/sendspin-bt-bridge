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

    def add_adapter(self, path: str, address: str, *, powered: bool = False, **extra: Any) -> None:
        """Register a controller object the way ObjectManager reports one."""
        self.objects[path] = {
            "org.bluez.Adapter1": {"Address": address, "Powered": powered, **extra},
        }

    def add_transport(self, path: str, state: str, *, device: str | None = None) -> None:
        """A MediaTransport1 under a device — what A2DP is doing.

        BlueZ names the owning device on the transport; the telemetry selector
        matches on it, so the fake carries it too. Defaults to the parent path.
        """
        self.objects[path] = {"org.bluez.MediaTransport1": {"State": state, "Device": device or path.rsplit("/", 1)[0]}}

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
        if name == "org.bluez.Adapter1":
            return FakeAdapterInterface(self._bluez, self._path)
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

    async def call_set(self, interface: str, name: str, variant: Any) -> None:
        """Properties.Set, as BlueZ applies it: the value lands and listeners hear."""
        self._bluez.calls.append((self._path, f"Set {name}", (interface,)))
        if f"Set {name}" in self._bluez.fail:
            raise self._bluez.fail[f"Set {name}"]
        self._bluez.set_property(self._path, name, getattr(variant, "value", variant), interface=interface)

    async def call_get_all(self, interface: str) -> dict:
        props = self._bluez.objects.get(self._path, {}).get(interface, {})
        return {k: FakeVariant(v) for k, v in props.items()}

    def on_properties_changed(self, handler) -> None:
        self._bluez.subscribe(self._path, handler)

    def off_properties_changed(self, handler) -> None:
        self._bluez.unsubscribe(self._path, handler)


class FakeAdapterInterface:
    def __init__(self, bluez: FakeBlueZ, path: str):
        self._bluez = bluez
        self._path = path

    async def call_remove_device(self, device_path: str) -> None:
        self._bluez.calls.append((self._path, "RemoveDevice", (device_path,)))
        if "RemoveDevice" in self._bluez.fail:
            raise self._bluez.fail["RemoveDevice"]
        self._bluez.remove(device_path)


class FakeDeviceInterface:
    def __init__(self, bluez: FakeBlueZ, path: str):
        self._bluez = bluez
        self._path = path

    async def call_connect_profile(self, uuid: str) -> None:
        self._bluez.calls.append((self._path, "ConnectProfile", (uuid,)))
        if "ConnectProfile" in self._bluez.fail:
            raise self._bluez.fail["ConnectProfile"]

    async def call_connect(self) -> None:
        self._bluez.calls.append((self._path, "Connect", ()))
        if "Connect" in self._bluez.fail:
            raise self._bluez.fail["Connect"]
        self._bluez.set_property(self._path, "Connected", True)

    async def call_disconnect(self) -> None:
        self._bluez.calls.append((self._path, "Disconnect", ()))
        if "Disconnect" in self._bluez.fail:
            raise self._bluez.fail["Disconnect"]

    async def call_disconnect_profile(self, uuid: str) -> None:
        self._bluez.calls.append((self._path, "DisconnectProfile", (uuid,)))
        if "DisconnectProfile" in self._bluez.fail:
            raise self._bluez.fail["DisconnectProfile"]


# -- wiring a manager to a fake BlueZ -------------------------------------


def device_module(manager, bluez: FakeBlueZ | None = None, *, controller: str = "hci0"):
    """A device module for *manager*'s speaker, reading from *bluez*.

    With no *bluez*, BlueZ knows nothing about the speaker — which is how a
    caller sees an unresolvable device object, and the state the bluetoothctl
    fallback exists for.
    """
    from sendspin_bridge.bluetooth.address import DeviceAddress
    from sendspin_bridge.bluetooth.device import BluetoothDevice

    address = DeviceAddress.require(manager.mac_address)
    return BluetoothDevice(address, controller=controller, bus_factory=(bluez or FakeBlueZ()).bus)


def bluez_knowing(manager, *, controller: str = "hci0", path: str | None = None, **device) -> FakeBlueZ:
    """A FakeBlueZ that knows *manager*'s speaker, with the given properties."""
    from sendspin_bridge.bluetooth.address import DeviceAddress

    address = DeviceAddress.require(manager.mac_address)
    bluez = FakeBlueZ()
    bluez.add_device(path or f"/org/bluez/{controller}/{address.dbus_node}", address.colons, **device)
    return bluez


def attach(manager, bluez: FakeBlueZ | None = None, *, controller: str = "hci0"):
    """Give *manager* a device module reading from *bluez*, and return it.

    Pins the manager's controller to match: the manager rebuilds its module
    when the controller it resolved differs from the one the module was built
    for, which would quietly drop the fake.
    """
    manager.adapter_hci_name = controller
    module = device_module(manager, bluez, controller=controller)
    manager.device = module
    return module


def silent(manager, *, controller: str = "hci0"):
    """Attach a module whose BlueZ answers nothing — the fallback's condition."""
    return attach(manager, FakeBlueZ(), controller=controller)


def unreachable(manager, error: Exception | None = None, *, controller: str = "hci0"):
    """Attach a module whose bus raises — a transport failure, not an answer."""
    bluez = FakeBlueZ()
    bluez.fail["GetManagedObjects"] = error or RuntimeError("D-Bus exploded")
    return attach(manager, bluez, controller=controller)


def controller_knowing_adapters(mapping: dict[str, str]):
    """A controller whose bus knows these ``hciN -> address`` controllers.

    For tests about how an ``hciN`` name is resolved: the object path is the
    kernel's own numbering, which is the whole reason the read exists.
    Install it with ``set_controller`` and it answers for the process.
    """
    from sendspin_bridge.bluetooth.bluez import get_bluez
    from sendspin_bridge.bluetooth.controller import DbusController, PreferredController

    bluez = FakeBlueZ()
    for hci, address in mapping.items():
        bluez.add_adapter(f"/org/bluez/{hci}", address, powered=True)
    return PreferredController(DbusController(bus_factory=bluez.bus), get_bluez)
