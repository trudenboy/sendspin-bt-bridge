"""Focused runtime tests for SendspinClient edge cases."""

from __future__ import annotations

import asyncio
import json
import threading
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


class _YieldingLock:
    """Lock wrapper that yields before acquire to exercise pre-acquire races."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._allow_acquire = asyncio.Event()

    def locked(self) -> bool:
        return self._lock.locked()

    def release_acquire(self) -> None:
        self._allow_acquire.set()

    async def __aenter__(self):
        await asyncio.sleep(0)
        await self._allow_acquire.wait()
        await self._lock.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._lock.release()
        return False


@pytest.mark.asyncio
async def test_send_subprocess_command_uses_snapshot_when_proc_changes():
    """Command send should survive proc mutation between guard and drain()."""
    client = SendspinClient("Test Player", "localhost", 9000)
    stdin = _RaceyStdin(client)
    client._daemon_proc = _FakeProc(stdin)

    await client._send_subprocess_command({"cmd": "stop"})

    assert stdin.writes == [f'{{"cmd": "stop", "protocol_version": {IPC_PROTOCOL_VERSION}}}\n'.encode()]


@pytest.mark.asyncio
async def test_send_subprocess_command_delegates_to_command_service():
    client = SendspinClient("Test Player", "localhost", 9000)
    proc = object()
    client._daemon_proc = proc

    class _FakeService:
        def __init__(self):
            self.calls = []

        async def send(self, current_proc, cmd):
            self.calls.append((current_proc, cmd))

    fake_service = _FakeService()
    client._command_service = fake_service

    await client._send_subprocess_command({"cmd": "pause"})

    assert fake_service.calls == [(proc, {"cmd": "pause"})]


@pytest.mark.asyncio
async def test_send_reconnect_marks_expected_ma_reconnect_and_clears_on_server_connect():
    client = SendspinClient("Test Player", "localhost", 9000)
    proc = SimpleNamespace(returncode=None, stdin=object())
    client._daemon_proc = proc
    client.status.update({"server_connected": True})

    class _FakeService:
        def __init__(self):
            self.calls = []

        async def send(self, current_proc, cmd):
            self.calls.append((current_proc, cmd))

    fake_service = _FakeService()
    client._command_service = fake_service

    await client.send_reconnect()

    assert client.status.get("ma_reconnecting") is True
    assert fake_service.calls == [(proc, {"cmd": "reconnect", "delay": 3.0})]

    client._update_status({"server_connected": True})
    client._clear_ma_reconnecting()

    assert client.status.get("ma_reconnecting") is False


@pytest.mark.asyncio
async def test_send_reconnect_timeout_clears_stuck_ma_reconnect(monkeypatch):
    client = SendspinClient("Test Player", "localhost", 9000)
    client._daemon_proc = SimpleNamespace(returncode=None, stdin=object())
    client.status.update({"server_connected": True})

    async def _fake_send(_proc, _cmd):
        return None

    monkeypatch.setattr(client._command_service, "send", _fake_send)
    monkeypatch.setattr("sendspin_client._MA_RECONNECT_TIMEOUT_S", 0.0)

    await client.send_reconnect()
    assert client._ma_reconnect_task is not None
    await client._ma_reconnect_task

    assert client.status.get("ma_reconnecting") is False


@pytest.mark.asyncio
async def test_stop_sendspin_delegates_to_stop_service():
    client = SendspinClient("Test Player", "localhost", 9000)
    proc = object()
    client._daemon_proc = proc
    daemon_task = asyncio.create_task(asyncio.sleep(3600))
    stderr_task = asyncio.create_task(asyncio.sleep(3600))
    client._daemon_task = daemon_task
    client._stderr_task = stderr_task

    class _FakeService:
        def __init__(self):
            self.cancel_calls = []
            self.stop_calls = []

        async def cancel_reader_tasks(self, tasks):
            self.cancel_calls.append(tasks)
            for task in tasks.values():
                if task:
                    task.cancel()
            return {"_daemon_task": None, "_stderr_task": None}

        async def stop_process(self, current_proc, *, send_stop, player_name, reader_tasks=None):
            self.stop_calls.append((current_proc, send_stop, player_name, reader_tasks))
            if reader_tasks:
                for task in reader_tasks.values():
                    if task:
                        task.cancel()
            return {"_daemon_task": None, "_stderr_task": None} if reader_tasks else None

    fake_service = _FakeService()
    client._stop_service = fake_service

    with patch("sendspin_client._state.notify_status_changed"):
        await client.stop_sendspin()

    assert len(fake_service.stop_calls) == 1
    call = fake_service.stop_calls[0]
    assert call[0] is proc
    assert call[1].__func__ is client._send_subprocess_command.__func__
    assert call[2] == "Test Player"
    assert call[3] == {"_daemon_task": daemon_task, "_stderr_task": stderr_task}
    assert client._daemon_task is None
    assert client._stderr_task is None
    assert client._daemon_proc is None


@pytest.mark.asyncio
async def test_start_sendspin_queues_followup_when_request_arrives_during_start():
    client = SendspinClient("Test Player", "localhost", 9000)
    client._start_sendspin_lock = asyncio.Lock()
    calls: list[str] = []

    async def _fake_start_inner():
        calls.append("start")
        if len(calls) == 1:
            queued = asyncio.create_task(client.start_sendspin())
            await asyncio.sleep(0)
            await queued

    client._start_sendspin_inner = _fake_start_inner

    await client.start_sendspin()

    assert calls == ["start", "start"]


@pytest.mark.asyncio
async def test_start_sendspin_coalesces_multiple_overlapping_requests():
    client = SendspinClient("Test Player", "localhost", 9000)
    client._start_sendspin_lock = asyncio.Lock()
    calls: list[int] = []
    release = asyncio.Event()

    async def _fake_start_inner():
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            await release.wait()

    client._start_sendspin_inner = _fake_start_inner

    first = asyncio.create_task(client.start_sendspin())
    await asyncio.sleep(0)
    queued = [asyncio.create_task(client.start_sendspin()) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    await first
    await asyncio.gather(*queued)

    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_start_sendspin_coalesces_requests_before_lock_acquire():
    client = SendspinClient("Test Player", "localhost", 9000)
    yielding_lock = _YieldingLock()
    client._start_sendspin_lock = yielding_lock
    calls: list[int] = []

    async def _fake_start_inner():
        calls.append(len(calls) + 1)

    client._start_sendspin_inner = _fake_start_inner

    first = asyncio.create_task(client.start_sendspin())
    second = asyncio.create_task(client.start_sendspin())
    await asyncio.sleep(0)
    yielding_lock.release_acquire()
    await asyncio.gather(first, second)

    assert calls == [1]


@pytest.mark.asyncio
async def test_start_sendspin_inner_offloads_configure_bt_audio_to_executor():
    """configure_bluetooth_audio must run via run_in_executor, not block the loop."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client._start_sendspin_lock = asyncio.Lock()

    configure_called_in_executor = False

    def _fake_configure():
        nonlocal configure_called_in_executor
        configure_called_in_executor = True
        return True

    bt_mgr = SimpleNamespace(connected=True, configure_bluetooth_audio=_fake_configure)
    client.bt_manager = bt_mgr
    client.bluetooth_sink_name = ""

    executor_used = False

    async def _tracking_executor(executor, fn, *args):
        nonlocal executor_used
        executor_used = True
        return fn(*args)

    with (
        patch.object(asyncio.get_running_loop(), "run_in_executor", side_effect=_tracking_executor),
        patch.object(client, "is_running", return_value=False),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        try:
            await client._start_sendspin_inner()
        except Exception:
            pass

    assert executor_used, "configure_bluetooth_audio should be called via run_in_executor"
    assert configure_called_in_executor, "configure_bluetooth_audio should have been invoked"


def test_set_bt_management_enabled_cancels_reconnect_before_release():
    client = SendspinClient("Test Player", "localhost", 9000)
    bt_manager = SimpleNamespace(
        cancel_reconnect_calls=0,
        allow_reconnect_calls=0,
        cancel_reconnect=lambda: None,
        allow_reconnect=lambda: None,
    )

    def _cancel():
        bt_manager.cancel_reconnect_calls += 1

    def _allow():
        bt_manager.allow_reconnect_calls += 1

    bt_manager.cancel_reconnect = _cancel
    bt_manager.allow_reconnect = _allow
    client.bt_manager = bt_manager
    client._status_lock = threading.Lock()
    client.status.update({"reconnecting": True, "bt_management_enabled": True})

    with patch.object(client, "is_running", return_value=False):
        client.set_bt_management_enabled(False)
        client.set_bt_management_enabled(True)

    assert bt_manager.cancel_reconnect_calls == 1
    assert bt_manager.allow_reconnect_calls == 1
    assert client.status["bt_management_enabled"] is True


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
async def test_read_subprocess_output_accepts_structured_error_envelope_once():
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
                json.dumps(
                    {
                        "type": "error",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "error_code": "audio_output_missing",
                        "message": "No audio output device found",
                        "details": {"at": "2026-03-18T09:10:00+00:00"},
                    }
                ).encode(),
            ]
        )
    )

    with patch("sendspin_client.logger.error"):
        await client._read_subprocess_output()

    assert client.status.last_error == "No audio output device found"
    assert client.status.last_error_at == "2026-03-18T09:10:00+00:00"


