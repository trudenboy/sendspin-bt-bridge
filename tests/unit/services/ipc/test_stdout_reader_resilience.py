"""The stdout reader is the daemon's only lifeline — it must not die quietly.

Production hand-rolled its own read loop and caught only `TimeoutError`, while
the hardened one that guards against oversized lines sat unused next door.  A
line past the 1 MB stream limit therefore killed the reader task, and nothing
killed the daemon with it: the daemon kept writing status through a blocking
`print`, the 64 KB pipe filled, and its event loop wedged for good.  Audio
carried on playing from the audio thread, so the speaker looked healthy while
accepting no commands at all.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.services.ipc.subprocess_ipc import SubprocessIpcService


class _Stdout:
    """A StreamReader stand-in that can raise the way a real one does."""

    def __init__(self, lines: list[bytes | Exception]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        item = self._lines.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _service(updates: list[dict]) -> SubprocessIpcService:
    return SubprocessIpcService(
        player_name="TestSpeaker",
        protocol_warning_cache=set(),
        status_updater=updates.append,
        log_methods={},
        allowed_keys=None,
    )


def _status(volume: int) -> bytes:
    import json

    return (json.dumps({"type": "status", "volume": volume}) + "\n").encode()


def test_an_oversized_line_does_not_kill_the_reader():
    """A StreamReader past its limit raises rather than returning the line."""
    updates: list[dict] = []
    service = _service(updates)
    stdout = _Stdout(
        [
            _status(10),
            ValueError("Separator is not found, and chunk exceed the limit"),
            _status(20),
        ]
    )

    asyncio.run(service.read_stream(stdout))

    assert [u["volume"] for u in updates] == [10, 20], "the reader stopped at the oversized line"


def test_an_idle_daemon_does_not_end_the_read():
    """A daemon that simply has nothing to say is not a dead daemon."""
    updates: list[dict] = []
    service = _service(updates)

    class _Slow(_Stdout):
        async def readline(self) -> bytes:
            if self._lines and not isinstance(self._lines[0], Exception):
                await asyncio.sleep(0)
            return await super().readline()

    idle_calls: list[int] = []

    async def _run():
        stdout = _Slow([TimeoutError(), _status(30)])
        await service.read_stream(
            stdout,
            idle_timeout=0.01,
            on_idle=lambda: idle_calls.append(1) or False,
        )

    asyncio.run(_run())

    assert [u["volume"] for u in updates] == [30]
    assert idle_calls, "the idle hook never fired"


def test_the_idle_hook_can_end_the_read():
    """A dead subprocess ends the loop; the hook is what knows the difference."""
    updates: list[dict] = []
    service = _service(updates)

    async def _run():
        stdout = _Stdout([TimeoutError(), _status(40)])
        await service.read_stream(stdout, idle_timeout=0.01, on_idle=lambda: True)

    asyncio.run(_run())

    assert updates == [], "the reader kept going after the hook said to stop"


def test_the_message_hook_sees_every_message():
    updates: list[dict] = []
    service = _service(updates)
    seen: list[dict] = []

    async def _on_message(msg):
        seen.append(msg)
        service.handle_message(msg)

    asyncio.run(service.read_stream(_Stdout([_status(50), _status(60)]), on_message=_on_message))

    assert [m["volume"] for m in seen] == [50, 60]
    assert [u["volume"] for u in updates] == [50, 60]


@pytest.mark.asyncio
async def test_a_dead_reader_takes_the_daemon_down():
    """Without this the daemon wedges on a pipe nobody drains."""
    from sendspin_bridge.bridge.client import SendspinClient

    client = SendspinClient.__new__(SendspinClient)
    client.player_name = "TestSpeaker"
    killed: list[str] = []

    class _Proc:
        returncode = None
        pid = 4242

        def kill(self):
            killed.append("kill")

    client._daemon_proc = _Proc()

    async def _boom():
        raise RuntimeError("reader exploded")

    task = asyncio.ensure_future(_boom())
    task.add_done_callback(client._make_reader_done_handler())
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert killed == ["kill"], "a dead reader left the daemon running"
