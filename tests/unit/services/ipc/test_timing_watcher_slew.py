import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.services.ipc import daemon_process


class _FakeSlew:
    def __init__(self):
        self.samples = 0

    def sample(self):
        self.samples += 1

    def ppm(self):
        return 12.345 if self.samples >= 2 else None


@pytest.mark.asyncio
async def test_clock_slew_ppm_reaches_status_after_two_ticks(monkeypatch):
    import sendspin_bridge.services.audio.timing_telemetry as telemetry

    fake = _FakeSlew()
    monkeypatch.setattr(daemon_process, "_make_slew_estimator", lambda: fake)
    monkeypatch.setattr(telemetry, "collect_timing_snapshot", lambda _a, _c: {"timing_metrics_available": True})
    status: dict = {"playing": False}
    stop_event = asyncio.Event()
    daemon = SimpleNamespace(_audio_handler=object(), _client=object())
    watcher = asyncio.create_task(
        daemon_process._timing_telemetry_watcher(daemon, status, MagicMock(), stop_event, poll_interval=0.01)
    )
    try:
        for _ in range(100):
            if "clock_slew_ppm" in status:
                break
            await asyncio.sleep(0.01)
    finally:
        stop_event.set()
        await watcher
    assert fake.samples >= 2
    assert status["clock_slew_ppm"] == 12.3
