"""The Music Assistant websocket monitor must start when credentials arrive late.

The monitor is created during startup only.  A token pasted (or an account
signed in) afterwards reached a reload helper that found no monitor and did
nothing, so the header kept saying "not connected" until a restart.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

_URL = "http://192.168.10.10:8095"
_TOKEN = "eyJhbGciOi.test"


@pytest.fixture(autouse=True)
def _clean_monitor_singleton(monkeypatch):
    from sendspin_bridge.services.music_assistant import ma_monitor

    monkeypatch.setattr(ma_monitor, "_monitor_instance", None)
    monkeypatch.setattr(ma_monitor, "_monitor_task", None)
    yield
    ma_monitor.stop_monitor()


def test_it_starts_the_monitor_when_the_bridge_booted_without_one(monkeypatch):
    from sendspin_bridge.services.music_assistant import ma_monitor

    started: list[tuple[str, str]] = []
    ran = MagicMock()

    class _FakeMonitor:
        async def run(self) -> None:
            ran()

    def _start(ma_url: str, ma_token: str) -> _FakeMonitor:
        started.append((ma_url, ma_token))
        return _FakeMonitor()

    monkeypatch.setattr(ma_monitor, "start_monitor", _start)

    loop = asyncio.new_event_loop()
    try:
        assert ma_monitor.ensure_monitor_started(loop, _URL, _TOKEN) is True
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        loop.close()

    assert started == [(_URL, _TOKEN)]
    ran.assert_called_once()


def test_it_reloads_the_credentials_of_a_running_monitor(monkeypatch):
    from sendspin_bridge.services.music_assistant import ma_monitor

    monitor = MagicMock()
    monitor._loop = None
    monitor.reload_credentials = AsyncMock()
    monkeypatch.setattr(ma_monitor, "_monitor_instance", monitor)

    loop = asyncio.new_event_loop()
    try:
        assert ma_monitor.ensure_monitor_started(loop, _URL, "new-token") is True
        loop.run_until_complete(asyncio.sleep(0.05))
    finally:
        loop.close()

    monitor.reload_credentials.assert_awaited_once_with(_URL, "new-token")


def test_without_a_loop_it_reports_failure_instead_of_dropping_the_work(monkeypatch):
    from sendspin_bridge.services.music_assistant import ma_monitor

    start = MagicMock()
    monkeypatch.setattr(ma_monitor, "start_monitor", start)

    assert ma_monitor.ensure_monitor_started(None, _URL, _TOKEN) is False
    start.assert_not_called()
