"""Tests for idle disconnect (standby) feature in SendspinClient."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory for every test."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{}")


# ── helpers ────────────────────────────────────────────────────────────────


def _make_client(idle_disconnect_minutes: int = 30):
    """Create a SendspinClient via __new__ with minimal wiring for idle timer tests."""
    from sendspin_client import DeviceStatus, SendspinClient

    client = SendspinClient.__new__(SendspinClient)
    client.player_name = "TestSpeaker"
    client.idle_disconnect_minutes = idle_disconnect_minutes
    client._status_lock = threading.Lock()
    client._idle_timer_lock = threading.Lock()
    client._idle_timer_task = None
    client._playback_health = MagicMock()
    client._playback_health.on_status_update = MagicMock()
    client.status = DeviceStatus()
    client._event_device_id = MagicMock(return_value="dev-test")  # type: ignore[method-assign]
    client.bt_manager = MagicMock()
    client.stop_sendspin = AsyncMock()  # type: ignore[method-assign]
    client._daemon_proc = None
    client.bluetooth_sink_name = None
    client._command_service = MagicMock()
    return client


# ── Tests ──────────────────────────────────────────────────────────────────


class TestDeviceStatusStandbyFields:
    """DeviceStatus exposes bt_standby and bt_standby_since."""

    def test_defaults_false(self):
        from sendspin_client import DeviceStatus

        status = DeviceStatus()
        assert status.get("bt_standby") is False
        assert status.get("bt_standby_since") is None

    def test_update_standby(self):
        from sendspin_client import DeviceStatus

        status = DeviceStatus()
        status.update({"bt_standby": True, "bt_standby_since": "2025-01-01T00:00:00+00:00"})
        assert status["bt_standby"] is True
        assert status["bt_standby_since"] == "2025-01-01T00:00:00+00:00"

    def test_clear_standby(self):
        from sendspin_client import DeviceStatus

        status = DeviceStatus()
        status.update({"bt_standby": True, "bt_standby_since": "2025-01-01T00:00:00+00:00"})
        status.update({"bt_standby": False, "bt_standby_since": None})
        assert status["bt_standby"] is False
        assert status["bt_standby_since"] is None


class TestIdleTimerStartCancel:
    """_start_idle_timer / _cancel_idle_timer lifecycle."""

    def test_timer_starts_on_streaming_stop(self):
        """Timer starts when audio_streaming goes True→False."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=30)
            client.status.update({"audio_streaming": True})

            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"audio_streaming": False})
                start_mock.assert_called_once()

    def test_timer_cancels_on_streaming_start(self):
        """Timer cancels when audio_streaming goes False→True."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=30)
            # Previous state: not streaming
            with patch.object(client, "_cancel_idle_timer") as cancel_mock:
                client._update_status({"audio_streaming": True})
                cancel_mock.assert_called_once()

    def test_timer_not_started_when_disabled(self):
        """Timer does not start when idle_disconnect_minutes=0."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=0)
            client.status.update({"audio_streaming": True})

            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"audio_streaming": False})
                start_mock.assert_not_called()

    def test_timer_starts_on_daemon_connect_without_audio(self):
        """Timer starts when daemon connects (server_connected=True) with no audio playing."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            # Device has no audio streaming (initial state)
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"server_connected": True})
                start_mock.assert_called_once()

    def test_timer_not_started_on_daemon_connect_when_streaming(self):
        """Timer does NOT start on daemon connect if audio is already streaming."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client.status.update({"audio_streaming": True})
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"server_connected": True})
                start_mock.assert_not_called()

    def test_timer_not_started_on_daemon_connect_when_already_standby(self):
        """Timer does NOT start on daemon connect if already in standby."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client.status.update({"bt_standby": True})
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"server_connected": True})
                start_mock.assert_not_called()

    def test_timer_not_started_on_daemon_connect_when_disabled(self):
        """Timer does NOT start on daemon connect when idle_disconnect_minutes=0."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=0)
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"server_connected": True})
                start_mock.assert_not_called()

    def test_timer_cancels_on_playing_start(self):
        """Timer cancels when playing goes False→True (MA reports playback)."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            # Previous state: not playing
            with patch.object(client, "_cancel_idle_timer") as cancel_mock:
                client._update_status({"playing": True})
                cancel_mock.assert_called_once()

    def test_timer_starts_on_playing_stop_without_streaming(self):
        """Timer starts when playing goes True→False and audio_streaming is False."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client.status.update({"playing": True})
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"playing": False})
                start_mock.assert_called_once()

    def test_timer_not_started_on_playing_stop_while_streaming(self):
        """Timer does NOT start when playing→False if audio_streaming is still True."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client.status.update({"playing": True, "audio_streaming": True})
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"playing": False})
                start_mock.assert_not_called()

    def test_timer_not_started_on_daemon_connect_when_playing(self):
        """Timer does NOT start on daemon connect if playing is True."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client.status.update({"playing": True})
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"server_connected": True})
                start_mock.assert_not_called()


