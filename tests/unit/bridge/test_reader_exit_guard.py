"""A stdout reader that ends leaves nobody draining the daemon.

The daemon writes its status with a blocking `print`. Once the 64 KB pipe
fills, its event loop wedges: no commands, no stop, no reconnect — while the
audio thread keeps playing and the speaker looks healthy from the outside.

The bridge already killed the daemon when its reader raised. It did not when
the reader simply *returned* — and the reader has an early return, for the
case where the process handle is gone by the time the task first runs. Seen on
the stand: a daemon alive for eleven minutes, listening on its port, blocked
in `anon_pipe_write`, with nothing in the log.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bridge.client import SendspinClient


class _LiveProc:
    """A daemon that is still running — returncode stays None."""

    def __init__(self) -> None:
        self.pid = 4242
        self.returncode = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _client() -> SendspinClient:
    return SendspinClient("Test Player", "localhost", 9000)


@pytest.mark.asyncio
async def test_a_reader_that_returns_quietly_still_frees_the_daemon():
    client = _client()
    proc = _LiveProc()
    client._daemon_proc = proc

    async def _reader_that_gives_up():
        return None

    task = asyncio.ensure_future(_reader_that_gives_up())
    task.add_done_callback(client._make_reader_done_handler())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert proc.killed, "the daemon was left writing into a pipe nobody reads"


@pytest.mark.asyncio
async def test_a_reader_that_raises_still_frees_the_daemon():
    client = _client()
    proc = _LiveProc()
    client._daemon_proc = proc

    async def _reader_that_breaks():
        raise RuntimeError("stdout decode failed")

    task = asyncio.ensure_future(_reader_that_breaks())
    task.add_done_callback(client._make_reader_done_handler())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert proc.killed


@pytest.mark.asyncio
async def test_a_daemon_that_has_already_exited_is_left_alone():
    """The ordinary end of a reader: the process it was reading is gone."""
    client = _client()
    proc = _LiveProc()
    proc.returncode = 0
    client._daemon_proc = proc

    async def _reader():
        return None

    task = asyncio.ensure_future(_reader())
    task.add_done_callback(client._make_reader_done_handler())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert not proc.killed


@pytest.mark.asyncio
async def test_a_cancelled_reader_is_the_bridge_stopping_it():
    client = _client()
    proc = _LiveProc()
    client._daemon_proc = proc

    async def _reader():
        await asyncio.sleep(10)

    task = asyncio.ensure_future(_reader())
    task.add_done_callback(client._make_reader_done_handler())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert not proc.killed, "a stop cancelled the reader; the stop path owns the daemon"