@pytest.mark.asyncio
async def test_read_subprocess_output_delegates_log_messages_to_ipc_service():
    client = SendspinClient("Test Player", "localhost", 9000)

    class _FakeStdout:
        def __aiter__(self):
            self._iter = iter([json.dumps({"type": "log", "level": "info", "msg": "hello"}).encode()])
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class _FakeService:
        def __init__(self):
            self.seen = []

        def parse_line(self, line):
            self.seen.append(("parse", line))
            return {"type": "log", "level": "info", "msg": "hello"}

        def handle_message(self, msg):
            self.seen.append(("handle", msg))
            return None

    fake_service = _FakeService()
    client._ipc_service = fake_service
    client._daemon_proc = SimpleNamespace(stdout=_FakeStdout())

    await client._read_subprocess_output()

    assert fake_service.seen == [
        ("parse", json.dumps({"type": "log", "level": "info", "msg": "hello"}).encode()),
        ("handle", {"type": "log", "level": "info", "msg": "hello"}),
    ]


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


# ---------------------------------------------------------------------------
# Mute-desync after BT reconnect (#132)
# ---------------------------------------------------------------------------


class _FakeStdoutLines:
    """Async-iterable that yields pre-encoded lines."""

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


@pytest.mark.asyncio
async def test_sink_unmute_syncs_to_ma_when_muted_via_ma():
    """When daemon reports sink_muted=False and MA thinks device is muted,
    the parent must forward the unmute to MA (issue #132)."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.player_id = "sendspin-test-player"
    # Simulate MA-muted state before reconnect
    client._update_status({"muted": True, "sink_muted": True})
    client._pending_reconnect_unmute_sync = True

    client._daemon_proc = SimpleNamespace(
        stdout=_FakeStdoutLines(
            [
                json.dumps(
                    {
                        "type": "status",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "sink_muted": False,
                        "muted": True,
                    }
                ).encode(),
            ]
        )
    )

    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("routes.api_config.get_mute_via_ma", return_value=True),
        patch("services.ma_runtime_state.is_ma_connected", return_value=True),
        patch("services.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_awaited_once_with(
        "players/cmd/volume_mute",
        {"player_id": "sendspin-test-player", "muted": False},
    )
    assert client.status.muted is False
    assert client._pending_reconnect_unmute_sync is False


@pytest.mark.asyncio
async def test_sink_unmute_skipped_when_mute_via_ma_disabled():
    """When MUTE_VIA_MA is disabled, sink unmute should NOT be synced to MA."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.player_id = "sendspin-test-player"
    client._update_status({"muted": True, "sink_muted": True})
    client._pending_reconnect_unmute_sync = True

    client._daemon_proc = SimpleNamespace(
        stdout=_FakeStdoutLines(
            [
                json.dumps(
                    {
                        "type": "status",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "sink_muted": False,
                        "muted": True,
                    }
                ).encode(),
            ]
        )
    )

    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("routes.api_config.get_mute_via_ma", return_value=False),
        patch("services.ma_runtime_state.is_ma_connected", return_value=True),
        patch("services.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_not_awaited()


@pytest.mark.asyncio
async def test_sink_unmute_skipped_when_ma_not_connected():
    """When MA is not connected, unmute should NOT be synced."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.player_id = "sendspin-test-player"
    client._update_status({"muted": True, "sink_muted": True})
    client._pending_reconnect_unmute_sync = True

    client._daemon_proc = SimpleNamespace(
        stdout=_FakeStdoutLines(
            [
                json.dumps(
                    {
                        "type": "status",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "sink_muted": False,
                        "muted": True,
                    }
                ).encode(),
            ]
        )
    )

    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("routes.api_config.get_mute_via_ma", return_value=True),
        patch("services.ma_runtime_state.is_ma_connected", return_value=False),
        patch("services.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_not_awaited()


@pytest.mark.asyncio
async def test_sink_unmute_skipped_when_already_in_sync():
    """When muted is already False, no MA call is needed (no desync)."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.player_id = "sendspin-test-player"
    client._update_status({"muted": False, "sink_muted": True})
    client._pending_reconnect_unmute_sync = True

    client._daemon_proc = SimpleNamespace(
        stdout=_FakeStdoutLines(
            [
                json.dumps(
                    {
                        "type": "status",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "sink_muted": False,
                        "muted": False,
                    }
                ).encode(),
            ]
        )
    )

    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("routes.api_config.get_mute_via_ma", return_value=True),
        patch("services.ma_runtime_state.is_ma_connected", return_value=True),
        patch("services.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_not_awaited()


@pytest.mark.asyncio
async def test_sink_unmute_not_synced_without_reconnect_flag():
    """After the first reconnect sync is consumed, subsequent sink_muted=False
    events must NOT trigger MA unmute — this would override user mute (#155)."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.player_id = "sendspin-test-player"
    client._update_status({"muted": True, "sink_muted": True})
    # Flag already consumed (default False) — simulates normal operation
    assert client._pending_reconnect_unmute_sync is False

    client._daemon_proc = SimpleNamespace(
        stdout=_FakeStdoutLines(
            [
                json.dumps(
                    {
                        "type": "status",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "sink_muted": False,
                        "muted": True,
                    }
                ).encode(),
            ]
        )
    )

    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("routes.api_config.get_mute_via_ma", return_value=True),
        patch("services.ma_runtime_state.is_ma_connected", return_value=True),
        patch("services.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_not_awaited()
    # Mute state should remain True (user's mute preserved)
    assert client.status.muted is True
