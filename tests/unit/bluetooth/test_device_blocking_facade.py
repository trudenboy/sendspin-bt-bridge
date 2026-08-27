"""Reading a speaker from a thread that has no event loop of its own.

Thirty-six of the callers this module replaces are ordinary synchronous
functions — the Bluetooth manager is driven from Waitress workers and from
D-Bus callbacks, not only from the bridge loop. They get a blocking facade
that hands the work to the bridge loop and waits, so "which thread am I on"
is decided in one place instead of thirty-six.

Called *from* the loop it must not wait: that is a deadlock, and it is a
programming error rather than a runtime condition, so it says so.
"""

from __future__ import annotations

import asyncio
import threading

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.device import BluetoothDevice
from tests.support.fake_dbus import FakeBlueZ

ADDRESS = DeviceAddress.require("FC:58:FA:EB:08:6C")
PATH = "/org/bluez/hci0/dev_FC_58_FA_EB_08_6C"


def _device(bluez: FakeBlueZ) -> BluetoothDevice:
    return BluetoothDevice(ADDRESS, controller="hci0", bus_factory=bluez.bus)


def _bluez(**device) -> FakeBlueZ:
    bluez = FakeBlueZ()
    bluez.add_device(PATH, ADDRESS.colons, **device)
    return bluez


def _loop_in_thread():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _stop(loop, thread) -> None:
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


# ── from an ordinary thread ──────────────────────────────────────────────


def test_a_worker_thread_gets_its_answer(monkeypatch):
    import sendspin_bridge.bridge.state as state

    loop, thread = _loop_in_thread()
    monkeypatch.setattr(state, "get_main_loop", lambda: loop)
    try:
        device = _device(_bluez(connected=True, paired=True))

        assert device.is_connected_blocking() is True
        assert device.is_paired_blocking() is True
    finally:
        _stop(loop, thread)


def test_without_a_bridge_loop_the_answer_is_no_answer(monkeypatch):
    """Startup and shutdown both have windows with no loop; they are not errors."""
    import sendspin_bridge.bridge.state as state

    monkeypatch.setattr(state, "get_main_loop", lambda: None)
    device = _device(_bluez(connected=True))

    assert device.is_connected_blocking() is False
    assert device.state_blocking() is None


def test_a_blocking_read_gives_up_rather_than_holding_the_caller(monkeypatch):
    """A wedged bus must not pin a Waitress worker for ever."""
    import sendspin_bridge.bridge.state as state

    loop, thread = _loop_in_thread()
    monkeypatch.setattr(state, "get_main_loop", lambda: loop)
    try:
        bluez = _bluez(connected=True)

        device = _device(bluez)
        started: list[asyncio.Task] = []

        async def _never_but_cancellable():
            task = asyncio.current_task()
            if task is not None:
                started.append(task)
            await asyncio.sleep(30)

        monkeypatch.setattr(device, "is_connected", _never_but_cancellable)

        assert device.is_connected_blocking(timeout=0.1) is False

        # The caller gave up; the work it abandoned must not outlive the test.
        done = threading.Event()

        async def _cancel_and_settle():
            for task in started:
                task.cancel()
            await asyncio.gather(*started, return_exceptions=True)
            done.set()

        asyncio.run_coroutine_threadsafe(_cancel_and_settle(), loop)
        done.wait(timeout=5)
    finally:
        _stop(loop, thread)


# ── from the loop itself ─────────────────────────────────────────────────


def test_a_read_that_timed_out_is_cancelled_not_abandoned(monkeypatch):
    """A caller that gave up must not leave D-Bus work running behind it.

    A wedged bus is exactly when this matters: every worker that times out
    would otherwise add another task still asking the same questions.
    """
    import sendspin_bridge.bridge.state as state

    loop, thread = _loop_in_thread()
    monkeypatch.setattr(state, "get_main_loop", lambda: loop)
    try:
        cancelled = threading.Event()

        async def _never():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        device = _device(_bluez(connected=True))
        monkeypatch.setattr(device, "is_connected", _never)

        assert device.is_connected_blocking(timeout=0.1) is False
        assert cancelled.wait(timeout=5), "the abandoned read was left running"
    finally:
        _stop(loop, thread)


def test_calling_the_facade_from_the_loop_is_refused(monkeypatch):
    """Waiting on the loop from the loop is a deadlock, not a slow call."""
    import sendspin_bridge.bridge.state as state

    loop, thread = _loop_in_thread()
    monkeypatch.setattr(state, "get_main_loop", lambda: loop)
    try:
        device = _device(_bluez(connected=True))
        errors: list[BaseException] = []

        async def _from_the_loop():
            try:
                device.is_connected_blocking()
            except BaseException as exc:
                errors.append(exc)

        asyncio.run_coroutine_threadsafe(_from_the_loop(), loop).result(timeout=5)

        assert errors and isinstance(errors[0], RuntimeError)
        assert "await" in str(errors[0]).lower()
    finally:
        _stop(loop, thread)
