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


def test_handle_line_surfaces_repeated_connection_errors():
    """Repeated ClientConnectorError lines should surface as last_error."""
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
        now_factory=lambda: datetime(2026, 4, 6, 15, 0, tzinfo=UTC),
    )

    # First two are warnings, third triggers error surfacing
    service.handle_line("Connection error (ClientConnectorError), retrying in 1s")
    service.handle_line("Connection error (ClientConnectorError), retrying in 2s")
    service.handle_line("Connection error (ClientConnectorError), retrying in 4s")

    assert any("Cannot connect" in u.get("last_error", "") for u in updates)


def test_handle_line_detects_errno_98_sets_port_collision():
    """stderr line with 'errno 98' should flag port_collision and include an lsof hint."""
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
        now_factory=lambda: datetime(2026, 4, 6, 15, 0, tzinfo=UTC),
    )

    service.handle_line("OSError: [Errno 98] Address already in use")

    assert len(updates) == 1
    update = updates[0]
    assert update["port_collision"] is True
    assert "lsof" in update["last_error"]
    assert update["last_error_at"] == "2026-04-06T15:00:00+00:00"
    logger.error.assert_called_once()


def test_handle_line_detects_address_already_in_use_phrase():
    """Phrase 'address already in use' (no errno) still triggers port_collision."""
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
    )

    service.handle_line("RuntimeError: address already in use")

    assert len(updates) == 1
    assert updates[0]["port_collision"] is True


def test_handle_line_port_collision_extracts_port_number():
    """Port number from 'host:port' phrase should appear in the surfaced hint."""
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
    )

    service.handle_line("bind 0.0.0.0:8928 address already in use")

    assert len(updates) == 1
    assert "8928" in updates[0]["last_error"]
    assert "lsof -i :8928" in updates[0]["last_error"]


def test_handle_line_port_collision_extracts_low_port_number():
    """Low-range ports (1-3 digits) from 'host:port' phrase still land in the hint."""
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
    )

    service.handle_line("bind 0.0.0.0:80 address already in use")
    service.handle_line("bind 0.0.0.0:443 address already in use")

    assert len(updates) == 2
    assert "lsof -i :80" in updates[0]["last_error"]
    assert "lsof -i :443" in updates[1]["last_error"]


def test_handle_line_port_collision_rejects_out_of_range_number():
    """Numbers > 65535 must not be accepted as ports; fall back to generic hint."""
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
    )

    service.handle_line("bind :99999 address already in use")

    assert len(updates) == 1
    # Generic hint (no specific port in "lsof -i :<n>")
    assert "lsof -i :<port>" in updates[0]["last_error"]
    assert "99999" not in updates[0]["last_error"]


def test_handle_line_connection_error_counter_resets_on_other_line():
    """Non-connection-error lines reset the consecutive counter."""
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessStderrService(
        player_name="Kitchen",
        update_status=updates.append,
        logger_=logger,
        now_factory=lambda: datetime(2026, 4, 6, 15, 0, tzinfo=UTC),
    )

    service.handle_line("Connection error (ClientConnectorError), retrying in 1s")
    service.handle_line("Connection error (ClientConnectorError), retrying in 2s")
    service.handle_line("ALSA lib pcm.c:2666: Unknown PCM default")  # resets counter
    service.handle_line("Connection error (ClientConnectorError), retrying in 4s")

    # Should NOT have surfaced because the counter was reset
    assert not any("Cannot connect" in u.get("last_error", "") for u in updates)
