"""The controller's verbs, over BlueZ's own bus.

Connect, disconnect, trust, forget and power are the verbs that act on a
controller.  The bridge has always run them through ``bluetoothctl``: a
subprocess per verb, a ``select <MAC>`` line to aim it, and a transcript to
read the answer out of.  Two of those three are where the bugs lived.  The
select line is advisory — BlueZ may not have applied it before the verb ran,
which is how a power-cycle aimed at hci0 powered hci1 (issue #340) — and the
transcript says "Failed to connect" in a form that changes between BlueZ
releases.

This is the same verbs against BlueZ directly.  The object path names the
controller, so an operation aimed at hci1 cannot land on hci0; the error
comes back as an exception carrying the BlueZ error name rather than as a
line to grep for; and there is no process to spawn per verb.

It answers with the same verdicts the bluetoothctl transport answers with,
because they describe the operation, not the way it was carried out.  When
this transport cannot answer at all — no bus, no such controller — it says
``UNAVAILABLE``, which is the one verdict that means "ask the other way".
Pairing is not here: it needs an agent, and it stays where the agent is.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Protocol

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.bluez import Adapter, Deadline, Outcome, PowerResult, RemoveResult, VerbResult

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["ControllerVerbs", "DbusController", "PreferredController", "get_controller", "set_controller"]

BLUEZ = "org.bluez"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
DEVICE_INTERFACE = "org.bluez.Device1"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"

#: How long a verb waits for the bus before calling itself unanswered.  The
#: same tier the bluetoothctl transport gives a mutating verb, so a caller
#: sees the same patience whichever transport ran it.
_VERB_TIMEOUT_S = float(Deadline.MUTATE)


class ControllerVerbs(Protocol):
    """What every transport for the controller's verbs must answer.

    Both transports satisfy it: :class:`DbusController` here, and the
    bluetoothctl one in :mod:`sendspin_bridge.bluetooth.bluez`.
    """

    def connect(self, mac: str, adapter: Adapter = ..., *, timeout: float | None = ...) -> VerbResult: ...

    def disconnect(self, mac: str, adapter: Adapter = ..., *, timeout: float | None = ...) -> VerbResult: ...

    def trust(self, mac: str, adapter: Adapter = ..., *, timeout: float | None = ...) -> VerbResult: ...

    def remove(self, mac: str, adapter: Adapter = ..., *, timeout: float | None = ...) -> RemoveResult: ...

    def power(self, on: bool, adapter: Adapter = ..., *, timeout: float | None = ...) -> PowerResult: ...

    def adapter_address(self, hci_name: str) -> str: ...


def _unwrap(value: Any) -> Any:
    """D-Bus hands values over wrapped in variants."""
    return value.value if hasattr(value, "value") else value


def _unavailable(detail: str) -> VerbResult:
    return VerbResult(Outcome.UNAVAILABLE, detail)


class DbusController:
    """The controller's verbs, spoken to BlueZ over D-Bus.

    The verbs are synchronous, as their callers are: the manager runs from
    the Bluetooth executor and the web layer from a Waitress worker.  The
    work itself happens on the bridge loop, so a wedged bus pins neither.
    """

    def __init__(self, *, bus_factory: Callable[[], Any] | None = None):
        self._bus_factory = bus_factory
        self._bus: Any = None
        self._lock = asyncio.Lock()
        self._addresses: dict[str, str] = {}

    # -- the verbs -------------------------------------------------------

    def connect(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> VerbResult:
        """Bring up the link to a speaker through this controller."""
        return self._run(self._device_method(mac, adapter, "call_connect"), timeout)

    def disconnect(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> VerbResult:
        """Drop the link to a speaker."""
        return self._run(self._device_method(mac, adapter, "call_disconnect"), timeout)

    def trust(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> VerbResult:
        """Let a speaker reconnect to this controller on its own."""
        return self._run(self._set_trusted(mac, adapter), timeout)

    def remove(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> RemoveResult:
        """Forget a speaker: drop the bond and BlueZ's object for it."""
        default = RemoveResult(removed=False, not_available=False, outcome=Outcome.UNAVAILABLE, detail="no bus")
        return self._run(self._remove(mac, adapter), timeout, default=default)

    def power(self, on: bool, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> PowerResult:
        """Power a controller up or down, reported against its own state."""
        default = PowerResult(applied=False, powered=not on, outcome=Outcome.UNAVAILABLE, detail="no bus")
        return self._run(self._power(on, adapter), timeout, default=default)

    def adapter_address(self, hci_name: str) -> str:
        """The address of the controller the kernel calls *hci_name*.

        The object path is the kernel's own numbering, unlike the order
        ``bluetoothctl list`` prints controllers in, which is BlueZ's
        registration order and diverges after a hot-plug.
        """
        if not hci_name:
            return ""
        address = self._run(self._adapter_address(hci_name), None, default="", refuse_on_loop=False)
        if address:
            self._addresses[hci_name] = address
            return address
        # A caller already on the loop cannot wait for it, and a controller's
        # address does not change while it is plugged in: the last live read
        # answers, and the ladder in the bluetoothctl transport takes over
        # when there has not been one.
        return self._addresses.get(hci_name, "")

    # -- what each verb does ---------------------------------------------

    async def _device_method(self, mac: str, adapter: Adapter, method: str) -> VerbResult:
        interface = await self._device_interface(mac, adapter)
        if isinstance(interface, VerbResult):
            return interface
        try:
            await getattr(interface, method)()
        except Exception as exc:
            return VerbResult(Outcome.FAILED, str(exc))
        return VerbResult(Outcome.OK)

    async def _set_trusted(self, mac: str, adapter: Adapter) -> VerbResult:
        objects = await self._objects()
        if objects is None:
            return _unavailable("no bus")
        path = self._device_path(objects, mac, adapter)
        if path is None:
            return _unavailable(f"BlueZ has no object for {mac}")
        return await self._set_property(path, DEVICE_INTERFACE, "Trusted", True)

    async def _remove(self, mac: str, adapter: Adapter) -> RemoveResult:
        objects = await self._objects()
        if objects is None:
            return RemoveResult(removed=False, not_available=False, outcome=Outcome.UNAVAILABLE, detail="no bus")
        adapter_path = self._adapter_path(objects, adapter)
        if adapter_path is None:
            return RemoveResult(
                removed=False,
                not_available=False,
                outcome=Outcome.UNAVAILABLE,
                detail=f"no controller for {adapter.ident or 'the default scope'}",
            )
        path = self._device_path(objects, mac, adapter)
        if path is None:
            # BlueZ has no object for it: the caller's goal already holds.
            return RemoveResult(removed=False, not_available=True, detail=f"BlueZ has no object for {mac}")
        interface = await self._interface(adapter_path, ADAPTER_INTERFACE)
        if interface is None:
            return RemoveResult(removed=False, not_available=False, outcome=Outcome.UNAVAILABLE, detail="no bus")
        try:
            await interface.call_remove_device(path)
        except Exception as exc:
            return RemoveResult(removed=False, not_available=False, outcome=Outcome.FAILED, detail=str(exc))
        return RemoveResult(removed=True, not_available=False)

    async def _power(self, on: bool, adapter: Adapter) -> PowerResult:
        objects = await self._objects()
        if objects is None:
            return PowerResult(applied=False, powered=not on, outcome=Outcome.UNAVAILABLE, detail="no bus")
        path = self._adapter_path(objects, adapter)
        if path is None:
            return PowerResult(
                applied=False,
                powered=not on,
                outcome=Outcome.UNAVAILABLE,
                detail=f"no controller for {adapter.ident or 'the default scope'}",
            )
        verdict = await self._set_property(path, ADAPTER_INTERFACE, "Powered", on)
        if not verdict.ok:
            powered = bool(_unwrap(objects.get(path, {}).get(ADAPTER_INTERFACE, {}).get("Powered", not on)))
            return PowerResult(applied=False, powered=powered, outcome=verdict.outcome, detail=verdict.detail)
        # BlueZ applies a power change before it answers the Set, so the
        # state it reports afterwards is the state — no settle poll needed.
        powered = await self._read_property(path, ADAPTER_INTERFACE, "Powered")
        settled = on if powered is None else bool(powered)
        return PowerResult(applied=settled is on, powered=settled)

    async def _adapter_address(self, hci_name: str) -> str:
        objects = await self._objects()
        if objects is None:
            return ""
        interfaces = objects.get(f"/org/bluez/{hci_name}") or {}
        address = _unwrap((interfaces.get(ADAPTER_INTERFACE) or {}).get("Address"))
        return str(address) if address else ""

    # -- finding things on the bus ---------------------------------------

    def _adapter_path(self, objects: dict[str, Any], adapter: Adapter) -> str | None:
        """The object of the controller a scope names.

        A scope names a controller by ``hciN`` or by address; the default
        scope means the one BlueZ would have picked, which with a single
        controller is unambiguous and with several is the first — the same
        rule ``bluetoothctl`` follows.
        """
        paths = sorted(path for path, interfaces in objects.items() if ADAPTER_INTERFACE in interfaces)
        if adapter.is_default:
            return paths[0] if paths else None
        ident = adapter.ident.strip()
        wanted = DeviceAddress.parse(ident)
        for path in paths:
            if wanted is not None:
                if DeviceAddress.parse(_unwrap(objects[path][ADAPTER_INTERFACE].get("Address"))) == wanted:
                    return path
            elif path.rsplit("/", 1)[-1] == ident:
                return path
        return None

    def _device_path(self, objects: dict[str, Any], mac: str, adapter: Adapter) -> str | None:
        """A speaker's object under the controller a scope names."""
        address = DeviceAddress.parse(mac)
        if address is None:
            return None
        adapter_path = self._adapter_path(objects, adapter)
        if adapter_path is None:
            return None
        prefix = f"{adapter_path}/"
        for path, interfaces in objects.items():
            device = interfaces.get(DEVICE_INTERFACE)
            if not device or not path.startswith(prefix):
                continue
            if DeviceAddress.parse(_unwrap(device.get("Address"))) == address:
                return path
        return None

    async def _device_interface(self, mac: str, adapter: Adapter) -> Any:
        """The speaker's ``Device1``, or the verdict that explains its absence."""
        objects = await self._objects()
        if objects is None:
            return _unavailable("no bus")
        if self._adapter_path(objects, adapter) is None:
            return _unavailable(f"no controller for {adapter.ident or 'the default scope'}")
        path = self._device_path(objects, mac, adapter)
        if path is None:
            return _unavailable(f"BlueZ has no object for {mac}")
        interface = await self._interface(path, DEVICE_INTERFACE)
        return interface if interface is not None else _unavailable("no bus")

    async def _set_property(self, path: str, interface_name: str, name: str, value: bool) -> VerbResult:
        properties = await self._interface(path, PROPERTIES_INTERFACE)
        if properties is None:
            return _unavailable("no bus")
        try:
            await properties.call_set(interface_name, name, _variant(value))
        except Exception as exc:
            return VerbResult(Outcome.FAILED, str(exc))
        return VerbResult(Outcome.OK)

    async def _read_property(self, path: str, interface_name: str, name: str) -> Any:
        properties = await self._interface(path, PROPERTIES_INTERFACE)
        if properties is None:
            return None
        try:
            return _unwrap(await properties.call_get(interface_name, name))
        except Exception as exc:
            logger.debug("Reading %s.%s at %s failed: %s", interface_name, name, path, exc)
            return None

    async def _interface(self, path: str, name: str) -> Any:
        bus = await self._connect_bus()
        if bus is None:
            return None
        try:
            introspection = await bus.introspect(BLUEZ, path)
            return bus.get_proxy_object(BLUEZ, path, introspection).get_interface(name)
        except Exception as exc:
            logger.debug("No %s at %s: %s", name, path, exc)
            return None

    async def _objects(self) -> dict[str, dict[str, dict[str, Any]]] | None:
        """Everything BlueZ knows, or ``None`` when it could not be asked."""
        bus = await self._connect_bus()
        if bus is None:
            return None
        try:
            introspection = await bus.introspect(BLUEZ, "/")
            proxy = bus.get_proxy_object(BLUEZ, "/", introspection)
            return await proxy.get_interface(OBJECT_MANAGER_INTERFACE).call_get_managed_objects()
        except Exception as exc:
            logger.debug("ObjectManager unavailable: %s", exc)
            self._bus = None
            return None

    async def _connect_bus(self) -> Any:
        async with self._lock:
            if self._bus is not None and getattr(self._bus, "connected", True):
                return self._bus
            factory = self._bus_factory or _system_bus
            try:
                bus = factory()
                self._bus = await bus.connect() if hasattr(bus, "connect") else bus
            except Exception as exc:
                self._bus = None
                logger.debug("System bus unavailable: %s", exc)
            return self._bus

    # -- crossing to the loop ---------------------------------------------

    def _run(self, coro: Any, timeout: float | None, default: Any = None, *, refuse_on_loop: bool = True) -> Any:
        """Run *coro* on the bridge loop and wait for its answer.

        Without a loop to run it on there is no answer — startup and
        shutdown both have such windows — and the caller hears the same
        thing as from a bus that is not there, which is what it is.
        """
        from sendspin_bridge.bridge import state as _state

        if default is None:
            default = _unavailable("no bridge loop")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            coro.close()
            if refuse_on_loop:
                raise RuntimeError("DbusController verbs are synchronous — call them off the event loop")
            return default

        loop = _state.get_main_loop()
        if loop is None or not loop.is_running():
            coro.close()
            return default
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout if timeout is not None else _VERB_TIMEOUT_S)
        except TimeoutError:
            future.cancel()
            return _timed_out(default)
        except Exception as exc:
            logger.debug("Controller verb failed: %s", exc)
            return default


