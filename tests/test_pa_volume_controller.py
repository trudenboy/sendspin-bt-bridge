"""Tests for services/pa_volume_controller.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.pa_volume_controller import PulseVolumeController


@pytest.fixture
def controller():
    return PulseVolumeController("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink")


@pytest.mark.asyncio
async def test_set_state_calls_pa_helpers(controller):
    with (
        patch("services.pa_volume_controller.aset_sink_volume", new_callable=AsyncMock, return_value=True) as mock_vol,
        patch("services.pa_volume_controller.aset_sink_mute", new_callable=AsyncMock, return_value=True) as mock_mute,
    ):
        await controller.set_state(75, muted=False)
        mock_vol.assert_awaited_once_with("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink", 75)
        mock_mute.assert_awaited_once_with("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink", False)


@pytest.mark.asyncio
async def test_set_state_clamps_volume(controller):
    with (
        patch("services.pa_volume_controller.aset_sink_volume", new_callable=AsyncMock, return_value=True),
        patch("services.pa_volume_controller.aset_sink_mute", new_callable=AsyncMock, return_value=True),
    ):
        await controller.set_state(150, muted=False)
        state = await _get_cached_state(controller)
        assert state[0] == 100

        await controller.set_state(-10, muted=True)
        state = await _get_cached_state(controller)
        assert state[0] == 0


@pytest.mark.asyncio
async def test_get_state_reads_from_pa(controller):
    with (
        patch("services.pa_volume_controller.aget_sink_volume", new_callable=AsyncMock, return_value=42),
        patch("services.pa_volume_controller.aget_sink_mute", new_callable=AsyncMock, return_value=True),
    ):
        vol, muted = await controller.get_state()
        assert vol == 42
        assert muted is True


@pytest.mark.asyncio
async def test_get_state_falls_back_to_cached_on_none(controller):
    with (
        patch("services.pa_volume_controller.aget_sink_volume", new_callable=AsyncMock, return_value=None),
        patch("services.pa_volume_controller.aget_sink_mute", new_callable=AsyncMock, return_value=None),
    ):
        vol, muted = await controller.get_state()
        assert vol == 100  # default
        assert muted is False  # default


@pytest.mark.asyncio
async def test_start_stop_monitoring(controller):
    def cb(v, m):
        pass

    await controller.start_monitoring(cb)
    assert controller._callback is cb
    await controller.stop_monitoring()
    assert controller._callback is None


async def _get_cached_state(ctrl: PulseVolumeController) -> tuple[int, bool]:
    """Read cached state without hitting PA."""
    return (ctrl._volume, ctrl._muted)
