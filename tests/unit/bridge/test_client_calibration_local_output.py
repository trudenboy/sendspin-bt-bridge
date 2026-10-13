"""Calibration tone and metronome must work on local audio outputs.

A ``pulse_sink`` player is never "Bluetooth connected"; it has an output
that is either present or reported missing.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.config.players import PULSE_SINK


class _FakeStdin:
    def write(self, _data: bytes) -> None:
        pass

    async def drain(self) -> None:
        await asyncio.sleep(0.01)

    def close(self) -> None:
        pass


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.stdin = _FakeStdin()

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode

    async def communicate(self, _data=None):
        self.returncode = 0
        return b"", b""


def _local_client(*, sink_missing: bool) -> SendspinClient:
    client = SendspinClient("Local Out", "localhost", 9000)
    client.backend_type = PULSE_SINK
    client.bluetooth_sink_name = "alsa_output.usb-dac"
    client.status["bluetooth_connected"] = False
    client.status["sink_missing"] = sink_missing
    return client


@pytest.mark.asyncio
async def test_tone_plays_on_a_present_local_output(monkeypatch):
    client = _local_client(sink_missing=False)

    async def _exec(*_a, **_k):
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    assert await client.play_calibration_tone() is True


@pytest.mark.asyncio
async def test_tone_refused_when_local_output_is_missing():
    assert await _local_client(sink_missing=True).play_calibration_tone() is False


@pytest.mark.asyncio
async def test_metronome_starts_on_a_present_local_output(monkeypatch):
    client = _local_client(sink_missing=False)
    real = client._metronome_for_current_sink

    async def _spawn(_args):
        return _FakeProc()

    async def _injected():
        metronome = await real()
        metronome._spawn = _spawn
        return metronome

    monkeypatch.setattr(client, "_metronome_for_current_sink", _injected)
    assert await client.start_calibration_metronome() is True
    await client.stop_calibration_metronome()


@pytest.mark.asyncio
async def test_metronome_refused_when_local_output_is_missing():
    assert await _local_client(sink_missing=True).start_calibration_metronome() is False


@pytest.mark.asyncio
async def test_bluetooth_still_requires_a_connection():
    client = SendspinClient("BT", "localhost", 9000)
    client.bluetooth_sink_name = "bluez_sink.AA.a2dp_sink"
    client.status["bluetooth_connected"] = False
    assert await client.play_calibration_tone() is False
    assert await client.start_calibration_metronome() is False
