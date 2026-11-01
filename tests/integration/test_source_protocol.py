"""The source client against a real aiosendspin server: pairing, line_sense and PCM.

The capture backend is faked (no Bluetooth in CI); everything downstream of it is the real
``aiosendspin`` protocol code, so a library upgrade that changes the source role fails here.
"""

from __future__ import annotations

import asyncio
import math
import struct
from typing import TYPE_CHECKING, Any

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from aiosendspin.models.types import PairMethod
from aiosendspin.noise.keys import Identity
from aiosendspin.noise.pairing import PairingAttempt
from aiosendspin.noise.trust_store import InMemoryServerPairingStore
from aiosendspin.server.roles.source.events import (
    SourceSignalChangedEvent,
    SourceStreamEndedEvent,
    SourceStreamStartedEvent,
)
from aiosendspin.server.server import SendspinServer

from sendspin_bridge.services.source import client as client_module
from sendspin_bridge.services.source import state as source_state
from sendspin_bridge.services.source.capture import CaptureEndpoint, chunk_bytes

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

PAIRING_CODE = "12345678"


class FeedOnceStream:
    """A capture stream that yields a fixed PCM block once, then stays open."""

    def __init__(self, pcm: bytes) -> None:
        self._pcm = pcm
        self._sent = False
        self.started = False
        self.stopped = False

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    async def start(self) -> None:
        self.started = True

    async def chunks(self) -> AsyncIterator[bytes]:
        while not self.stopped:
            if self._sent:
                await asyncio.sleep(0.01)
                continue
            self._sent = True
            for offset in range(0, len(self._pcm), chunk_bytes()):
                yield self._pcm[offset : offset + chunk_bytes()]

    async def stop(self) -> None:
        self.stopped = True


class FakeBackend:
    name = "synthetic"

    def __init__(self, stream: FeedOnceStream) -> None:
        self._stream = stream
        self.present = True

    async def find(self) -> CaptureEndpoint | None:
        if not self.present:
            return None
        return CaptureEndpoint(node="synthetic.phone", backend="synthetic", running=True)

    async def open(self, _endpoint: CaptureEndpoint) -> FeedOnceStream:
        return self._stream

    async def ensure_a2dp_source(self, _mac: str | None = None) -> bool:
        return True


def sine_pcm(samples: int, channels: int = 2, rate: int = 48000) -> bytes:
    out = bytearray()
    for index in range(samples):
        value = int(4000 * math.sin(2 * math.pi * 440 * index / rate))
        out += struct.pack("<h", value) * channels
    return bytes(out)


async def wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def server() -> AsyncIterator[tuple[SendspinServer, str]]:
    sendspin = SendspinServer(
        loop=asyncio.get_running_loop(),
        identity=Identity.generate(),
        server_name="test-server",
        pairing_store=InMemoryServerPairingStore(),
    )
    app = web.Application()
    app.router.add_get(SendspinServer.API_PATH, sendspin.on_client_connect)
    test_server = TestServer(app)
    await test_server.start_server()
    try:
        yield sendspin, f"ws://127.0.0.1:{test_server.port}{SendspinServer.API_PATH}"
    finally:
        await test_server.close()
        await sendspin.close()


async def test_pair_then_stream_pcm_and_line_sense(server, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(client_module, "WATCH_INTERVAL_S", 0.01)
    monkeypatch.setattr(client_module, "PAIRING_POLL_S", 0.01)
    source_state.reset()

    sendspin, url = server
    pcm = sine_pcm(5 * 480)  # five 10 ms chunks, 48 kHz stereo
    stream = FeedOnceStream(pcm)
    backend = FakeBackend(stream)
    settings = client_module.SourceSettings(server_url=url, state_dir=tmp_path / "state")
    source = client_module.SourceClient(settings, backend)

    await source.start()
    try:
        await wait_for(lambda: source_state.snapshot().ma_connected)
        await wait_for(lambda: source_state.snapshot().ma_pairing_code is not None)
        peer_id = source._identity.peer_id

        code = source_state.snapshot().ma_pairing_code
        assert code is not None

        async def code_provider() -> str:
            return code

        await sendspin.initiate_pairing(
            peer_id,
            PairingAttempt(method=PairMethod.STATIC_PAIRING_CODE, pairing_code_provider=code_provider),
        )
        await wait_for(lambda: source_state.snapshot().ma_paired, timeout=10.0)

        client = source._client
        await wait_for(lambda: client is not None and client.is_time_synchronized(), timeout=10.0)
        server_client = sendspin.get_client(peer_id)
        assert server_client is not None
        events: list[Any] = []
        server_client.add_event_listener(lambda _client, event: events.append(event))
        await wait_for(lambda: "source@v1" in server_client.active_role_ids)

        # Toggle presence after the listener is attached: an event sent earlier may predate
        # it, and the server dedupes an unchanged signal.
        backend.present = False
        await wait_for(lambda: any(isinstance(event, SourceSignalChangedEvent) for event in events))
        assert next(event for event in events if isinstance(event, SourceSignalChangedEvent)).signal.value == "absent"
        backend.present = True
        await wait_for(
            lambda: any(
                isinstance(event, SourceSignalChangedEvent) and event.signal.value == "present" for event in events
            )
        )

        role = server_client.role("source@v1")
        assert role is not None
        role.request_start()
        await wait_for(lambda: any(isinstance(event, SourceStreamStartedEvent) for event in events))
        handle = next(event for event in events if isinstance(event, SourceStreamStartedEvent)).handle

        received = bytearray()

        async def drain() -> None:
            async for chunk, _timestamp in handle:
                received.extend(chunk)
                if len(received) >= len(pcm):
                    return

        await asyncio.wait_for(drain(), timeout=10.0)
        assert bytes(received[: len(pcm)]) == pcm

        role.request_stop()
        await wait_for(lambda: any(isinstance(event, SourceStreamEndedEvent) for event in events))
    finally:
        await source.stop()
