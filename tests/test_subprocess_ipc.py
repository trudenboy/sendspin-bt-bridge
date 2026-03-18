from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from services.ipc_protocol import IPC_PROTOCOL_VERSION
from services.subprocess_ipc import SubprocessIpcService


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        self._iter = iter(self._lines)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


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

    assert service.handle_message({"type": "log", "level": "error", "msg": "daemon crashed"}) is None

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
