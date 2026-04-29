"""Tests for sink routing correction after BT device connections."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_client(
    player_name: str = "Speaker",
    bt_manager: object | None = None,
    pid: int | None = 1234,
    sink: str | None = "bluez_sink.XX.a2dp_sink",
) -> MagicMock:
    """Create a minimal mock SendspinClient for routing tests."""
    client = MagicMock()
    client.player_name = player_name
    client.bt_manager = bt_manager
    client.get_subprocess_pid = MagicMock(return_value=pid)
    client.bluetooth_sink_name = sink
    return client


@pytest.mark.asyncio
@patch("sendspin_bridge.bluetooth.monitor._SINK_CORRECTION_DELAY", 0)
async def test_skips_triggering_device():
    """amove_pid_sink_inputs must NOT be called for the triggering device."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    triggering_mgr = MagicMock()
    client_a = _make_client("A", bt_manager=triggering_mgr, pid=100, sink="sink_a")
    client_b = _make_client("B", bt_manager=MagicMock(), pid=200, sink="sink_b")

    with (
        patch(
            "sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot",
            return_value=[client_a, client_b],
        ),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs",
            new_callable=AsyncMock,
            return_value=0,
        ) as mock_move,
    ):
        await _correct_other_devices_routing(triggering_mgr)

    mock_move.assert_called_once_with(200, "sink_b")


@pytest.mark.asyncio
@patch("sendspin_bridge.bluetooth.monitor._SINK_CORRECTION_DELAY", 0)
async def test_corrects_misrouted_stream():
    """Verify amove_pid_sink_inputs is called with B's pid and sink."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    triggering_mgr = MagicMock()
    client_a = _make_client("A", bt_manager=triggering_mgr, pid=100, sink="sink_a")
    client_b = _make_client("B", bt_manager=MagicMock(), pid=1234, sink="bluez_sink.XX.a2dp_sink")

    with (
        patch(
            "sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot",
            return_value=[client_a, client_b],
        ),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_move,
    ):
        await _correct_other_devices_routing(triggering_mgr)

    mock_move.assert_called_once_with(1234, "bluez_sink.XX.a2dp_sink")


@pytest.mark.asyncio
@patch("sendspin_bridge.bluetooth.monitor._SINK_CORRECTION_DELAY", 0)
async def test_skips_client_without_subprocess():
    """Client with no subprocess (pid=None) must be skipped."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    triggering_mgr = MagicMock()
    client = _make_client("NoProc", bt_manager=MagicMock(), pid=None, sink="sink_x")

    with (
        patch(
            "sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot",
            return_value=[client],
        ),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs",
            new_callable=AsyncMock,
        ) as mock_move,
    ):
        await _correct_other_devices_routing(triggering_mgr)

    mock_move.assert_not_called()


@pytest.mark.asyncio
@patch("sendspin_bridge.bluetooth.monitor._SINK_CORRECTION_DELAY", 0)
async def test_skips_client_without_sink():
    """Client with a pid but no bluetooth_sink_name must be skipped."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    triggering_mgr = MagicMock()
    client = _make_client("NoSink", bt_manager=MagicMock(), pid=500, sink=None)

    with (
        patch(
            "sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot",
            return_value=[client],
        ),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs",
            new_callable=AsyncMock,
        ) as mock_move,
    ):
        await _correct_other_devices_routing(triggering_mgr)

    mock_move.assert_not_called()


@pytest.mark.asyncio
@patch("sendspin_bridge.bluetooth.monitor._SINK_CORRECTION_DELAY", 0)
async def test_handles_pulse_error_gracefully():
    """PulseAudio errors must be caught — function returns normally."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    triggering_mgr = MagicMock()
    client = _make_client("Err", bt_manager=MagicMock(), pid=999, sink="sink_err")

    with (
        patch(
            "sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot",
            return_value=[client],
        ),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PA connection lost"),
        ),
    ):
        await _correct_other_devices_routing(triggering_mgr)  # must not raise


@pytest.mark.asyncio
async def test_get_subprocess_pid_protocol():
    """SendspinClient.get_subprocess_pid() returns None / PID correctly."""
    from sendspin_client import SendspinClient

    bt_mgr = MagicMock()
    bt_mgr.check_bluetooth_available = MagicMock(return_value=False)
    bt_mgr.mac_address = "AA:BB:CC:DD:EE:FF"

    client = SendspinClient(
        player_name="TestSpeaker",
        server_host="127.0.0.1",
        server_port=9000,
        bt_manager=bt_mgr,
    )

    # No subprocess → None
    assert client.get_subprocess_pid() is None

    # Simulate a running subprocess
    fake_proc = MagicMock()
    fake_proc.returncode = None
    fake_proc.pid = 4242
    client._daemon_proc = fake_proc
    assert client.get_subprocess_pid() == 4242

    # Simulate exited subprocess
    fake_proc.returncode = 0
    assert client.get_subprocess_pid() is None
