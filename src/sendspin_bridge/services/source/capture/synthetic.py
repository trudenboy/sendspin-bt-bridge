"""A synthetic capture: a steady tone in the same shape as a real phone stream.

The demo stand and the protocol tests need a phone that never disconnects and never needs
Bluetooth hardware.  This backend produces the exact s16le 48 kHz stereo frames the real
backends produce, so everything downstream (feed, timestamps, Sendspin) runs unchanged.
"""

from __future__ import annotations

import asyncio
import math
import struct
from typing import TYPE_CHECKING

from sendspin_bridge.services.source.capture import (
    CHANNELS,
    CHUNK_MS,
    SAMPLE_RATE,
    CaptureEndpoint,
    CaptureStream,
    chunk_bytes,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["SyntheticBackend", "SyntheticCapture"]


class SyntheticCapture:
    """A 440 Hz tone as whole s16 frames, paced in real time."""

    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        chunk_ms: int = CHUNK_MS,
        tone_hz: float = 440.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_ms = chunk_ms
        self._tone_hz = tone_hz
        self._size = chunk_bytes(sample_rate, channels, chunk_ms)
        self._started = False
        self._next_at = 0.0
        self._phase = 0.0

    @property
    def running(self) -> bool:
        return self._started

    async def start(self) -> None:
        self._started = True
        self._next_at = asyncio.get_running_loop().time()

    def _chunk(self) -> bytes:
        frames = self._size // (2 * self._channels)
        samples = bytearray()
        for _ in range(frames):
            value = int(6000 * math.sin(self._phase))
            self._phase += 2 * math.pi * self._tone_hz / self._sample_rate
            sample = struct.pack("<h", value)
            samples += sample * self._channels
        return bytes(samples)

    async def chunks(self) -> AsyncIterator[bytes]:
        while self._started:
            loop = asyncio.get_running_loop()
            delay = self._next_at - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_at += self._chunk_ms / 1000
            yield self._chunk()

    async def stop(self) -> None:
        self._started = False


class SyntheticBackend:
    """A backend that always reports one streaming phone."""

    name = "synthetic"

    def __init__(self, *, running: bool = True, tone_hz: float = 440.0) -> None:
        self._running = running
        self._tone_hz = tone_hz
        self._stream: SyntheticCapture | None = None

    async def find(self) -> CaptureEndpoint | None:
        if not self._running:
            return None
        return CaptureEndpoint(
            node="synthetic.phone",
            backend=self.name,
            phone_mac="00:00:00:00:00:01",
            running=True,
        )

    async def open(self, endpoint: CaptureEndpoint) -> CaptureStream:
        del endpoint
        self._stream = SyntheticCapture(tone_hz=self._tone_hz)
        return self._stream