def _timed_out(default: Any) -> Any:
    """The default the caller would have got, said as a timeout."""
    if isinstance(default, VerbResult):
        return VerbResult(Outcome.TIMEOUT, "BlueZ did not answer")
    if isinstance(default, PowerResult):
        return PowerResult(
            applied=False, powered=default.powered, outcome=Outcome.TIMEOUT, detail="BlueZ did not answer"
        )
    if isinstance(default, RemoveResult):
        return RemoveResult(removed=False, not_available=False, outcome=Outcome.TIMEOUT, detail="BlueZ did not answer")
    return default


def _variant(value: bool) -> Any:
    from dbus_fast import Variant

    return Variant("b", value)


def _system_bus() -> Any:
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    return MessageBus(bus_type=BusType.SYSTEM)


class PreferredController:
    """BlueZ's own bus for every verb, with the subprocess behind it.

    The fallback is late-bound: tests and the runtime both replace the
    shared bluetoothctl transport after this composite is built, and a
    captured instance would keep answering from the old one.

    Only ``UNAVAILABLE`` hands a verb over.  A refusal is an answer — the
    speaker was out of range, the bond was gone — and running the same verb
    again through a subprocess just asks BlueZ the same thing twice, which
    on connect means a second page attempt and another ten seconds.
    """

    def __init__(self, primary: DbusController, fallback: Callable[[], Any]):
        self._primary = primary
        self._fallback = fallback

    @property
    def dbus(self) -> DbusController:
        """The D-Bus transport, for the reads only it can answer."""
        return self._primary

    def connect(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> VerbResult:
        return self._either("connect", mac, adapter, timeout=timeout)

    def disconnect(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> VerbResult:
        return self._either("disconnect", mac, adapter, timeout=timeout)

    def trust(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> VerbResult:
        return self._either("trust", mac, adapter, timeout=timeout)

    def remove(self, mac: str, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> RemoveResult:
        return self._either("remove", mac, adapter, timeout=timeout)

    def power(self, on: bool, adapter: Adapter = Adapter.DEFAULT, *, timeout: float | None = None) -> PowerResult:
        return self._either("power", on, adapter, timeout=timeout)

    def adapter_address(self, hci_name: str) -> str:
        """Only the bus can answer this one; "" means it could not."""
        return self._primary.adapter_address(hci_name)

    def _either(self, verb: str, subject: Any, adapter: Adapter, *, timeout: float | None) -> Any:
        result = getattr(self._primary, verb)(subject, adapter, timeout=timeout)
        if not result.unavailable:
            return result
        logger.debug("BlueZ could not answer %s (%s); asking bluetoothctl", verb, result.detail)
        return getattr(self._fallback(), verb)(subject, adapter, timeout=timeout)


_controller: PreferredController | None = None


def get_controller() -> PreferredController:
    """The transport the controller's verbs run through (lazy singleton)."""
    global _controller
    if _controller is None:
        from sendspin_bridge.bluetooth.bluez import get_bluez

        _controller = PreferredController(DbusController(), get_bluez)
    return _controller


def set_controller(controller: PreferredController | None) -> None:
    """Replace the shared transport (tests; pass ``None`` to reset)."""
    global _controller
    _controller = controller
