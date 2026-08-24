"""Scheduling work on the bridge loop, from whichever thread is asking.

`_update_status` says in its own docstring that it is entered from Flask
worker threads, the asyncio loop and D-Bus callbacks.  Two of its branches
called `asyncio.ensure_future` bare, which needs a running loop *on the
calling thread* — so from a Waitress worker or a D-Bus callback it raised
`RuntimeError`, aborting whatever status write triggered it: a volume set, a
mute, a playback flag.

The same file already knew the answer, spelling out
`get_main_loop() + run_coroutine_threadsafe` three times.  Power-save exit
was the site that missed it.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from sendspin_bridge.bridge.loop_scheduling import schedule_on_bridge_loop


@pytest.fixture(autouse=True)
def _no_ambient_loop(monkeypatch):
    """Default to "no bridge loop registered" so each test states its own."""
    import sendspin_bridge.bridge.state as state

    monkeypatch.setattr(state, "get_main_loop", lambda: None)


def _run_loop_in_thread() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _stop(loop, thread) -> None:
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def test_a_worker_thread_can_schedule_onto_the_bridge_loop(monkeypatch):
    """The case that used to raise: no loop on the calling thread."""
    import sendspin_bridge.bridge.state as state

    loop, thread = _run_loop_in_thread()
    monkeypatch.setattr(state, "get_main_loop", lambda: loop)
    ran = threading.Event()

    async def _work():
        ran.set()

    try:
        handle = schedule_on_bridge_loop(_work())
        assert handle is not None
        assert ran.wait(timeout=5), "the coroutine never ran on the bridge loop"
    finally:
        _stop(loop, thread)


def test_scheduling_from_the_loop_itself_still_works():
    ran: list[str] = []

    async def _work():
        ran.append("ran")

    async def _from_the_loop():
        schedule_on_bridge_loop(_work())
        await asyncio.sleep(0)

    asyncio.run(_from_the_loop())

    assert ran == ["ran"]


def test_no_bridge_loop_is_reported_not_raised():
    """Startup and shutdown both have windows with no loop; neither is a crash."""
    coro = asyncio.sleep(0)

    handle = schedule_on_bridge_loop(coro)

    assert handle is None
    coro.close()


def test_a_stopped_bridge_loop_is_reported_not_raised(monkeypatch):
    import sendspin_bridge.bridge.state as state

    loop = asyncio.new_event_loop()  # created, never started
    monkeypatch.setattr(state, "get_main_loop", lambda: loop)
    coro = asyncio.sleep(0)

    try:
        assert schedule_on_bridge_loop(coro) is None
    finally:
        coro.close()
        loop.close()


def test_the_failure_reason_reaches_the_log(monkeypatch, caplog):
    coro = asyncio.sleep(0)
    try:
        with caplog.at_level("DEBUG", logger="sendspin_bridge.bridge.loop_scheduling"):
            schedule_on_bridge_loop(coro, description="exit power save")
    finally:
        coro.close()

    assert "exit power save" in caplog.text


def test_update_status_from_a_worker_thread_does_not_raise(tmp_path, monkeypatch):
    """The whole point: a status write from a Flask worker must survive."""
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")

    from sendspin_bridge.bridge.client import SendspinClient

    loop, thread = _run_loop_in_thread()
    import sendspin_bridge.bridge.state as state

    monkeypatch.setattr(state, "get_main_loop", lambda: loop)

    client = SendspinClient("Test Player", "localhost", 9000)
    client.idle_mode = "power_save"
    client.status.update({"bt_power_save": True, "playing": False, "audio_streaming": False})

    failures: list[BaseException] = []

    def _worker():
        try:
            client._update_status({"playing": True, "audio_streaming": True})
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=_worker, name="waitress-worker")
    worker.start()
    worker.join(timeout=5)

    try:
        assert failures == [], f"a status write from a worker thread raised: {failures[0]!r}"
    finally:
        _stop(loop, thread)
