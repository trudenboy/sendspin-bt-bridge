import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.services.ipc import daemon_process


class _RaisingSlew:
    def sample(self):
        raise RuntimeError("clock broke")

    def ppm(self):
        return None


async def _run_until(status, key, daemon):
    stop_event = asyncio.Event()
    watcher = asyncio.create_task(
        daemon_process._timing_telemetry_watcher(daemon, status, MagicMock(), stop_event, poll_interval=0.01)
    )
    try:
        for _ in range(200):
            if key in status:
                break
            await asyncio.sleep(0.01)
    finally:
        stop_event.set()
        await watcher


def _patch_common(monkeypatch, slew):
    import sendspin_bridge.services.audio.timing_telemetry as telemetry

    monkeypatch.setattr(daemon_process, "_make_slew_estimator", lambda: slew)
    monkeypatch.setattr(telemetry, "collect_timing_snapshot", lambda _a, _c: {"timing_metrics_available": True})


@pytest.mark.asyncio
async def test_pa_latency_reaches_status_while_playing(monkeypatch):
    _patch_common(monkeypatch, SimpleNamespace(sample=lambda: None, ppm=lambda: None))

    async def fake_read():
        return 587.04

    monkeypatch.setattr(daemon_process, "_read_pa_latency_ms", fake_read)
    status: dict = {"playing": True}
    await _run_until(status, "pa_latency_ms", SimpleNamespace(_audio_handler=object(), _client=object()))
    assert status["pa_latency_ms"] == 587.0


@pytest.mark.asyncio
async def test_pa_latency_not_read_when_not_playing(monkeypatch):
    _patch_common(monkeypatch, SimpleNamespace(sample=lambda: None, ppm=lambda: None))
    calls = []

    async def fake_read():
        calls.append(1)
        return 1.0

    monkeypatch.setattr(daemon_process, "_read_pa_latency_ms", fake_read)
    status: dict = {"playing": False}
    await _run_until(status, "timing_metrics_available", SimpleNamespace(_audio_handler=object(), _client=object()))
    assert calls == []


@pytest.mark.asyncio
async def test_failing_slew_does_not_drop_other_telemetry(monkeypatch):
    _patch_common(monkeypatch, _RaisingSlew())

    async def fake_read():
        return 100.0

    monkeypatch.setattr(daemon_process, "_read_pa_latency_ms", fake_read)
    status: dict = {"playing": True}
    await _run_until(status, "pa_latency_ms", SimpleNamespace(_audio_handler=object(), _client=object()))
    assert status["timing_metrics_available"] is True
    assert status["pa_latency_ms"] == 100.0
    assert "clock_slew_ppm" not in status


@pytest.mark.asyncio
async def test_failing_pa_read_does_not_drop_other_telemetry(monkeypatch):
    _patch_common(monkeypatch, SimpleNamespace(sample=lambda: None, ppm=lambda: 5.0))

    async def fake_read():
        raise RuntimeError("pulse died")

    monkeypatch.setattr(daemon_process, "_read_pa_latency_ms", fake_read)
    status: dict = {"playing": True}
    await _run_until(status, "clock_slew_ppm", SimpleNamespace(_audio_handler=object(), _client=object()))
    assert status["timing_metrics_available"] is True
    assert status["clock_slew_ppm"] == 5.0
    assert status.get("pa_latency_ms") is None


@pytest.mark.asyncio
async def test_a_hanging_pa_read_times_out_and_does_not_block_the_watcher(monkeypatch, caplog):
    import logging

    _patch_common(monkeypatch, SimpleNamespace(sample=lambda: None, ppm=lambda: None))
    assert daemon_process._PA_LATENCY_TIMEOUT_S == 0.5
    monkeypatch.setattr(daemon_process, "_PA_LATENCY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(daemon_process, "_pa_latency_timeout_logged", False)

    async def hanging_read():
        await asyncio.sleep(3600)

    monkeypatch.setattr(daemon_process, "_read_pa_latency_ms", hanging_read)
    caplog.set_level(logging.DEBUG, logger=daemon_process.__name__)
    for _ in range(2):
        status: dict = {"playing": True}
        loop = asyncio.get_running_loop()
        began = loop.time()
        await _run_until(status, "timing_metrics_available", SimpleNamespace(_audio_handler=object(), _client=object()))
        assert status.get("timing_metrics_available") is True
        assert loop.time() - began < 1.0
        assert status.get("pa_latency_ms") is None
    assert caplog.text.count("PulseAudio latency read timed out") == 1


@pytest.mark.asyncio
async def test_off_mode_sends_pa_latency_with_the_metrics_tick_not_every_second(monkeypatch):
    _patch_common(monkeypatch, SimpleNamespace(sample=lambda: None, ppm=lambda: None))
    reads: list[int] = []

    async def fake_read():
        reads.append(1)
        return 600.0

    monkeypatch.setattr(daemon_process, "_read_pa_latency_ms", fake_read)
    status: dict = {"playing": True}
    changes = MagicMock()
    stop_event = asyncio.Event()
    daemon = SimpleNamespace(_audio_handler=object(), _client=object(), _args=SimpleNamespace(playout_mode="off"))
    watcher = asyncio.create_task(
        daemon_process._timing_telemetry_watcher(daemon, status, changes, stop_event, poll_interval=0.01)
    )
    await asyncio.sleep(2.3)
    stop_event.set()
    await watcher

    assert status["pa_latency_ms"] == 600.0
    assert reads == [1]
    assert changes.call_count == 1
