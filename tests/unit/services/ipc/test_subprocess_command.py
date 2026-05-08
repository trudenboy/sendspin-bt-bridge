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


@pytest.mark.asyncio
async def test_send_raises_ipc_error_when_write_fails():
    """A live proc whose stdin write throws must surface :class:`IPCError`
    so transactional callers (``apply_hot_config``) can decide whether to
    commit parent-state."""
    from sendspin_bridge.bridge.exceptions import IPCError

    class _ExplodingStdin:
        def write(self, data):
            raise BrokenPipeError("simulated broken pipe")

        async def drain(self):  # pragma: no cover — never reached
            return None

    service = SubprocessCommandService()
    proc = _FakeProc(_ExplodingStdin())

    with pytest.raises(IPCError):
        await service.send(proc, {"cmd": "set_static_delay_ms", "value": 100})


@pytest.mark.asyncio
async def test_send_does_not_raise_for_dead_proc():
    """``send`` is a best-effort no-op for a missing or already-exited proc —
    callers depend on this for the natural race between an ``is_running``
    check and the ``await``.  Adding a raise on the early-return path would
    flood logs with spurious IPCErrors during graceful shutdown."""
    from sendspin_bridge.bridge.exceptions import IPCError

    service = SubprocessCommandService()
    dead = _FakeProc(_FakeStdin())
    dead.returncode = 0  # subprocess exited cleanly

    # Must not raise — early-return path stays silent.
    await service.send(None, {"cmd": "set_static_delay_ms", "value": 100})
    await service.send(dead, {"cmd": "set_static_delay_ms", "value": 100})

    # Ensure the import is exercised even though no exception is raised.
    assert IPCError.__name__ == "IPCError"