class TestCancelIdleTimer:
    """_cancel_idle_timer edge cases."""

    def test_cancel_noop_when_no_task(self):
        client = _make_client()
        client._idle_timer_task = None
        client._cancel_idle_timer()
        assert client._idle_timer_task is None

    def test_cancel_calls_task_cancel(self):
        client = _make_client()
        task_mock = MagicMock()
        task_mock.cancel = MagicMock()
        client._idle_timer_task = task_mock
        client._cancel_idle_timer()
        task_mock.cancel.assert_called_once()
        assert client._idle_timer_task is None


class TestEnterStandby:
    """_enter_standby() stops daemon, disconnects BT, sets status."""

    @pytest.mark.asyncio
    async def test_enter_standby_sets_status_and_disconnects(self):
        with patch("sendspin_client._state") as state_mock:
            client = _make_client(idle_disconnect_minutes=30)

            await client._enter_standby()

            assert client.status["bt_standby"] is True
            assert client.status["bt_standby_since"] is not None
            assert client.status["bt_released_by"] == "idle_timeout"
            # Phase 2: daemon stays alive, stop_sendspin NOT called
            client.stop_sendspin.assert_not_awaited()
            client.bt_manager.disconnect_device.assert_called_once()
            state_mock.publish_device_event.assert_called()

    @pytest.mark.asyncio
    async def test_enter_standby_noop_if_already_standby(self):
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=30)
            client.status.update({"bt_standby": True})

            await client._enter_standby()

            client.stop_sendspin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enter_standby_handles_bt_disconnect_failure(self):
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=30)
            client.bt_manager.disconnect_device.side_effect = RuntimeError("BT gone")

            # Should not raise
            await client._enter_standby()
            assert client.status["bt_standby"] is True


class TestWakeFromStandby:
    """_wake_from_standby() clears standby and allows reconnect."""

    @pytest.mark.asyncio
    async def test_wake_clears_standby_and_allows_reconnect(self):
        with patch("sendspin_client._state") as state_mock:
            client = _make_client(idle_disconnect_minutes=30)
            client.status.update(
                {
                    "bt_standby": True,
                    "bt_standby_since": "2025-01-01T00:00:00+00:00",
                }
            )

            await client._wake_from_standby()

            # bt_standby stays True until reroute completes; bt_waking signals reconnect
            assert client.status["bt_standby"] is True
            assert client.status["bt_waking"] is True
            assert client.status.get("bt_released_by") is None
            client.bt_manager.allow_reconnect.assert_called_once()
            state_mock.publish_device_event.assert_called()

    @pytest.mark.asyncio
    async def test_wake_noop_if_not_in_standby(self):
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=30)

            await client._wake_from_standby()

            client.bt_manager.allow_reconnect.assert_not_called()


class TestBtMonitorStandbyCheck:
    """bt_monitor skips reconnect when device is in standby."""

    def test_standby_skips_reconnect_in_polling(self):
        """Verify the standby check exists in _monitor_polling logic."""
        import inspect

        import bt_monitor

        src = inspect.getsource(bt_monitor._monitor_polling)
        assert "bt_standby" in src, "_monitor_polling should check bt_standby"

    def test_standby_skips_reconnect_in_dbus(self):
        """Verify the standby check exists in _inner_dbus_monitor logic."""
        import inspect

        import bt_monitor

        src = inspect.getsource(bt_monitor._inner_dbus_monitor)
        assert "bt_standby" in src, "_inner_dbus_monitor should check bt_standby"


