"""Shutdown gives the daemon a chance to leave cleanly.

The sequence cancelled the daemon task and then "waited for a clean
shutdown" with `wait_for(shield(daemon_task), 3.0)`.  `shield` protects an
outer await from cancellation; it cannot un-cancel a task already cancelled
a line earlier, so the wait returned immediately with `CancelledError` and
the wait-for-clean-shutdown step never once did what it says.  Every stop was
an abrupt teardown: no goodbye to the server, no PulseAudio drain — the
unclean disconnect the server-side reconnect logic then has to absorb.

Exceptions from the tasks were dropped too: `asyncio.wait` leaves them
unretrieved, so a command reader that died took its reason with it.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.services.ipc.shutdown import shut_down_tasks


class _Recorder:
    def __init__(self):
        self.events: list[str] = []


@pytest.mark.asyncio
async def test_the_primary_task_is_given_time_to_finish():
    recorder = _Recorder()

    async def _daemon():
        try:
            await asyncio.sleep(0.05)
            recorder.events.append("goodbye sent")
        except asyncio.CancelledError:
            recorder.events.append("cancelled")
            raise

    task = asyncio.ensure_future(_daemon())

    await shut_down_tasks(primary=task, auxiliary=[], grace_s=1.0)

    assert recorder.events == ["goodbye sent"], "the daemon never got to shut down cleanly"


@pytest.mark.asyncio
async def test_a_task_that_overstays_its_grace_is_cancelled():
    recorder = _Recorder()

    async def _stuck():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            recorder.events.append("cancelled")
            raise

    task = asyncio.ensure_future(_stuck())

    await shut_down_tasks(primary=task, auxiliary=[], grace_s=0.05)

    assert recorder.events == ["cancelled"]
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_auxiliary_tasks_are_cancelled_at_once():
    """Observability tasks have nothing to flush; they go immediately."""
    recorder = _Recorder()

    async def _watcher(name: str):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            recorder.events.append(name)
            raise

    aux = [asyncio.ensure_future(_watcher("a")), asyncio.ensure_future(_watcher("b"))]
    await asyncio.sleep(0)  # let them reach their first await, as in production

    async def _primary():
        return None

    await shut_down_tasks(primary=asyncio.ensure_future(_primary()), auxiliary=aux, grace_s=1.0)

    assert sorted(recorder.events) == ["a", "b"]


@pytest.mark.asyncio
async def test_a_failure_is_reported_rather_than_dropped():
    """`asyncio.wait` leaves exceptions unretrieved; a dead reader is news."""
    reported: list[tuple[str, BaseException]] = []

    async def _boom():
        raise RuntimeError("command reader died")

    async def _primary():
        return None

    aux = [asyncio.ensure_future(_boom())]
    await asyncio.sleep(0)

    await shut_down_tasks(
        primary=asyncio.ensure_future(_primary()),
        auxiliary=aux,
        grace_s=1.0,
        on_error=lambda label, exc: reported.append((label, exc)),
    )

    assert [label for label, _ in reported] == ["auxiliary"]
    assert isinstance(reported[0][1], RuntimeError)


@pytest.mark.asyncio
async def test_a_primary_failure_is_reported_too():
    reported: list[tuple[str, BaseException]] = []

    async def _boom():
        raise RuntimeError("daemon died")

    await shut_down_tasks(
        primary=asyncio.ensure_future(_boom()),
        auxiliary=[],
        grace_s=1.0,
        on_error=lambda label, exc: reported.append((label, exc)),
    )

    assert [label for label, _ in reported] == ["primary"]


@pytest.mark.asyncio
async def test_an_already_finished_task_needs_no_grace():
    async def _done():
        return None

    task = asyncio.ensure_future(_done())
    await asyncio.sleep(0)

    await shut_down_tasks(primary=task, auxiliary=[], grace_s=30.0)

    assert task.done()
