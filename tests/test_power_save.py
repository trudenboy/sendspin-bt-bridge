"""Tests for power_save idle mode: PA sink suspend/resume and timer scheduling."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_client(*, idle_mode: str = "power_save", power_save_delay: int = 1):
    """Create a SendspinClient configured for power_save testing."""
    from sendspin_client import DeviceStatus, SendspinClient

    client = SendspinClient.__new__(SendspinClient)
    client.player_name = "TestSpeaker"
    client.player_id = "test-player-id"
    client.idle_mode = idle_mode
    client.idle_disconnect_minutes = 0
    client.power_save_delay_minutes = power_save_delay
    client._status_lock = threading.Lock()
    client._idle_timer_task = None
    client._idle_timer_lock = threading.Lock()
    client._power_save_timer_task = None
    client._playback_health = MagicMock()
    client._playback_health.on_status_update = MagicMock()
    client.status = DeviceStatus(idle_mode=idle_mode)
    client._event_device_id = MagicMock(return_value="dev-test")  # type: ignore[method-assign]
    client.bt_manager = MagicMock()
    client.stop_sendspin = AsyncMock()  # type: ignore[method-assign]
    client.bluetooth_sink_name = "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
    client._command_service = MagicMock()
    client._command_service.send = AsyncMock()
    client._daemon_proc = None
    return client


class TestEnterPowerSave:
    """Tests for _enter_power_save()."""

    @pytest.mark.asyncio
    async def test_enter_power_save_suspends_sink(self):
        client = _make_client()
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock, return_value=True) as mock_suspend:
            await client._enter_power_save()
            mock_suspend.assert_awaited_once_with("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink", True)
            assert client.status["bt_power_save"] is True

    @pytest.mark.asyncio
    async def test_enter_power_save_noop_if_already_in_power_save(self):
        client = _make_client()
        client.status.update({"bt_power_save": True})
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock) as mock_suspend:
            await client._enter_power_save()
            mock_suspend.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enter_power_save_noop_if_no_sink(self):
        client = _make_client()
        client.bluetooth_sink_name = None
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock) as mock_suspend:
            await client._enter_power_save()
            mock_suspend.assert_not_awaited()
            assert client.status["bt_power_save"] is False

    @pytest.mark.asyncio
    async def test_enter_power_save_failure_does_not_set_flag(self):
        client = _make_client()
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock, return_value=False):
            await client._enter_power_save()
            assert client.status["bt_power_save"] is False


class TestExitPowerSave:
    """Tests for _exit_power_save()."""

    @pytest.mark.asyncio
    async def test_exit_power_save_resumes_sink(self):
        client = _make_client()
        client.status.update({"bt_power_save": True})
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock, return_value=True) as mock_suspend:
            await client._exit_power_save()
            mock_suspend.assert_awaited_once_with("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink", False)
            assert client.status["bt_power_save"] is False

    @pytest.mark.asyncio
    async def test_exit_power_save_noop_if_not_in_power_save(self):
        client = _make_client()
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock) as mock_suspend:
            await client._exit_power_save()
            mock_suspend.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exit_clears_flag_even_if_no_sink(self):
        client = _make_client()
        client.status.update({"bt_power_save": True})
        client.bluetooth_sink_name = None
        await client._exit_power_save()
        assert client.status["bt_power_save"] is False


class TestPowerSaveTimer:
    """Tests for _start_power_save_timer and cancellation."""

    @pytest.mark.asyncio
    async def test_timer_schedules_suspend_after_delay(self):
        client = _make_client(power_save_delay=0)
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock, return_value=True) as mock_suspend:
            client._start_power_save_timer()
            await asyncio.sleep(0.1)
            mock_suspend.assert_awaited_once_with("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink", True)
            assert client.status["bt_power_save"] is True

    @pytest.mark.asyncio
    async def test_timer_cancel_prevents_suspend(self):
        client = _make_client(power_save_delay=1)
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock) as mock_suspend:
            client._start_power_save_timer()
            client._cancel_power_save_timer()
            await asyncio.sleep(0.1)
            mock_suspend.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timer_skips_if_playing(self):
        client = _make_client(power_save_delay=0)
        client.status.update({"playing": True})
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock) as mock_suspend:
            client._start_power_save_timer()
            await asyncio.sleep(0.1)
            mock_suspend.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timer_skips_if_standby(self):
        client = _make_client(power_save_delay=0)
        client.status.update({"bt_standby": True})
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock) as mock_suspend:
            client._start_power_save_timer()
            await asyncio.sleep(0.1)
            mock_suspend.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timer_skips_if_mode_changed(self):
        client = _make_client(power_save_delay=0)
        client.idle_mode = "default"
        with patch("services.pulse.asuspend_sink", new_callable=AsyncMock) as mock_suspend:
            client._start_power_save_timer()
            await asyncio.sleep(0.1)
            mock_suspend.assert_not_awaited()

    def test_cancel_timer_noop_if_no_task(self):
        client = _make_client()
        client._power_save_timer_task = None
        client._cancel_power_save_timer()  # should not raise


class TestIdleModeInStatus:
    """Tests that idle_mode is exposed in DeviceStatus."""

    def test_idle_mode_in_device_status_default(self):
        from sendspin_client import DeviceStatus

        status = DeviceStatus()
        assert status["idle_mode"] == "default"

    def test_idle_mode_in_device_status_custom(self):
        from sendspin_client import DeviceStatus

        status = DeviceStatus(idle_mode="power_save")
        assert status["idle_mode"] == "power_save"

    def test_idle_mode_in_dict_conversion(self):
        from sendspin_client import DeviceStatus

        status = DeviceStatus(idle_mode="keep_alive")
        d = status.copy()
        assert d["idle_mode"] == "keep_alive"