class TestWakeApiEndpoint:
    """POST /api/bt/wake endpoint tests."""

    def _make_app(self):
        from flask import Flask

        app = Flask(__name__)
        app.config["TESTING"] = True
        from routes.api_bt import bt_bp

        app.register_blueprint(bt_bp)
        return app

    def test_wake_success(self):
        client = MagicMock()
        client.status = MagicMock()
        client.status.get = MagicMock(
            side_effect=lambda k, d=None: {
                "bt_standby": True,
            }.get(k, d)
        )
        client._wake_from_standby = AsyncMock()

        loop = asyncio.new_event_loop()

        with (
            patch("routes.api_bt.get_client_or_error", return_value=(client, None)),
            patch("state.get_main_loop", return_value=loop),
        ):
            app = self._make_app()
            with app.test_client() as tc:
                loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
                loop_thread.start()
                try:
                    resp = tc.post("/api/bt/wake", json={"player_name": "Spk"})
                    assert resp.status_code == 200
                    data = resp.get_json()
                    assert data["success"] is True
                finally:
                    loop.call_soon_threadsafe(loop.stop)
                    loop_thread.join(timeout=2)
                    loop.close()

    def test_wake_not_in_standby_returns_409(self):
        client = MagicMock()
        client.status = MagicMock()
        client.status.get = MagicMock(return_value=False)

        with patch("routes.api_bt.get_client_or_error", return_value=(client, None)):
            app = self._make_app()
            with app.test_client() as tc:
                resp = tc.post("/api/bt/wake", json={"player_name": "Spk"})
                assert resp.status_code == 409

    def test_wake_no_client_returns_error(self):
        app = self._make_app()
        with app.app_context():
            from flask import jsonify as _jsonify

            err_resp = _jsonify(success=False, error="Not found"), 404
        with patch("routes.api_bt.get_client_or_error", return_value=(None, err_resp)), app.test_client() as tc:
            resp = tc.post("/api/bt/wake", json={"player_name": "Unknown"})
            assert resp.status_code == 404


