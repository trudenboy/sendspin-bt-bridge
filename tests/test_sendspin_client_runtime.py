"""Focused runtime tests for SendspinClient edge cases."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import state
from sendspin_client import SendspinClient, _filter_duplicate_bluetooth_devices
from services.ipc_protocol import IPC_PROTOCOL_VERSION
from services.log_analysis import classify_subprocess_stderr_level


class _RaceyStdin:
    """Fake stdin that clears the client's proc during write to simulate TOCTOU."""

    def __init__(self, client: SendspinClient):
        self.client = client
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)
        self.client._daemon_proc = None

    async def drain(self) -> None:
        await asyncio.sleep(0)


class _FakeProc:
    def __init__(self, stdin: _RaceyStdin):
        self.stdin = stdin
        self.returncode = None


@pytest.mark.asyncio
async def test_send_subprocess_command_uses_snapshot_when_proc_changes():
    """Command send should survive proc mutation between guard and drain()."""
    client = SendspinClient("Test Player", "localhost", 9000)
    stdin = _RaceyStdin(client)
    client._daemon_proc = _FakeProc(stdin)

    await client._send_subprocess_command({"cmd": "stop"})

    assert stdin.writes == [f'{{"cmd": "stop", "protocol_version": {IPC_PROTOCOL_VERSION}}}\n'.encode()]


@pytest.mark.asyncio
async def test_read_subprocess_output_accepts_protocol_versioned_status_once():
    client = SendspinClient("Test Player", "localhost", 9000)

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

    client._daemon_proc = SimpleNamespace(
        stdout=_FakeStdout(
            [
                json.dumps({"type": "status", "protocol_version": IPC_PROTOCOL_VERSION, "playing": True}).encode(),
            ]
        )
    )

    await client._read_subprocess_output()

    assert client.status.playing is True


@pytest.mark.asyncio
async def test_read_subprocess_stderr_delegates_to_stderr_service():
    client = SendspinClient("Test Player", "localhost", 9000)
    fake_stderr = object()
    client._daemon_proc = SimpleNamespace(stderr=fake_stderr)

    class _FakeService:
        def __init__(self):
            self.stderr = None

        async def read_stream(self, stderr):
            self.stderr = stderr

    fake_service = _FakeService()
    client._stderr_service = fake_service

    await client._read_subprocess_stderr()

    assert fake_service.stderr is fake_stderr


def test_filter_duplicate_bluetooth_devices_keeps_first_mac():
    devices = [
        {"mac": "aa:bb:cc:dd:ee:ff", "player_name": "Kitchen"},
        {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen Copy"},
        {"mac": "11:22:33:44:55:66", "player_name": "Office"},
    ]

    filtered = _filter_duplicate_bluetooth_devices(devices)

    assert filtered == [
        {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen"},
        {"mac": "11:22:33:44:55:66", "player_name": "Office"},
    ]


def test_update_status_resets_stream_tracking_when_playback_stops():
    client = SendspinClient("Test Player", "localhost", 9000)

    with patch("sendspin_client._state.notify_status_changed"):
        client._update_status({"playing": True})
        client._update_status({"audio_streaming": True})

        assert client._has_streamed is True

        client._update_status({"playing": False})

    assert client._has_streamed is False
    assert client._playing_since is None


def test_update_status_records_structured_device_events():
    client = SendspinClient("Test Player", "localhost", 9000)
    state.clear_device_events(client.player_id)

    try:
        with patch("sendspin_client._state.notify_status_changed"):
            client._update_status({"bluetooth_connected": True, "server_connected": True})
            client._update_status({"playing": True})
            client._update_status({"audio_streaming": True})
            client._update_status({"last_error": "Route degraded", "last_error_at": "2026-03-18T00:00:00+00:00"})

        events = state.get_device_events(client.player_id)
        assert [event["event_type"] for event in events[:5]] == [
            "runtime-error",
            "audio-streaming",
            "playback-started",
            "daemon-connected",
            "bluetooth-connected",
        ]
        assert events[0]["level"] == "error"
        assert events[0]["message"] == "Route degraded"
    finally:
        state.clear_device_events(client.player_id)


def test_zombie_watchdog_triggers_after_second_play_without_audio():
    client = SendspinClient("Test Player", "localhost", 9000)
    client._daemon_proc = SimpleNamespace(returncode=None)

    with patch("sendspin_client._state.notify_status_changed"):
        client._update_status({"playing": True})
        client._update_status({"audio_streaming": True})
        client._update_status({"playing": False})
        client._update_status({"playing": True, "audio_streaming": False})
    client._playing_since = 100.0

    scheduled = []
    with (
        patch("sendspin_client.time.monotonic", return_value=116.0),
        patch("sendspin_client.asyncio.create_task", side_effect=lambda coro: scheduled.append(coro)),
    ):
        client._check_zombie_playback()

    assert len(scheduled) == 1
    scheduled[0].close()


def test_classify_subprocess_stderr_level_promotes_traceback_and_fatal_lines():
    assert classify_subprocess_stderr_level("Traceback (most recent call last):") == "error"
    assert classify_subprocess_stderr_level("TypeError: unexpected keyword argument") == "error"
    assert classify_subprocess_stderr_level("fatal: daemon crashed") == "critical"


def test_handle_subprocess_stderr_line_sets_last_error_for_crash_output():
    client = SendspinClient("Test Player", "localhost", 9000)

    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("sendspin_client.logger.error") as log_error,
    ):
        client._handle_subprocess_stderr_line("TypeError: unexpected keyword argument 'use_hardware_volume'")

    assert client.status.last_error == "TypeError: unexpected keyword argument 'use_hardware_volume'"
    assert client.status.last_error_at is not None
    log_error.assert_called_once()


def test_handle_subprocess_stderr_line_keeps_benign_stderr_as_warning():
    client = SendspinClient("Test Player", "localhost", 9000)

    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("sendspin_client.logger.warning") as log_warning,
    ):
        client._handle_subprocess_stderr_line("ALSA lib pcm.c:2666: Unknown PCM default")

    assert client.status.last_error is None
    log_warning.assert_called_once()
