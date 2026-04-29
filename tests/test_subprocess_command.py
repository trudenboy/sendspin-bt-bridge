from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION
from sendspin_bridge.services.ipc.subprocess_command import SubprocessCommandService


class _FakeStdin:
    def __init__(self):
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)


class _FakeProc:
    def __init__(self, stdin):
        self.stdin = stdin
        self.returncode = None


@pytest.mark.asyncio
async def test_send_wraps_commands_with_protocol_version():
    service = SubprocessCommandService()
    stdin = _FakeStdin()

    await service.send(_FakeProc(stdin), {"cmd": "stop"})

    assert stdin.writes == [f'{{"cmd": "stop", "protocol_version": {IPC_PROTOCOL_VERSION}}}\n'.encode()]


@pytest.mark.asyncio
async def test_send_ignores_missing_or_exited_proc():
    service = SubprocessCommandService()
    stdin = _FakeStdin()
    proc = _FakeProc(stdin)
    proc.returncode = 1

    await service.send(None, {"cmd": "stop"})
    await service.send(proc, {"cmd": "stop"})

    assert stdin.writes == []