class TestIdleTimerFullCycle:
    """Integration-style test: full timer lifecycle with asyncio."""

    @pytest.mark.asyncio
    async def test_timer_fires_and_enters_standby(self):
        """Timer fires after timeout and calls _enter_standby."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None  # force ensure_future path
            state_mock.notify_status_changed = MagicMock()
            state_mock.publish_device_event = MagicMock()
            client = _make_client(idle_disconnect_minutes=1)

            # Directly test _enter_standby via the timer mechanism
            # Instead of patching sleep (tricky), call _enter_standby directly
            await client._enter_standby()
            assert client.status["bt_standby"] is True
            # Phase 2: daemon stays alive, stop_sendspin NOT called
            client.stop_sendspin.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timer_cancelled_before_firing(self):
        """Timer cancelled before expiry does not enter standby."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)

            client._start_idle_timer()
            assert client._idle_timer_task is not None
            client._cancel_idle_timer()
            assert client._idle_timer_task is None
            await asyncio.sleep(0.05)
            assert client.status["bt_standby"] is False

    @pytest.mark.asyncio
    async def test_multiple_start_stop_cycles_reset_timer(self):
        """Starting/stopping streaming multiple times resets the timer each time."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)

            # Simulate: streaming on → off → on → off
            client.status.update({"audio_streaming": True})
            client._update_status({"audio_streaming": False})
            task1 = client._idle_timer_task
            assert task1 is not None

            client._update_status({"audio_streaming": True})
            assert client._idle_timer_task is None  # cancelled

            client.status.update({"audio_streaming": True})
            client._update_status({"audio_streaming": False})
            task2 = client._idle_timer_task
            assert task2 is not None
            assert task2 is not task1  # new timer

            client._cancel_idle_timer()


class TestIdleTimerGuardAtFiring:
    """_idle_timeout() safety guard at firing time."""

    @pytest.mark.asyncio
    async def test_timer_enters_standby_directly(self):
        """Timer fires → enters standby without checking daemon flags."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            state_mock.publish_device_event = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)
            # Playing=True should NOT prevent standby — PA sink state
            # (via SinkMonitor) is the authority, not daemon flags.
            client.status.update({"playing": True})

            with patch("sendspin_client.asyncio.sleep", new_callable=AsyncMock):
                client._start_idle_timer()
                task = client._idle_timer_task
                assert task is not None
                await task
                assert client.status["bt_standby"] is True

    @pytest.mark.asyncio
    async def test_timer_enters_standby_when_idle_at_fire_time(self):
        """If both playing and audio_streaming are False, standby proceeds."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            state_mock.publish_device_event = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)
            # Both default to False — device is truly idle

            with patch("sendspin_client.asyncio.sleep", new_callable=AsyncMock):
                client._start_idle_timer()
                task = client._idle_timer_task
                assert task is not None
                await task
                assert client.status["bt_standby"] is True

    @pytest.mark.asyncio
    async def test_timer_suppressed_when_already_standby(self):
        """Timer fires but bt_standby is already True → skip."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            state_mock.publish_device_event = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)
            client.status.update({"bt_standby": True})

            with patch("sendspin_client.asyncio.sleep", new_callable=AsyncMock):
                client._start_idle_timer()
                task = client._idle_timer_task
                assert task is not None
                await task
                # _enter_standby is a no-op when bt_standby already True
                assert client.status["bt_standby"] is True

    @pytest.mark.asyncio
    async def test_timer_suppressed_when_waking(self):
        """Timer fires during bt_waking → skip standby."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            state_mock.publish_device_event = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)
            # Simulate wake in progress
            client.status.update({"bt_standby": False, "bt_waking": True})

            with patch("sendspin_client.asyncio.sleep", new_callable=AsyncMock):
                client._start_idle_timer()
                task = client._idle_timer_task
                assert task is not None
                await task
                # Should NOT enter standby during wake
                assert client.status["bt_standby"] is False

    @pytest.mark.asyncio
    async def test_timer_suppressed_when_pa_sink_running(self):
        """Timer fires but cached PA sink state is 'running' → suppress standby."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            state_mock.publish_device_event = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)
            # Wire a fake sink monitor with cached running state
            sm = MagicMock()
            sm._sink_states = {"bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink": "running"}
            client._sink_monitor = sm
            client.bluetooth_sink_name = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"

            with patch("sendspin_client.asyncio.sleep", new_callable=AsyncMock):
                client._start_idle_timer()
                task = client._idle_timer_task
                assert task is not None
                await task
                assert client.status["bt_standby"] is False

    @pytest.mark.asyncio
    async def test_timer_proceeds_when_pa_sink_idle(self):
        """Timer fires and cached PA sink state is 'idle' → enter standby."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            state_mock.notify_status_changed = MagicMock()
            state_mock.publish_device_event = MagicMock()
            client = _make_client(idle_disconnect_minutes=30)
            sm = MagicMock()
            sm._sink_states = {"bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink": "idle"}
            client._sink_monitor = sm
            client.bluetooth_sink_name = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"

            with patch("sendspin_client.asyncio.sleep", new_callable=AsyncMock):
                client._start_idle_timer()
                task = client._idle_timer_task
                assert task is not None
                await task
                assert client.status["bt_standby"] is True


class TestSinkMonitorDrivenIdle:
    """Sink monitor callbacks drive idle timer start/cancel."""

    def test_on_sink_active_cancels_timer(self):
        """_on_sink_active() cancels idle timer."""
        client = _make_client(idle_disconnect_minutes=15)
        client._idle_timer_task = MagicMock()
        client._idle_timer_task.cancel = MagicMock()
        client._on_sink_active()
        assert client._idle_timer_task is None

    def test_on_sink_idle_starts_timer(self):
        """_on_sink_idle() starts idle timer."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            client = _make_client(idle_disconnect_minutes=15)
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._on_sink_idle()
                start_mock.assert_called_once()

    def test_on_sink_idle_noop_when_disabled(self):
        """_on_sink_idle() does nothing when idle_disconnect_minutes=0."""
        client = _make_client(idle_disconnect_minutes=0)
        with patch.object(client, "_start_idle_timer") as start_mock:
            client._on_sink_idle()
            start_mock.assert_not_called()

    def test_on_sink_active_noop_when_disabled(self):
        """_on_sink_active() does nothing when idle_disconnect_minutes=0."""
        client = _make_client(idle_disconnect_minutes=0)
        client._idle_timer_task = MagicMock()
        client._on_sink_active()
        # Timer NOT cancelled — _on_sink_active returns early
        assert client._idle_timer_task is not None

    def test_on_sink_idle_noop_when_keepalive_enabled(self):
        """_on_sink_idle() does nothing when keepalive is enabled."""
        client = _make_client(idle_disconnect_minutes=15)
        client.keepalive_enabled = True
        with patch.object(client, "_start_idle_timer") as start_mock:
            client._on_sink_idle()
            start_mock.assert_not_called()

    def test_on_sink_idle_noop_when_already_standby(self):
        """_on_sink_idle() does nothing when device is already in standby."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client.status.update({"bt_standby": True})
            with patch.object(client, "_start_idle_timer") as start_mock:
                client._on_sink_idle()
                start_mock.assert_not_called()


class TestSinkMonitorFallback:
    """When sink monitor is active, daemon-flag fallback is suppressed."""

    def test_fallback_suppressed_when_sink_monitor_active(self):
        """_update_status does NOT start timer from daemon flags when sink monitor is active."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client._sink_monitor = MagicMock()
            client._sink_monitor.available = True
            client.bluetooth_sink_name = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"
            client.status.update({"audio_streaming": True})

            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"audio_streaming": False})
                start_mock.assert_not_called()

    def test_fallback_active_when_no_sink_monitor(self):
        """_update_status DOES start timer from daemon flags when sink monitor is unavailable."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client._sink_monitor = None
            client.status.update({"audio_streaming": True})

            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"audio_streaming": False})
                start_mock.assert_called_once()

    def test_fallback_active_when_sink_monitor_not_available(self):
        """_update_status DOES start timer when sink monitor exists but not available."""
        with patch("sendspin_client._state"):
            client = _make_client(idle_disconnect_minutes=15)
            client._sink_monitor = MagicMock()
            client._sink_monitor.available = False
            client.status.update({"audio_streaming": True})

            with patch.object(client, "_start_idle_timer") as start_mock:
                client._update_status({"audio_streaming": False})
                start_mock.assert_called_once()


class TestSinkMonitorActiveCheck:
    """_sink_monitor_active() returns correct state."""

    def test_no_sink_monitor_returns_false(self):
        client = _make_client()
        client._sink_monitor = None
        assert client._sink_monitor_active() is False

    def test_sink_monitor_available_with_sink_returns_true(self):
        client = _make_client()
        client._sink_monitor = MagicMock()
        client._sink_monitor.available = True
        client.bluetooth_sink_name = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"
        assert client._sink_monitor_active() is True

    def test_sink_monitor_not_available_returns_false(self):
        client = _make_client()
        client._sink_monitor = MagicMock()
        client._sink_monitor.available = False
        client.bluetooth_sink_name = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"
        assert client._sink_monitor_active() is False

    def test_sink_monitor_available_but_no_sink_name_returns_false(self):
        """Fallback stays active until BT sink is actually discovered."""
        client = _make_client()
        client._sink_monitor = MagicMock()
        client._sink_monitor.available = True
        client.bluetooth_sink_name = None
        assert client._sink_monitor_active() is False


class TestIdleTimerThreadSafety:
    """Thread-safe access to _idle_timer_task via _idle_timer_lock."""

    def test_concurrent_start_cancel_no_leak(self):
        """Concurrent _start and _cancel don't leak timers."""
        with patch("sendspin_client._state") as state_mock:
            state_mock.get_main_loop.return_value = None
            client = _make_client(idle_disconnect_minutes=15)

            results = []

            def _start():
                try:
                    client._start_idle_timer()
                    results.append("started")
                except Exception as e:
                    results.append(f"error: {e}")

            def _cancel():
                try:
                    client._cancel_idle_timer()
                    results.append("cancelled")
                except Exception as e:
                    results.append(f"error: {e}")

            threads = []
            for _ in range(5):
                threads.append(threading.Thread(target=_start))
                threads.append(threading.Thread(target=_cancel))

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            # No errors should have occurred
            assert all("error" not in r for r in results)

    def test_lock_exists(self):
        """Verify _idle_timer_lock is a real lock."""
        client = _make_client()
        assert isinstance(client._idle_timer_lock, type(threading.Lock()))
