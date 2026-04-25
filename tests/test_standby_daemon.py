"""Tests for Phase 2: null-sink standby with daemon alive + auto-wake."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_client(idle_disconnect_minutes: int = 30, *, daemon_alive: bool = False):
    """Create a SendspinClient with optional mock daemon proc."""
    from sendspin_client import DeviceStatus, SendspinClient

    client = SendspinClient.__new__(SendspinClient)
    client.player_name = "TestSpeaker"
    client.player_id = "test-player-id"
    client.idle_disconnect_minutes = idle_disconnect_minutes
    client.idle_mode = "auto_disconnect" if idle_disconnect_minutes > 0 else "default"
    client.power_save_delay_minutes = 1
    client._status_lock = threading.Lock()
    client._idle_timer_task = None
    client._idle_timer_lock = threading.Lock()
    client._power_save_timer_task = None
    client._playback_health = MagicMock()
    client._playback_health.on_status_update = MagicMock()
    client.status = DeviceStatus()
    client._event_device_id = MagicMock(return_value="dev-test")  # type: ignore[method-assign]
    client.bt_manager = MagicMock()
    client.stop_sendspin = AsyncMock()  # type: ignore[method-assign]
    client.bluetooth_sink_name = "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
    client._command_service = MagicMock()
    client._command_service.send = AsyncMock()

    if daemon_alive:
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = None
        client._daemon_proc = proc
    else:
        client._daemon_proc = None

    return client


# ── Null sink tests ───────────────────────────────────────────────────────


class TestNullSink:
    """services.pulse null sink management."""

    def test_standby_sink_name_constant(self):
        from services.pulse import STANDBY_SINK_NAME

        assert STANDBY_SINK_NAME == "sendspin_fallback"

    @patch("services.pulse.subprocess")
    def test_fallback_load_creates_sink(self, mock_subprocess):
        from services.pulse import _fallback_load_null_sink

        # First call: list shows sink doesn't exist
        list_result = MagicMock(returncode=0, stdout="alsa_output.default\t...\n")
        # Second call: load-module succeeds
        load_result = MagicMock(returncode=0, stdout="42\n")
        mock_subprocess.run.side_effect = [list_result, load_result]

        assert _fallback_load_null_sink() is True

    @patch("services.pulse.subprocess")
    def test_fallback_load_already_exists(self, mock_subprocess):
        from services.pulse import STANDBY_SINK_NAME, _fallback_load_null_sink

        list_result = MagicMock(
            returncode=0,
            stdout=f"1\t{STANDBY_SINK_NAME}\tmodule-null-sink.c\n",
        )
        mock_subprocess.run.return_value = list_result

        assert _fallback_load_null_sink() is True
        # Only one call (list), no load-module needed
        assert mock_subprocess.run.call_count == 1

    @patch("services.pulse.subprocess")
    def test_remove_null_sink_no_module(self, mock_subprocess):
        import services.pulse as pulse_mod

        pulse_mod._null_sink_module_id = None
        assert pulse_mod.remove_null_sink() is True
        mock_subprocess.run.assert_not_called()


# ── Standby with daemon alive ────────────────────────────────────────────


class TestStandbyDaemonAlive:
    """Phase 2: _enter_standby keeps daemon running on null sink."""

    @pytest.mark.asyncio
    async def test_enter_standby_moves_streams_to_null_sink(self):
        """Daemon stays alive; streams are moved to null sink."""
        with (
            patch("sendspin_client._state") as state_mock,
            patch("services.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as move_mock,
            patch("services.pulse.aensure_null_sink", new_callable=AsyncMock) as ensure_mock,
        ):
            ensure_mock.return_value = True
            move_mock.return_value = 1
            client = _make_client(daemon_alive=True)

            await client._enter_standby()

            assert client.status["bt_standby"] is True
            ensure_mock.assert_awaited_once()
            move_mock.assert_awaited_once_with(12345, "sendspin_fallback")
            client.stop_sendspin.assert_not_awaited()
            client.bt_manager.disconnect_device.assert_called_once()
            state_mock.publish_device_event.assert_called()

    @pytest.mark.asyncio
    async def test_enter_standby_fallback_stops_daemon_if_null_sink_fails(self):
        """Falls back to stopping daemon when null sink cannot be created."""
        with (
            patch("sendspin_client._state") as state_mock,
            patch("services.pulse.amove_pid_sink_inputs", new_callable=AsyncMock),
            patch("services.pulse.aensure_null_sink", new_callable=AsyncMock) as ensure_mock,
        ):
            ensure_mock.return_value = False  # null sink creation failed
            client = _make_client(daemon_alive=True)

            await client._enter_standby()

            assert client.status["bt_standby"] is True
            client.stop_sendspin.assert_awaited_once()
            state_mock.publish_device_event.assert_called()

    @pytest.mark.asyncio
    async def test_enter_standby_no_daemon_skips_null_sink(self):
        """When daemon is not running, null sink logic is skipped."""
        with patch("sendspin_client._state") as state_mock:
            client = _make_client(daemon_alive=False)

            await client._enter_standby()

            assert client.status["bt_standby"] is True
            client.bt_manager.disconnect_device.assert_called_once()
            state_mock.publish_device_event.assert_called()


# ── Auto-wake on play detection ──────────────────────────────────────────


class TestAutoWake:
    """Auto-wake when MA starts playback during standby."""

    @pytest.mark.asyncio
    async def test_on_standby_play_detected_triggers_wake(self):
        with patch("sendspin_client._state"):
            client = _make_client(daemon_alive=True)
            client.status.update({"bt_standby": True, "bt_standby_since": "2025-01-01"})

            await client._on_standby_play_detected()

            # bt_standby stays True until reroute; bt_waking signals reconnect
            assert client.status["bt_standby"] is True
            assert client.status["bt_waking"] is True
            client.bt_manager.allow_reconnect.assert_called_once()
            client.bt_manager.signal_standby_wake.assert_called_once()
            # Direct BT connect kicked off via run_in_executor
            client.bt_manager.connect_device.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_standby_play_noop_if_not_standby(self):
        with patch("sendspin_client._state"):
            client = _make_client(daemon_alive=True)
            # Not in standby
            await client._on_standby_play_detected()
            client.bt_manager.allow_reconnect.assert_not_called()


# ── Reroute to BT sink after wake ────────────────────────────────────────


class TestRerouteToBtSink:
    """After BT reconnects, streams move from null sink to BT sink."""

    @pytest.mark.asyncio
    async def test_reroute_moves_streams_and_sends_reanchor(self):
        with patch("services.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as move_mock:
            move_mock.return_value = 2
            client = _make_client(daemon_alive=True)
            client._send_subprocess_command = AsyncMock()

            result = await client._reroute_to_bt_sink()

            assert result is True
            move_mock.assert_awaited_once_with(12345, client.bluetooth_sink_name)
            # Two IPC calls: set_standby (restore PULSE_SINK) + reconnect (reanchor)
            assert client._send_subprocess_command.await_count == 2
            cmds = [c[0][0] for c in client._send_subprocess_command.call_args_list]
            assert cmds[0]["cmd"] == "set_standby"
            assert cmds[1]["cmd"] == "reconnect"

    @pytest.mark.asyncio
    async def test_reroute_no_daemon_noop(self):
        client = _make_client(daemon_alive=False)
        client._send_subprocess_command = AsyncMock()

        result = await client._reroute_to_bt_sink()

        assert result is False
        client._send_subprocess_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reroute_no_sink_name_noop(self):
        client = _make_client(daemon_alive=True)
        client.bluetooth_sink_name = None
        client._send_subprocess_command = AsyncMock()

        result = await client._reroute_to_bt_sink()

        assert result is False
        client._send_subprocess_command.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reroute_zero_moved_sends_reconnect(self):
        with patch("services.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as move_mock:
            move_mock.return_value = 0  # nothing to move
            client = _make_client(daemon_alive=True)
            client._send_subprocess_command = AsyncMock()

            result = await client._reroute_to_bt_sink()

            assert result is True
            # set_standby restore + reconnect
            assert client._send_subprocess_command.await_count == 2
            cmds = [c[0][0]["cmd"] for c in client._send_subprocess_command.call_args_list]
            assert cmds == ["set_standby", "reconnect"]


# ── Start sendspin with daemon already alive (standby wake) ──────────────


class TestStartSendspinReroute:
    """start_sendspin_inner reroutes instead of restart when daemon is alive."""

    @pytest.mark.asyncio
    async def test_start_sendspin_reroutes_if_daemon_alive(self):
        """When daemon is already running, _start_sendspin_inner reroutes to BT sink."""
        with patch("services.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as move_mock:
            move_mock.return_value = 1
            client = _make_client(daemon_alive=True)
            client._send_subprocess_command = AsyncMock()
            client._start_sendspin_lock = None

            await client._start_sendspin_inner()

            move_mock.assert_awaited_once()
            client.stop_sendspin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_sendspin_reconnects_if_reroute_finds_zero_streams(self):
        """When reroute finds 0 streams, daemon sends MA reconnect (no full restart)."""
        with patch("services.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as move_mock:
            move_mock.return_value = 0  # no streams to reroute
            client = _make_client(daemon_alive=True)
            client._send_subprocess_command = AsyncMock()
            client._start_sendspin_lock = None

            await client._start_sendspin_inner()

            move_mock.assert_awaited_once()
            # Reroute now returns True (reconnect sent) — no full restart
            client.stop_sendspin.assert_not_awaited()


# ── Keepalive infrasound ──────────────────────────────────────────────────


class TestKeepaliveInfrasound:
    """Keepalive burst uses 2 Hz infrasound instead of pure silence."""

    def test_infrasound_buffer_is_not_silence(self):
        """The keepalive buffer must contain non-zero samples (infrasound, not silence)."""
        from sendspin_client import _generate_infrasound_burst

        buf = _generate_infrasound_burst()
        assert isinstance(buf, (bytes, bytearray))
        assert len(buf) > 0
        # Must not be all zeros — that's the old silence approach
        assert buf != b"\x00" * len(buf)

    def test_infrasound_buffer_length(self):
        """Buffer length must match 1 s of stereo 16-bit 44100 Hz audio."""
        from sendspin_client import _generate_infrasound_burst

        buf = _generate_infrasound_burst()
        # 1 s x 44100 Hz x 2 channels x 2 bytes/sample = 176400 bytes
        assert len(buf) == 44100 * 2 * 2

    def test_infrasound_amplitude_bounded(self):
        """Peak amplitude must stay below -40 dB (≈ 328 out of 32767)."""
        import struct

        from sendspin_client import _generate_infrasound_burst

        buf = _generate_infrasound_burst()
        samples = struct.unpack(f"<{len(buf) // 2}h", buf)
        peak = max(abs(s) for s in samples)
        # Amplitude should be ~100, well below 328 (-40 dB threshold)
        assert peak < 328, f"Peak amplitude {peak} exceeds -40 dB safety limit"
        # But must have non-trivial signal
        assert peak > 10, f"Peak amplitude {peak} too low, signal may be lost"

    def test_infrasound_frequency_is_subsonic(self):
        """Verify the signal completes roughly 2 full cycles in 1 second (2 Hz)."""
        import struct

        from sendspin_client import _generate_infrasound_burst

        buf = _generate_infrasound_burst()
        # Extract left channel samples only (every other sample)
        all_samples = struct.unpack(f"<{len(buf) // 2}h", buf)
        left = all_samples[::2]  # stereo → left channel

        # Count zero-crossings (positive → negative transitions)
        crossings = 0
        for i in range(1, len(left)):
            if left[i - 1] >= 0 and left[i] < 0:
                crossings += 1
        # 2 Hz → 2 full cycles → 2 negative zero-crossings
        assert crossings == 2, f"Expected 2 zero-crossings (2 Hz), got {crossings}"


# ── keep_alive_method enum (v2.63.0-rc.2) ────────────────────────────────


class TestKeepaliveMethodEnum:
    """``keep_alive_method`` per-device option selects how the keepalive
    burst payload is generated.  Three modes:

      * ``infrasound`` (default) — 2 Hz subsonic stereo, the existing
        wake-keeping burst.
      * ``silence``  — same length, all-zero PCM; suitable for speakers
        whose firmware treats *any* non-empty A2DP frame as activity but
        misbehaves on the periodic 2 Hz tone (rare, but reported on a
        couple of older Yandex / JBL units).
      * ``none``     — return empty bytes; callers must skip the burst.
        Use when the operator wants the speaker to time out naturally.
    """

    def test_infrasound_method_uses_existing_burst(self):
        from sendspin_client import _generate_infrasound_burst, _generate_keepalive_buffer

        assert _generate_keepalive_buffer("infrasound") == _generate_infrasound_burst()

    def test_silence_method_is_all_zeros_with_same_length(self):
        from sendspin_client import _generate_infrasound_burst, _generate_keepalive_buffer

        ref_len = len(_generate_infrasound_burst())
        buf = _generate_keepalive_buffer("silence")
        assert len(buf) == ref_len
        assert buf == b"\x00" * ref_len

    def test_none_method_returns_empty_bytes(self):
        from sendspin_client import _generate_keepalive_buffer

        assert _generate_keepalive_buffer("none") == b""

    def test_unknown_method_falls_back_to_infrasound(self):
        """Defensive fallback so a bad config value can't disable keepalive
        silently — the original infrasound default is returned."""
        from sendspin_client import _generate_infrasound_burst, _generate_keepalive_buffer

        assert _generate_keepalive_buffer("bogus") == _generate_infrasound_burst()


# ── Keepalive suppression during standby ─────────────────────────────────


class TestKeepaliveSuppression:
    """Keepalive loop skips bursts during standby."""

    @pytest.mark.asyncio
    async def test_keepalive_skips_when_standby(self):
        """Verify _keepalive_loop does NOT send burst when bt_standby is True."""
        client = _make_client(daemon_alive=True)
        client.keepalive_enabled = True
        client.keepalive_interval = 0.05
        client.running = True
        client.status.update({"bt_standby": True, "audio_streaming": False})
        client.bt_manager.connected = True
        client._send_keepalive_burst = AsyncMock()

        # Run keepalive loop briefly
        task = asyncio.create_task(client._keepalive_loop())
        await asyncio.sleep(0.15)
        client.running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # bt_standby=True should have suppressed all bursts
        client._send_keepalive_burst.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_keepalive_fires_when_not_standby(self):
        """Verify _keepalive_loop DOES send burst when conditions are met."""
        client = _make_client(daemon_alive=True)
        client.keepalive_enabled = True
        client.keepalive_interval = 0.05
        client.running = True
        client.status.update({"bt_standby": False, "audio_streaming": False})
        client.bt_manager.connected = True
        client._send_keepalive_burst = AsyncMock()

        task = asyncio.create_task(client._keepalive_loop())
        await asyncio.sleep(0.15)
        client.running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert client._send_keepalive_burst.await_count >= 1


# ── Sync group auto-wake ─────────────────────────────────────────────────


class TestGroupAutoWake:
    """Sync group auto-wake: wake standby members when group starts playing."""

    @pytest.mark.asyncio
    async def test_group_auto_wake_triggers_for_standby_member(self):
        """When a group is playing and a client is in standby with that group_id, wake it."""
        from sendspin_client import DeviceStatus

        wake_called = False

        async def fake_wake():
            nonlocal wake_called
            wake_called = True

        client = MagicMock()
        client.player_name = "Speaker-A"
        client.status = DeviceStatus()
        client.status.update({"bt_standby": True, "group_id": "group-1"})
        client._wake_from_standby = fake_wake

        import state

        loop = asyncio.get_running_loop()
        with (
            patch.object(state, "_get_registry_active_clients_snapshot", return_value=[client]),
            patch.object(state, "get_main_loop", return_value=loop),
        ):
            state._check_group_auto_wake({"group-1": {"state": "playing"}})
            await asyncio.sleep(0.05)

        assert wake_called

    def test_group_auto_wake_noop_when_not_playing(self):
        """No wake when group state is idle."""
        import state
        from sendspin_client import DeviceStatus

        client = MagicMock()
        client.status = DeviceStatus()
        client.status.update({"bt_standby": True, "group_id": "group-1"})

        with (
            patch.object(state, "_get_registry_active_clients_snapshot", return_value=[client]),
            patch.object(state, "get_main_loop", return_value=MagicMock()),
        ):
            state._check_group_auto_wake({"group-1": {"state": "idle"}})

        # No playing groups → no wake attempted
        client._wake_from_standby.assert_not_called()

    def test_group_auto_wake_noop_when_not_standby(self):
        """Client not in standby is not woken even if group is playing."""
        import state
        from sendspin_client import DeviceStatus

        client = MagicMock()
        client.status = DeviceStatus()
        client.status.update({"bt_standby": False, "group_id": "group-1"})

        with (
            patch.object(state, "_get_registry_active_clients_snapshot", return_value=[client]),
            patch.object(state, "get_main_loop", return_value=MagicMock()),
        ):
            state._check_group_auto_wake({"group-1": {"state": "playing"}})

        client._wake_from_standby.assert_not_called()

    @pytest.mark.asyncio
    async def test_solo_player_auto_wake_by_player_id(self):
        """Solo player (no group_id) is auto-woken when now_playing keyed by player_id is playing."""
        from sendspin_client import DeviceStatus

        wake_called = False

        async def fake_wake():
            nonlocal wake_called
            wake_called = True

        client = MagicMock()
        client.player_name = "Solo-Speaker"
        client.player_id = "solo-player-abc"
        client.status = DeviceStatus()
        client.status.update({"bt_standby": True})  # No group_id
        client._wake_from_standby = fake_wake

        import state

        loop = asyncio.get_running_loop()
        with (
            patch.object(state, "_get_registry_active_clients_snapshot", return_value=[client]),
            patch.object(state, "get_main_loop", return_value=loop),
        ):
            # now_playing keyed by player_id (how MA monitor caches solo players)
            state._check_group_auto_wake({"solo-player-abc": {"state": "playing"}})
            await asyncio.sleep(0.05)

        assert wake_called

    def test_solo_player_no_wake_when_idle(self):
        """Solo player is NOT woken when now_playing keyed by player_id is idle."""
        import state
        from sendspin_client import DeviceStatus

        client = MagicMock()
        client.player_name = "Solo-Speaker"
        client.player_id = "solo-player-abc"
        client.status = DeviceStatus()
        client.status.update({"bt_standby": True})  # No group_id

        with (
            patch.object(state, "_get_registry_active_clients_snapshot", return_value=[client]),
            patch.object(state, "get_main_loop", return_value=MagicMock()),
        ):
            state._check_group_auto_wake({"solo-player-abc": {"state": "idle"}})

        client._wake_from_standby.assert_not_called()
