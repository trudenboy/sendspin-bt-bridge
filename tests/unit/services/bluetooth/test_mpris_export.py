"""The MPRIS export is a resource with a lifecycle, not two loose hooks.

Connect and disconnect used to schedule an export and an unexport
fire-and-forget, with nothing ordering them.  On a reconnect bounce the
unexport read a bus attribute the in-flight export had not set yet and
returned; the export then finished, opening a system-bus connection and
registering a player with BlueZ that nobody would ever unregister.  Each
bounce leaked one socket and left one orphan registration, and a speaker
that reconnect-loops does that until the process restarts.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.services.bluetooth.mpris_export import MprisExport

MAC = "AA:BB:CC:DD:EE:FF"
ADAPTER_PATH = "/org/bluez/hci0"
PATH = "/org/sendspin/players/AA_BB_CC_DD_EE_FF"


class _FakeMedia:
    def __init__(self, log: list[str]):
        self._log = log

    async def call_register_player(self, path, props):
        self._log.append(f"register {path}")

    async def call_unregister_player(self, path):
        self._log.append(f"unregister {path}")


class _FakeProxy:
    def __init__(self, log: list[str]):
        self._log = log

    def get_interface(self, _name):
        return _FakeMedia(self._log)


class _FakeBus:
    def __init__(self, log: list[str], connect_delay: float = 0.0):
        self._log = log
        self._connect_delay = connect_delay
        self.connected = False
        self.disconnected = False
        self.exported: list[str] = []

    async def connect(self):
        if self._connect_delay:
            await asyncio.sleep(self._connect_delay)
        self.connected = True
        self._log.append("bus connect")
        return self

    def export(self, path, _iface):
        self.exported.append(path)
        self._log.append(f"export {path}")

    def unexport(self, path):
        if path in self.exported:
            self.exported.remove(path)
        self._log.append(f"unexport {path}")

    async def introspect(self, _service, _path):
        return object()

    def get_proxy_object(self, _service, _path, _introspection):
        return _FakeProxy(self._log)

    def disconnect(self):
        self.disconnected = True
        self._log.append("bus disconnect")


@pytest.fixture()
def log():
    return []


def _export(log, *, connect_delay: float = 0.0, buses: list | None = None):
    def _bus_factory():
        bus = _FakeBus(log, connect_delay=connect_delay)
        if buses is not None:
            buses.append(bus)
        return bus

    return MprisExport(
        mac=MAC,
        player=object(),
        adapter_path_provider=lambda: ADAPTER_PATH,
        bus_factory=_bus_factory,
        iface_builder=lambda _player: object(),
    )


def test_export_registers_the_player_with_bluez(log):
    export = _export(log)

    assert asyncio.run(export.ensure_exported()) is True
    assert log == ["bus connect", f"export {PATH}", f"register {PATH}"]


def test_export_is_idempotent(log):
    export = _export(log)

    async def _run():
        await export.ensure_exported()
        await export.ensure_exported()

    asyncio.run(_run())

    assert log.count("bus connect") == 1
    assert log.count(f"register {PATH}") == 1


def test_unexport_unregisters_then_drops_the_connection(log):
    export = _export(log)

    async def _run():
        await export.ensure_exported()
        log.clear()
        await export.ensure_unexported()

    asyncio.run(_run())

    assert log == [f"unregister {PATH}", f"unexport {PATH}", "bus disconnect"]


def test_unexport_without_an_export_is_a_no_op(log):
    export = _export(log)

    asyncio.run(export.ensure_unexported())

    assert log == []


def test_a_bounce_leaves_nothing_registered_and_no_open_connection(log):
    """The reconnect-bounce race: unexport starts while export is connecting."""
    buses: list[_FakeBus] = []
    export = _export(log, connect_delay=0.05, buses=buses)

    async def _run():
        exporting = asyncio.ensure_future(export.ensure_exported())
        await asyncio.sleep(0)  # let the export reach its first await
        await export.ensure_unexported()
        await exporting

    asyncio.run(_run())

    assert log.count("bus connect") <= 1
    registered = log.count(f"register {PATH}") - log.count(f"unregister {PATH}")
    assert registered == 0, "BlueZ kept a registration for a player nobody will unregister"
    assert all(bus.disconnected for bus in buses if bus.connected), "a system-bus connection leaked"
    assert all(not bus.exported for bus in buses), "an object path stayed exported"


def test_repeated_bounces_do_not_accumulate_connections(log):
    buses: list[_FakeBus] = []
    export = _export(log, connect_delay=0.01, buses=buses)

    async def _run():
        for _ in range(5):
            exporting = asyncio.ensure_future(export.ensure_exported())
            await asyncio.sleep(0)
            await export.ensure_unexported()
            await exporting

    asyncio.run(_run())

    open_connections = [bus for bus in buses if bus.connected and not bus.disconnected]
    assert open_connections == []


def test_a_repeated_connect_retires_the_previous_export(monkeypatch):
    """BlueZ can repeat a connect transition on a flapping link."""
    import sendspin_bridge.services.bluetooth.device_activation as activation

    unexported: list[str] = []

    class _Stale:
        async def ensure_unexported(self):
            unexported.append(MAC)

    class _Loop:
        def call_soon_threadsafe(self, *a, **kw):
            return None

    def _run_coroutine_threadsafe(coro, _loop):
        asyncio.run(coro)

        class _Future:
            pass

        return _Future()

    monkeypatch.setitem(activation._EXPORTS, MAC, _Stale())
    monkeypatch.setattr(activation.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)
    monkeypatch.setattr(activation, "MprisExport", lambda **kw: _Stale())
    monkeypatch.setattr(
        "sendspin_bridge.services.lifecycle.bridge_runtime_state.get_main_loop",
        lambda: _Loop(),
    )

    client = type("_C", (), {"player_id": "p", "bt_manager": None})()
    hook = activation._make_mpris_connected_hook(client, MAC)
    hook()

    assert unexported == [MAC], "the previous export was orphaned"
