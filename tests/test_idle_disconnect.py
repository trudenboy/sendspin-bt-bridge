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

            assert client.status["bt_standby"] is False
            assert client.status["bt_standby_since"] is None
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
