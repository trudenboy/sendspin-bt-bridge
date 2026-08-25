"""A click train must not be orphaned when the sink name moves.

The metronome owns a player process.  Rebuilding it because the device
reconnected on a differently-named sink dropped the only reference to the
running one, so its player kept clicking with nothing left able to stop it —
and the next start added a second player on top.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bridge.client import SendspinClient


class _FakeStdin:
    """A pipe the feed task can keep writing to, so the train stays alive."""

    def __init__(self):
        self.closed = False

    def write(self, _data: bytes) -> None:
        if self.closed:
            raise BrokenPipeError("pipe closed")

    async def drain(self) -> None:
        await asyncio.sleep(0.01)

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.stdin = _FakeStdin()
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.terminated = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.mark.asyncio
async def test_a_sink_change_stops_the_old_train_before_starting_a_new_one(monkeypatch):
    client = SendspinClient("Test Player", "localhost", 9000)
    client.bluetooth_sink_name = "bluez_sink.AA.a2dp_sink"
    client.status["bluetooth_connected"] = True

    spawned: list[_FakeProc] = []

    async def _spawn(_args):
        proc = _FakeProc()
        spawned.append(proc)
        return proc

    real_metronome = client._metronome_for_current_sink

    async def _injected():
        metronome = await real_metronome()
        metronome._spawn = _spawn
        return metronome

    monkeypatch.setattr(client, "_metronome_for_current_sink", _injected)

    assert await client.start_calibration_metronome() is True
    first = client._calibration_metronome
    await asyncio.sleep(0)

    client.bluetooth_sink_name = "bluez_sink.BB.a2dp_sink"
    assert await client.start_calibration_metronome() is True

    assert client._calibration_metronome is not first
    assert spawned[0].terminated, "the train on the old sink was left running"
    assert first is not None and not first.active
