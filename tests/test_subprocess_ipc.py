from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION, build_log_envelope
from sendspin_bridge.services.ipc.subprocess_ipc import SubprocessIpcService


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)
        self._index = 0

    def __aiter__(self):
        self._iter = iter(self._lines)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def readline(self):
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line


def test_handle_message_updates_status_and_warns_once_for_mismatched_protocol():
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessIpcService(
        player_name="Kitchen",
        protocol_warning_cache=set(),
        status_updater=updates.append,
        logger_=logger,
        allowed_keys=frozenset({"playing", "volume"}),
    )

    msg = {"type": "status", "protocol_version": 999, "playing": True, "volume": 55, "ignored": "x"}
    assert service.handle_message(msg) == {"playing": True, "volume": 55}
    assert service.handle_message(msg) == {"playing": True, "volume": 55}

    assert updates == [{"playing": True, "volume": 55}, {"playing": True, "volume": 55}]
    logger.warning.assert_called_once()


def test_handle_message_logs_proc_messages():
    logger = Mock()
    service = SubprocessIpcService(
        player_name="Kitchen",
        protocol_warning_cache=set(),
        status_updater=lambda _updates: None,
        logger_=logger,
    )

    assert service.handle_message(build_log_envelope(level="error", msg="daemon crashed")) is None

    logger.error.assert_called_once()


def test_handle_message_updates_last_error_from_error_envelope():
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessIpcService(
        player_name="Kitchen",
        protocol_warning_cache=set(),
        status_updater=updates.append,
        logger_=logger,
    )

    returned = service.handle_message(
        {
            "type": "error",
            "protocol_version": IPC_PROTOCOL_VERSION,
            "error_code": "audio_output_missing",
            "message": "No audio output device found",
            "details": {"at": "2026-03-18T09:10:00+00:00"},
        }
    )

    assert returned == {
        "last_error": "No audio output device found",
        "last_error_at": "2026-03-18T09:10:00+00:00",
    }
    assert updates == [returned]
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_read_stream_parses_json_lines_and_ignores_invalid_lines():
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessIpcService(
        player_name="Kitchen",
        protocol_warning_cache=set(),
        status_updater=updates.append,
        logger_=logger,
        allowed_keys=frozenset({"playing"}),
    )

    await service.read_stream(
        _FakeStdout(
            [
                b"not-json\n",
                json.dumps({"type": "status", "protocol_version": IPC_PROTOCOL_VERSION, "playing": True}).encode(),
            ]
        )
    )

    assert updates == [{"playing": True}]


def test_parse_line_ignores_json_arrays():
    service = SubprocessIpcService(
        player_name="Kitchen",
        protocol_warning_cache=set(),
        status_updater=lambda _updates: None,
    )

    assert service.parse_line(b"[1, 2, 3]\n") is None


def test_handle_message_defaults_invalid_error_details_to_empty_dict():
    updates: list[dict] = []
    logger = Mock()
    service = SubprocessIpcService(
        player_name="Kitchen",
        protocol_warning_cache=set(),
        status_updater=updates.append,
        logger_=logger,
    )

    returned = service.handle_message(
        {
            "type": "error",
            "protocol_version": IPC_PROTOCOL_VERSION,
            "error_code": "runtime_error",
            "message": "boom",
            "details": "not-a-dict",
        }
    )

    assert returned == {"last_error": "boom", "last_error_at": None}
    assert updates == [returned]
    logger.error.assert_called_once()
