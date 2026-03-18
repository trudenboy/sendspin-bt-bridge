from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from services.subprocess_stderr import SubprocessStderrService

UTC = timezone.utc


class _FakeStderr:
    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


def test_handle_line_sets_last_error_for_crash_output():
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
        now_factory=lambda: datetime(2026, 3, 18, 8, 30, tzinfo=UTC),
    )

    service.handle_line("TypeError: unexpected keyword argument 'use_hardware_volume'")

    assert updates == [
        {
            "last_error": "TypeError: unexpected keyword argument 'use_hardware_volume'",
            "last_error_at": "2026-03-18T08:30:00+00:00",
        }
    ]
    logger.error.assert_called_once()


def test_handle_line_keeps_benign_output_as_warning():
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
    )

    service.handle_line("ALSA lib pcm.c:2666: Unknown PCM default")

    assert updates == []
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_read_stream_processes_until_eof():
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
        now_factory=lambda: datetime(2026, 3, 18, 8, 31, tzinfo=UTC),
    )

    await service.read_stream(
        _FakeStderr(
            [
                b"ALSA lib pcm.c:2666: Unknown PCM default\n",
                b"fatal: daemon crashed\n",
            ]
        )
    )

    assert updates == [
        {
            "last_error": "fatal: daemon crashed",
            "last_error_at": "2026-03-18T08:31:00+00:00",
        }
    ]
    logger.warning.assert_called_once()
    logger.critical.assert_called_once()
