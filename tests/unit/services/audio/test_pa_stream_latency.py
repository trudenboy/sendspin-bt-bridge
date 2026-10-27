from types import SimpleNamespace

import pytest

from sendspin_bridge.services.audio.pa_stream_latency import read_own_stream_latency_ms


class FakePulse:
    def __init__(self, inputs):
        self._inputs = inputs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def sink_input_list(self):
        return self._inputs


def si(pid, buf, sink):
    return SimpleNamespace(proplist={"application.process.id": str(pid)}, buffer_usec=buf, sink_usec=sink)


@pytest.mark.asyncio
async def test_sums_buffer_and_sink_latency_of_own_stream():
    ms = await read_own_stream_latency_ms(
        1234, pulse_factory=lambda: FakePulse([si(99, 1, 1), si(1234, 560_000, 27_000)])
    )
    assert ms == pytest.approx(587.0)


@pytest.mark.asyncio
async def test_max_when_several_own_streams():
    ms = await read_own_stream_latency_ms(
        1234, pulse_factory=lambda: FakePulse([si(1234, 100_000, 0), si(1234, 300_000, 20_000)])
    )
    assert ms == pytest.approx(320.0)


@pytest.mark.asyncio
async def test_none_without_own_stream():
    assert await read_own_stream_latency_ms(1234, pulse_factory=lambda: FakePulse([si(99, 1, 1)])) is None


@pytest.mark.asyncio
async def test_none_when_pulse_is_unreachable():
    def boom():
        raise OSError("no server")

    assert await read_own_stream_latency_ms(1234, pulse_factory=boom) is None
