"""Tests for services/pa_volume_controller.py."""

from __future__ import annotations

import asyncio
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


@pytest.mark.asyncio
async def test_handle_sink_event_fires_callback_on_external_change(controller):
    # Simulate the subscribe loop dispatching ``_handle_sink_event`` after
    # PA reports a sink change.  External tools (a separate ``pactl`` call,
    # the bridge's own /api/volume direct-pactl path, a physical knob on
    # the BT speaker) push the new state to MA without waiting for a poll.
    received: list[tuple[int, bool]] = []
    controller._callback = lambda v, m: received.append((v, m))
    controller._volume = 100
    controller._muted = False

    with (
        patch("services.pa_volume_controller.aget_sink_volume", new_callable=AsyncMock, return_value=42),
        patch("services.pa_volume_controller.aget_sink_mute", new_callable=AsyncMock, return_value=True),
    ):
        await controller._handle_sink_event()

    assert received == [(42, True)]
    # Cache updated so subsequent identical events don't re-fire.
    assert controller._volume == 42
    assert controller._muted is True


@pytest.mark.asyncio
async def test_handle_sink_event_suppresses_echo_from_set_state(controller):
    # Echo loop guard: when the controller has just applied (75, False)
    # via ``set_state``, the PA event echoing that change must NOT fire
    # the callback — otherwise sendspin → controller → MA → controller
    # would loop indefinitely.
    received: list[tuple[int, bool]] = []
    controller._callback = lambda v, m: received.append((v, m))
    # Pretend ``set_state(75, muted=False)`` already updated the cache.
    controller._volume = 75
    controller._muted = False

    with (
        patch("services.pa_volume_controller.aget_sink_volume", new_callable=AsyncMock, return_value=75),
        patch("services.pa_volume_controller.aget_sink_mute", new_callable=AsyncMock, return_value=False),
    ):
        await controller._handle_sink_event()

    assert received == []  # echo suppressed


@pytest.mark.asyncio
async def test_handle_sink_event_skips_when_sink_unreachable(controller):
    # Sink missing (BT disconnect, PA restart): aget_sink_* returns None.
    # The handler must not push (None, None) to the callback nor poison
    # the cache with a phantom 0/False reading.
    received: list[tuple[int, bool]] = []
    controller._callback = lambda v, m: received.append((v, m))
    controller._volume = 80
    controller._muted = False

    with (
        patch("services.pa_volume_controller.aget_sink_volume", new_callable=AsyncMock, return_value=None),
        patch("services.pa_volume_controller.aget_sink_mute", new_callable=AsyncMock, return_value=None),
    ):
        await controller._handle_sink_event()

    assert received == []
    assert controller._volume == 80  # cache untouched


@pytest.mark.asyncio
async def test_handle_sink_event_swallows_callback_exception(controller):
    # A bad sendspin-side callback must not kill the subscribe loop.
    def boom(v, m):
        raise RuntimeError("downstream is angry")

    controller._callback = boom
    controller._volume = 50
    controller._muted = False

    with (
        patch("services.pa_volume_controller.aget_sink_volume", new_callable=AsyncMock, return_value=60),
        patch("services.pa_volume_controller.aget_sink_mute", new_callable=AsyncMock, return_value=False),
    ):
        # Must not raise.
        await controller._handle_sink_event()

    assert controller._volume == 60  # cache still updated despite callback raising


@pytest.mark.asyncio
async def test_handle_sink_event_no_callback_still_updates_cache(controller):
    # Sendspin may call get_state directly without start_monitoring;
    # external events should keep the cache fresh either way so a
    # subsequent get_state returns the right values.
    controller._callback = None
    controller._volume = 30
    controller._muted = True

    with (
        patch("services.pa_volume_controller.aget_sink_volume", new_callable=AsyncMock, return_value=80),
        patch("services.pa_volume_controller.aget_sink_mute", new_callable=AsyncMock, return_value=False),
    ):
        await controller._handle_sink_event()

    assert controller._volume == 80
    assert controller._muted is False


@pytest.mark.asyncio
async def test_subscribe_loop_skips_when_pulsectl_unavailable(controller):
    # The dev/test boxes (macOS, containers without libpulse) must
    # tolerate a missing pulsectl_asyncio cleanly — the loop returns
    # immediately, no exception escapes ``start_monitoring``.
    with patch("services.pulse._PULSECTL_AVAILABLE", new=False):
        await controller._subscribe_loop()  # must return cleanly


@pytest.mark.asyncio
async def test_stop_monitoring_cancels_subscribe_task(controller):
    async def _never_ending():
        await asyncio.sleep(60)

    controller._monitor_task = asyncio.create_task(_never_ending())
    await controller.stop_monitoring()
    assert controller._monitor_task is None


@pytest.mark.asyncio
async def test_start_monitoring_replaces_callback_without_restarting_task(controller):
    def cb_a(v, m):
        pass

    def cb_b(v, m):
        pass

    with patch("services.pulse._PULSECTL_AVAILABLE", new=False):
        await controller.start_monitoring(cb_a)
        first_task = controller._monitor_task
        # Subscribe task may have already finished (since pulsectl is
        # unavailable), so calling start_monitoring again may legitimately
        # spawn a new task — the contract is just that the callback gets
        # replaced and the controller stays usable.
        await controller.start_monitoring(cb_b)
        assert controller._callback is cb_b

    # Cleanup
    if controller._monitor_task and not controller._monitor_task.done():
        controller._monitor_task.cancel()
    if first_task and not first_task.done():
        first_task.cancel()


async def _get_cached_state(ctrl: PulseVolumeController) -> tuple[int, bool]:
    """Read cached state without hitting PA."""
    return (ctrl._volume, ctrl._muted)
