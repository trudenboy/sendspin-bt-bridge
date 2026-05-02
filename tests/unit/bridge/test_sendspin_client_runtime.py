"""Focused runtime tests for SendspinClient edge cases."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import sendspin_bridge.bridge.state as state
from sendspin_bridge.bridge.client import SendspinClient, _filter_duplicate_bluetooth_devices
from sendspin_bridge.services.diagnostics.log_analysis import classify_subprocess_stderr_level
from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION


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
    monkeypatch.setattr("sendspin_bridge.bridge.client._MA_RECONNECT_TIMEOUT_S", 0.0)

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

    with patch("sendspin_bridge.bridge.client._state.notify_status_changed"):
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

    client._daemon_proc = SimpleNamespace(
        returncode=None,
        stdout=_FakeStdoutLines(
            [
                json.dumps({"type": "status", "protocol_version": IPC_PROTOCOL_VERSION, "playing": True}).encode(),
            ]
        ),
    )

    await client._read_subprocess_output()

    assert client.status.playing is True


@pytest.mark.asyncio
async def test_read_subprocess_output_accepts_structured_error_envelope_once():
    client = SendspinClient("Test Player", "localhost", 9000)

    client._daemon_proc = SimpleNamespace(
        returncode=None,
        stdout=_FakeStdoutLines(
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
        ),
    )

    with patch("sendspin_bridge.bridge.client.logger.error"):
        await client._read_subprocess_output()

    assert client.status.last_error == "No audio output device found"
    assert client.status.last_error_at == "2026-03-18T09:10:00+00:00"


@pytest.mark.asyncio
async def test_read_subprocess_output_delegates_log_messages_to_ipc_service():
    client = SendspinClient("Test Player", "localhost", 9000)

    log_line = json.dumps({"type": "log", "level": "info", "msg": "hello"}).encode()

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
    client._daemon_proc = SimpleNamespace(
        returncode=None,
        stdout=_FakeStdoutLines([log_line]),
    )

    await client._read_subprocess_output()

    assert fake_service.seen == [
        ("parse", log_line + b"\n"),
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

    with patch("sendspin_bridge.bridge.client._state.notify_status_changed"):
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
        with patch("sendspin_bridge.bridge.client._state.notify_status_changed"):
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

    with patch("sendspin_bridge.bridge.client._state.notify_status_changed"):
        client._update_status({"playing": True})
        client._update_status({"audio_streaming": True})
        client._update_status({"playing": False})
        client._update_status({"playing": True, "audio_streaming": False})
    client._playing_since = 100.0

    scheduled = []
    with (
        patch("sendspin_bridge.bridge.client.time.monotonic", return_value=116.0),
        patch("sendspin_bridge.bridge.client.asyncio.create_task", side_effect=lambda coro: scheduled.append(coro)),
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
        patch("sendspin_bridge.bridge.client._state.notify_status_changed"),
        patch("sendspin_bridge.bridge.client.logger.error") as log_error,
    ):
        client._handle_subprocess_stderr_line("TypeError: unexpected keyword argument 'use_hardware_volume'")

    assert client.status.last_error == "TypeError: unexpected keyword argument 'use_hardware_volume'"
    assert client.status.last_error_at is not None
    log_error.assert_called_once()


def test_handle_subprocess_stderr_line_keeps_benign_stderr_as_warning():
    client = SendspinClient("Test Player", "localhost", 9000)

    with (
        patch("sendspin_bridge.bridge.client._state.notify_status_changed"),
        patch("sendspin_bridge.bridge.client.logger.warning") as log_warning,
    ):
        client._handle_subprocess_stderr_line("ALSA lib pcm.c:2666: Unknown PCM default")

    assert client.status.last_error is None
    log_warning.assert_called_once()


# ---------------------------------------------------------------------------
# Mute-desync after BT reconnect (#132)
# ---------------------------------------------------------------------------


class _FakeStdoutLines:
    """``readline``-based stdout mock that yields pre-encoded lines then EOF."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            line = self._lines.pop(0)
            if line and not line.endswith(b"\n"):
                line = line + b"\n"
            return line
        return b""


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
        patch("sendspin_bridge.bridge.client._state.notify_status_changed"),
        patch("sendspin_bridge.services.music_assistant.ma_runtime_state.is_ma_connected", return_value=True),
        patch("sendspin_bridge.services.music_assistant.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_awaited_once_with(
        "players/cmd/volume_mute",
        {"player_id": "sendspin-test-player", "muted": False},
    )
    assert client.status.muted is False
    assert client._pending_reconnect_unmute_sync is False


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
        patch("sendspin_bridge.bridge.client._state.notify_status_changed"),
        patch("sendspin_bridge.services.music_assistant.ma_runtime_state.is_ma_connected", return_value=False),
        patch("sendspin_bridge.services.music_assistant.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_not_awaited()


@pytest.mark.asyncio
async def test_sink_unmute_force_pushes_to_ma_even_when_local_status_says_unmuted():
    # Regression for the #user-report mute-mismatch bug: the daemon
    # mutes its PA sink during startup to hide format-probe noise; MA's
    # initial ``volume_controller.get_state()`` poll happens during that
    # window and reads ``(100, True)`` so MA records ``muted=True``.
    # ~15 s later the startup-unmute watcher releases the PA sink, and
    # the parent observes ``sink_muted=False`` while
    # ``_pending_reconnect_unmute_sync`` is still set from spawn.
    #
    # The daemon's local ``status["muted"]`` was *always* ``False`` —
    # it doesn't reflect MA's view of the player.  The pre-fix early-
    # exit would treat this as "already in sync" and never push the
    # unmute, leaving HA's MA UI with the volume slider greyed out
    # forever (until the user manually clicked Unmute).
    #
    # ``force=True`` on the post-spawn sync bypasses that early-exit.
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
        patch("sendspin_bridge.bridge.client._state.notify_status_changed"),
        patch("sendspin_bridge.services.music_assistant.ma_runtime_state.is_ma_connected", return_value=True),
        patch("sendspin_bridge.services.music_assistant.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_awaited_once_with(
        "players/cmd/volume_mute",
        {"player_id": "sendspin-test-player", "muted": False},
    )
    assert client._pending_reconnect_unmute_sync is False


@pytest.mark.asyncio
async def test_sync_unmute_to_ma_without_force_skips_when_already_unmuted():
    # Direct call to ``_sync_unmute_to_ma()`` *without* the post-spawn
    # context still honours the original safety guard: if the bridge's
    # local view says we're not muted, don't push to MA.  This protects
    # against double-unmuting after the user has explicitly muted
    # (#155 regression).
    client = SendspinClient("Test Player", "localhost", 9000)
    client.player_id = "sendspin-test-player"
    client._update_status({"muted": False})

    with (
        patch("sendspin_bridge.bridge.client._state.notify_status_changed"),
        patch("sendspin_bridge.services.music_assistant.ma_runtime_state.is_ma_connected", return_value=True),
        patch("sendspin_bridge.services.music_assistant.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._sync_unmute_to_ma()  # default force=False

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
        patch("sendspin_bridge.bridge.client._state.notify_status_changed"),
        patch("sendspin_bridge.services.music_assistant.ma_runtime_state.is_ma_connected", return_value=True),
        patch("sendspin_bridge.services.music_assistant.ma_monitor.send_player_cmd", return_value=True) as mock_cmd,
    ):
        await client._read_subprocess_output()

    mock_cmd.assert_not_awaited()
    # Mute state should remain True (user's mute preserved)
    assert client.status.muted is True


def test_sink_mute_watchdog_starts_on_desync():
    """Watchdog should be scheduled when sink_muted becomes True while app is not muted."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.status.update({"muted": False, "sink_muted": False})

    with patch.object(client, "_start_sink_mute_watchdog") as mock_start:
        client._update_status({"sink_muted": True})

    mock_start.assert_called_once()


def test_sink_mute_watchdog_cancelled_on_unmute():
    """Watchdog should be cancelled when sink gets unmuted."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.status.update({"muted": False, "sink_muted": True})

    with patch.object(client, "_cancel_sink_mute_watchdog") as mock_cancel:
        client._update_status({"sink_muted": False})

    mock_cancel.assert_called_once()


def test_sink_mute_watchdog_not_started_when_app_muted():
    """When user explicitly muted, sink_muted is expected — no watchdog."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.status.update({"muted": True, "sink_muted": False})

    with patch.object(client, "_start_sink_mute_watchdog") as mock_start:
        client._update_status({"sink_muted": True})

    mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_start_sendspin_inner_auto_shifts_listen_port_when_taken(monkeypatch):
    """When the configured port is taken, listen_port must auto-shift and status must flag collision."""
    client = SendspinClient("Test Player", "localhost", 9000, listen_port=8928, listen_host="127.0.0.1")
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = ""  # skip BT configure path

    # Force the probe to return a shifted port, and capture probe args to verify
    # we always probe the wildcard interface even when listen_host is set.
    probe_calls: list[dict] = []

    def _fake_probe(port, *, host, max_attempts):
        probe_calls.append({"port": port, "host": host, "max_attempts": max_attempts})
        return 8929

    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", _fake_probe)

    captured_params: list[str] = []

    class _FakeProc:
        returncode = None

        def __init__(self):
            self.stdin = SimpleNamespace(write=lambda _d: None, drain=_noop_async, close=lambda: None)
            self.stdout = SimpleNamespace(readline=_eof_readline)
            self.stderr = SimpleNamespace(readline=_eof_readline)

        async def wait(self):
            return 0

    async def _fake_exec(*args, **kwargs):
        # args = (python, '-m', 'sendspin_bridge.services.ipc.daemon_process', params, ...)
        captured_params.append(args[3])
        return _FakeProc()

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=_fake_exec),
        patch.object(client, "is_running", return_value=False),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client._start_sendspin_inner()

    assert client.listen_port == 8929
    assert client.status["port_collision"] is True
    assert client.status["active_listen_port"] == 8929
    assert captured_params, "subprocess should have been spawned"
    parsed = json.loads(captured_params[0])
    assert parsed["listen_port"] == 8929
    # Probe must target wildcard to catch collisions on interfaces the daemon
    # actually binds — not the specific listen_host that is display-only.
    assert probe_calls[0]["host"] == "0.0.0.0"


@pytest.mark.asyncio
async def test_start_sendspin_inner_clears_port_collision_on_clean_start(monkeypatch):
    """After a clean spawn, a prior port_collision flag must be cleared."""
    client = SendspinClient("Test Player", "localhost", 9000, listen_port=8928)
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = ""
    # Pretend a previous restart cycle flagged a collision.
    client.status.update({"port_collision": True, "active_listen_port": 8929})

    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda p, **_: p)

    class _FakeProc:
        returncode = None

        def __init__(self):
            self.stdin = SimpleNamespace(write=lambda _d: None, drain=_noop_async, close=lambda: None)
            self.stdout = SimpleNamespace(readline=_eof_readline)
            self.stderr = SimpleNamespace(readline=_eof_readline)

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=_fake_exec),
        patch.object(client, "is_running", return_value=False),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client._start_sendspin_inner()

    assert client.status["port_collision"] is False
    assert client.status["active_listen_port"] is None


@pytest.mark.asyncio
async def test_start_sendspin_inner_halts_after_max_bind_failures(monkeypatch):
    """After _MAX_BIND_FAILURES consecutive probe failures, the restart loop must be halted."""
    from sendspin_bridge.bridge.client import _MAX_BIND_FAILURES

    client = SendspinClient("Test Player", "localhost", 9000, listen_port=8928)
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = ""

    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: None)

    exec_calls = 0

    async def _unexpected_exec(*args, **kwargs):
        nonlocal exec_calls
        exec_calls += 1
        return SimpleNamespace(returncode=0, stdin=None, stdout=None, stderr=None)

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=_unexpected_exec),
        patch.object(client, "is_running", return_value=False),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        for _ in range(_MAX_BIND_FAILURES):
            await client._start_sendspin_inner()

    assert client._bind_failures == _MAX_BIND_FAILURES
    assert client._restart_halted is True
    assert client.status["port_collision"] is True
    assert exec_calls == 0  # never spawned because probe kept failing


async def _noop_async(*_args, **_kwargs):
    return None


async def _eof_readline():
    return b""


@pytest.mark.asyncio
async def test_read_subprocess_output_persists_static_delay_from_ma(monkeypatch):
    """When MA pushes SET_STATIC_DELAY → daemon mirrors to status → parent persists.

    Issue #237: per-device static_delay_ms must be saved to BLUETOOTH_DEVICES[i]
    (not a LAST_* runtime cache) so the value survives container restart.
    """
    client = SendspinClient("Test Player", "localhost", 9000)
    client.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
    client._daemon_proc = SimpleNamespace(
        returncode=None,
        stdout=_FakeStdoutLines(
            [
                json.dumps(
                    {
                        "type": "status",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "static_delay_ms": 750,
                    }
                ).encode(),
            ]
        ),
    )

    saved: list[tuple[str, int]] = []

    def _fake_save(mac, delay_ms):
        saved.append((mac, delay_ms))

    monkeypatch.setattr("sendspin_bridge.bridge.client.save_device_static_delay", _fake_save)

    await client._read_subprocess_output()

    assert saved == [("AA:BB:CC:DD:EE:FF", 750)]
    # Parent-side cache also updated so warm_restart re-spawns with the new value.
    assert client.static_delay_ms == 750.0


@pytest.mark.asyncio
async def test_read_subprocess_output_skips_static_delay_persist_when_no_mac(monkeypatch):
    """No MAC (BT manager not initialized) → no save attempt; no crash."""
    client = SendspinClient("Test Player", "localhost", 9000)
    client.bt_manager = None  # type: ignore[assignment]
    client._daemon_proc = SimpleNamespace(
        returncode=None,
        stdout=_FakeStdoutLines(
            [
                json.dumps(
                    {
                        "type": "status",
                        "protocol_version": IPC_PROTOCOL_VERSION,
                        "static_delay_ms": 400,
                    }
                ).encode(),
            ]
        ),
    )

    saved: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "sendspin_bridge.bridge.client.save_device_static_delay",
        lambda mac, val: saved.append((mac, val)),
    )

    await client._read_subprocess_output()

    assert saved == []
