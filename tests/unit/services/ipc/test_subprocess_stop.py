from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from sendspin_bridge.services.ipc.subprocess_stop import SubprocessStopService


@pytest.mark.asyncio
async def test_cancel_reader_tasks_cancels_and_clears_slots():
    service = SubprocessStopService()

    async def sleeper():
        await asyncio.sleep(3600)

    task = asyncio.create_task(sleeper())
    cleared = await service.cancel_reader_tasks({"_daemon_task": task, "_stderr_task": None})

    assert cleared == {"_daemon_task": None, "_stderr_task": None}
    assert task.cancelled() is True


class _FakeProc:
    def __init__(self, *, wait_raises_timeout: bool = False):
        self.returncode: int | None = None
        self.kill_called = False
        self._wait_raises_timeout = wait_raises_timeout

    async def wait(self):
        if self._wait_raises_timeout:
            raise TimeoutError
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.kill_called = True
        self._wait_raises_timeout = False
        self.returncode = -9


@pytest.mark.asyncio
async def test_stop_process_requests_graceful_stop():
    service = SubprocessStopService()
    sent: list[dict] = []
    proc = _FakeProc()

    async def fake_send(cmd):
        sent.append(cmd)

    await service.stop_process(proc, send_stop=fake_send, player_name="Kitchen")

    assert sent == [{"cmd": "stop"}]
    assert proc.kill_called is False


@pytest.mark.asyncio
async def test_stop_process_kills_when_wait_times_out():
    logger = Mock()
    service = SubprocessStopService(logger_=logger)
    sent: list[dict] = []
    proc = _FakeProc(wait_raises_timeout=True)

    async def fake_send(cmd):
        sent.append(cmd)

    await service.stop_process(proc, send_stop=fake_send, player_name="Kitchen")

    assert sent == [{"cmd": "stop"}]
    assert proc.kill_called is True
    logger.warning.assert_called_once()
