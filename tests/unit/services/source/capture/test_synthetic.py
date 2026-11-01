"""The synthetic capture: same frame shape as a real phone stream."""

from __future__ import annotations

from sendspin_bridge.services.source.capture import chunk_bytes
from sendspin_bridge.services.source.capture.synthetic import SyntheticBackend, SyntheticCapture


async def test_it_produces_whole_ten_millisecond_s16_frames():
    capture = SyntheticCapture()
    await capture.start()
    chunks = []
    async for chunk in capture.chunks():
        chunks.append(chunk)
        if len(chunks) == 3:
            break
    await capture.stop()

    assert [len(chunk) for chunk in chunks] == [chunk_bytes()] * 3
    assert any(chunk != b"\x00" * len(chunk) for chunk in chunks)


async def test_the_backend_reports_one_streaming_phone():
    backend = SyntheticBackend()
    endpoint = await backend.find()
    assert endpoint is not None
    assert endpoint.running is True
    assert endpoint.phone_mac == "00:00:00:00:00:01"

    stream = await backend.open(endpoint)
    await stream.start()
    assert stream.running is True
    await stream.stop()
    assert stream.running is False
