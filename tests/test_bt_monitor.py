"""Tests for bt_monitor.py — D-Bus signal and polling fallback monitoring loops.

bt_monitor functions receive a BluetoothManager instance and operate on its
attributes.  All D-Bus, Bluetooth, and PulseAudio interactions are mocked.

Note: bt_monitor uses *lazy imports* inside function bodies (e.g.
``from services.device_registry import get_active_clients_snapshot``),
so patches must target the *source module*, not ``bt_monitor.<name>``.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture()
def bt_manager():
    """Create a BluetoothManager with reasonable defaults for testing."""
    from bluetooth_manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="TestSpeaker",
        )
    mgr._running = True
    mgr.management_enabled = True
    return mgr


# ---------------------------------------------------------------------------
# _correct_other_devices_routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_correct_other_devices_routing_skips_triggering_manager(bt_manager):
    """The triggering manager's own client should be skipped (no move attempt)."""
    from bt_monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = bt_manager
    client.get_subprocess_pid.return_value = 123
    client.bluetooth_sink_name = "bluez_sink.AA_BB"

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_not_called()


@pytest.mark.asyncio
async def test_correct_other_devices_routing_moves_other_clients(bt_manager):
    """Clients belonging to other managers get their sink routing corrected."""
    from bt_monitor import _correct_other_devices_routing

    other_mgr = MagicMock()
    client = MagicMock()
    client.bt_manager = other_mgr
    client.get_subprocess_pid.return_value = 456
    client.bluetooth_sink_name = "bluez_sink.CC_DD"
    client.player_name = "OtherSpeaker"

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock, return_value=1
        ) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_awaited_once_with(456, "bluez_sink.CC_DD")


@pytest.mark.asyncio
async def test_correct_other_devices_routing_skips_client_without_pid(bt_manager):
    """Clients with no running subprocess (pid=None) are skipped."""
    from bt_monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = MagicMock()  # different manager
    client.get_subprocess_pid.return_value = None
    client.bluetooth_sink_name = "bluez_sink.XX_YY"

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_not_called()


@pytest.mark.asyncio
async def test_correct_other_devices_routing_skips_client_without_sink(bt_manager):
    """Clients with empty bluetooth_sink_name are skipped."""
    from bt_monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = MagicMock()
    client.get_subprocess_pid.return_value = 789
    client.bluetooth_sink_name = ""

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_not_called()


@pytest.mark.asyncio
async def test_correct_other_devices_routing_handles_move_exception(bt_manager):
    """amove_pid_sink_inputs exceptions are caught and logged, not propagated."""
    from bt_monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = MagicMock()
    client.get_subprocess_pid.return_value = 111
    client.bluetooth_sink_name = "bluez_sink.EE_FF"
    client.player_name = "FailSpeaker"

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs",
            new_callable=AsyncMock,
            side_effect=RuntimeError("PA dead"),
        ),
    ):
        # Should not raise
        await _correct_other_devices_routing(bt_manager)


@pytest.mark.asyncio
async def test_correct_other_devices_routing_handles_none_bt_manager(bt_manager):
    """Clients whose bt_manager attribute is None should be processed normally."""
    from bt_monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = None
    client.get_subprocess_pid.return_value = 222
    client.bluetooth_sink_name = "bluez_sink.GG_HH"

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock, return_value=0
        ) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_awaited_once_with(222, "bluez_sink.GG_HH")


# ---------------------------------------------------------------------------
# monitor_and_reconnect — D-Bus vs polling fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_and_reconnect_falls_back_to_polling_on_import_error(bt_manager):
    """When dbus_fast is unavailable, monitor_and_reconnect uses polling fallback."""
    from bt_monitor import monitor_and_reconnect

    with (
        patch("bt_monitor._monitor_dbus", new_callable=AsyncMock, side_effect=ImportError("no dbus_fast")),
        patch("bt_monitor._monitor_polling", new_callable=AsyncMock) as mock_polling,
        patch.dict("sys.modules", {"dbus_fast": None, "dbus_fast.aio": None}),
    ):
        await monitor_and_reconnect(bt_manager)

    mock_polling.assert_awaited_once_with(bt_manager)


@pytest.mark.asyncio
async def test_monitor_and_reconnect_falls_back_on_runtime_error(bt_manager):
    """RuntimeError from D-Bus monitor triggers polling fallback."""
    from bt_monitor import monitor_and_reconnect

    mock_dbus_fast = MagicMock()

    with (
        patch.dict("sys.modules", {"dbus_fast": mock_dbus_fast, "dbus_fast.aio": mock_dbus_fast}),
        patch("bt_monitor._monitor_dbus", new_callable=AsyncMock, side_effect=RuntimeError("D-Bus fail")),
        patch("bt_monitor._monitor_polling", new_callable=AsyncMock) as mock_polling,
    ):
        await monitor_and_reconnect(bt_manager)

    mock_polling.assert_awaited_once_with(bt_manager)


# ---------------------------------------------------------------------------
# _monitor_dbus — device path validation and connect failures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_dbus_raises_when_no_device_path(bt_manager):
    """_monitor_dbus raises RuntimeError if _dbus_device_path is None."""
    from bt_monitor import _monitor_dbus

    bt_manager._dbus_device_path = None

    with pytest.raises(RuntimeError, match="adapter resolution failed"):
        await _monitor_dbus(bt_manager, MagicMock(), MagicMock())


@pytest.mark.asyncio
async def test_monitor_dbus_raises_after_max_introspection_failures(bt_manager):
    """Three consecutive introspection failures trigger RuntimeError."""
    from bt_monitor import _monitor_dbus

    bt_manager._dbus_device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"

    mock_bus = AsyncMock()
    mock_bus.connected = True
    mock_bus.introspect = AsyncMock(side_effect=Exception("introspection failed"))

    MockMessageBus = MagicMock()
    MockMessageBus.return_value.connect = AsyncMock(return_value=mock_bus)
    MockBusType = MagicMock()
    MockBusType.SYSTEM = "system"

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(RuntimeError, match="introspection failed 3 times"),
    ):
        await _monitor_dbus(bt_manager, MockMessageBus, MockBusType)


# ---------------------------------------------------------------------------
# _monitor_polling — management_enabled gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_polling_skips_when_management_disabled(bt_manager):
    """When management_enabled is False, polling sleeps without checking BT state."""
    from bt_monitor import _monitor_polling

    bt_manager.management_enabled = False
    iteration_count = 0

    original_sleep = asyncio.sleep

    async def _counting_sleep(duration):
        nonlocal iteration_count
        iteration_count += 1
        if iteration_count >= 2:
            bt_manager._running = False
        await original_sleep(0)

    with (
        patch("bt_monitor.asyncio.sleep", side_effect=_counting_sleep),
        patch("bluetooth_manager._bt_executor"),
    ):
        await _monitor_polling(bt_manager)

    assert iteration_count >= 2


# ---------------------------------------------------------------------------
# _inner_dbus_monitor — reconnect, churn, heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inner_dbus_monitor_reconnect_cancelled_resets_attempt(bt_manager):
    """When _reconnect_cancelled() is True after connect, reconnect_attempt resets."""
    from bt_monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)
    bt_manager.host.is_subprocess_running = MagicMock(return_value=False)

    disconnect_event = asyncio.Event()
    disconnect_event.set()

    loop = asyncio.get_running_loop()

    # is_device_paired, then connect_device (which triggers cancel)
    async def _fake_executor(executor, fn, *args):
        result = fn(*args) if args else fn()
        return result

    call_count = 0

    def _connect_side_effect():
        nonlocal call_count
        call_count += 1
        bt_manager._cancel_reconnect.set()
        return False

    with (
        patch("bluetooth_manager._bt_executor", new=None),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "connect_device", side_effect=_connect_side_effect),
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
    ):
        # Patch run_in_executor to call functions directly
        async def _mock_run_in_executor(executor, fn, *args):
            return fn(*args) if args else fn()

        with patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor):
            original_reconnect_cancelled = bt_manager._reconnect_cancelled

            def _cancel_and_stop():
                result = original_reconnect_cancelled()
                if result:
                    bt_manager._running = False
                return result

            with patch.object(bt_manager, "_reconnect_cancelled", side_effect=_cancel_and_stop):
                await _inner_dbus_monitor(bt_manager, MagicMock(), disconnect_event, loop)


@pytest.mark.asyncio
async def test_inner_dbus_monitor_heartbeat_detects_missed_disconnect(bt_manager):
    """Heartbeat timeout should detect a missed disconnect signal."""
    from bt_monitor import _inner_dbus_monitor

    bt_manager.connected = True
    bt_manager.management_enabled = True
    bt_manager.check_interval = 0.01

    device_iface = AsyncMock()
    device_iface.get_connected = AsyncMock(return_value=False)

    disconnect_event = asyncio.Event()

    # Track that disconnect was detected (event.set() called) before the
    # reconnect branch clears it again via event.clear().
    disconnect_detected = False
    _orig_set = disconnect_event.set

    def _tracking_set():
        nonlocal disconnect_detected
        disconnect_detected = True
        _orig_set()

    disconnect_event.set = _tracking_set  # type: ignore[method-assign]

    async def _fake_wait_for(coro, *, timeout=None):
        coro.close()
        raise TimeoutError

    loop = asyncio.get_running_loop()

    async def _mock_run_in_executor(executor, fn, *args):
        return fn(*args) if args else fn()

    with (
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("bt_monitor.asyncio.wait_for", side_effect=_fake_wait_for),
        patch("bt_monitor._dbus_get_battery_level", return_value=None),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        # After heartbeat detects disconnect, _handle_reconnect_failure → True exits immediately
        patch.object(bt_manager, "_handle_reconnect_failure", return_value=True),
    ):
        bt_manager.host = MagicMock()
        bt_manager.host.get_status_value = MagicMock(return_value=False)
        bt_manager.host.is_subprocess_running = MagicMock(return_value=False)
        bt_manager.host.send_subprocess_command = AsyncMock()
        bt_manager.host.stop_subprocess = AsyncMock()

        await _inner_dbus_monitor(bt_manager, device_iface, disconnect_event, loop)

    assert bt_manager.connected is False
    assert disconnect_detected is True


@pytest.mark.asyncio
async def test_inner_dbus_monitor_successful_reconnect_starts_subprocess(bt_manager):
    """Successful reconnect should start the subprocess and return for re-subscription."""
    from bt_monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)
    bt_manager.host.is_subprocess_running = MagicMock(return_value=False)
    bt_manager.host.start_subprocess = AsyncMock()

    disconnect_event = asyncio.Event()
    disconnect_event.set()

    loop = asyncio.get_running_loop()

    # is_device_paired → True, connect_device → True
    executor_results = iter([True, True])

    async def _mock_run_in_executor(executor, fn, *args):
        return next(executor_results)

    with (
        patch("bluetooth_manager._bt_executor", new=None),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "_handle_reconnect_failure", return_value=False),
        patch.object(bt_manager, "_reconnect_cancelled", return_value=False),
        patch.object(bt_manager, "_record_reconnect"),
        patch("bt_monitor._correct_other_devices_routing", new_callable=AsyncMock),
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("bt_monitor.asyncio.ensure_future"),
    ):
        await _inner_dbus_monitor(bt_manager, AsyncMock(), disconnect_event, loop)

    assert bt_manager.connected is True
    bt_manager.host.start_subprocess.assert_awaited_once()


@pytest.mark.asyncio
async def test_inner_dbus_monitor_handle_reconnect_failure_returns(bt_manager):
    """When _handle_reconnect_failure returns True, _inner_dbus_monitor should exit."""
    from bt_monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)

    disconnect_event = asyncio.Event()
    disconnect_event.set()

    loop = asyncio.get_running_loop()

    # is_device_paired → True
    async def _mock_run_in_executor(executor, fn, *args):
        return True

    with (
        patch("bluetooth_manager._bt_executor", new=None),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "_handle_reconnect_failure", return_value=True),
        patch("bt_monitor.asyncio.sleep", new_callable=AsyncMock),
    ):
        # Should return without attempting connect
        await _inner_dbus_monitor(bt_manager, AsyncMock(), disconnect_event, loop)


# ---------------------------------------------------------------------------
# Exponential backoff (exercised through BluetoothManager, verified here
# for completeness of bt_monitor coverage)
# ---------------------------------------------------------------------------


def test_reconnect_delay_escalates_correctly(bt_manager):
    """Verify exponential backoff: linear for 1-3, doubling after."""
    bt_manager.check_interval = 15
    assert bt_manager._reconnect_delay(1) == 15
    assert bt_manager._reconnect_delay(2) == 15
    assert bt_manager._reconnect_delay(3) == 15
    assert bt_manager._reconnect_delay(4) == 30
    assert bt_manager._reconnect_delay(5) == 60
    assert bt_manager._reconnect_delay(6) == 120


def test_reconnect_delay_caps_at_300(bt_manager):
    """Backoff never exceeds 300 seconds regardless of attempt count."""
    bt_manager.check_interval = 15
    assert bt_manager._reconnect_delay(100) == 300.0


# ---------------------------------------------------------------------------
# _record_reconnect and _check_reconnect_churn
# ---------------------------------------------------------------------------


def test_record_reconnect_adds_timestamp(bt_manager):
    """_record_reconnect appends a monotonic timestamp."""
    assert len(bt_manager._reconnect_timestamps) == 0

    bt_manager._record_reconnect()

    assert len(bt_manager._reconnect_timestamps) == 1


def test_record_reconnect_prunes_outside_window(bt_manager):
    """Timestamps outside the churn window are pruned on record."""
    bt_manager._CHURN_WINDOW = 10
    bt_manager._reconnect_timestamps = [time.monotonic() - 20]

    bt_manager._record_reconnect()

    assert len(bt_manager._reconnect_timestamps) == 1


def test_check_reconnect_churn_returns_false_below_threshold(bt_manager):
    """No churn release when reconnect count is below threshold."""
    bt_manager._CHURN_THRESHOLD = 5
    bt_manager._CHURN_WINDOW = 60
    now = time.monotonic()
    bt_manager._reconnect_timestamps = [now - 1, now - 2]

    assert bt_manager._check_reconnect_churn() is False
    assert bt_manager.management_enabled is True


def test_check_reconnect_churn_disables_management_at_threshold(bt_manager):
    """Churn detection disables management when threshold is reached."""
    bt_manager._CHURN_THRESHOLD = 3
    bt_manager._CHURN_WINDOW = 60
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True

    now = time.monotonic()
    bt_manager._reconnect_timestamps = [now - 2, now - 1, now]

    with patch("sendspin_bridge.services.bluetooth.persist_device_released"):
        result = bt_manager._check_reconnect_churn()

    assert result is True
    assert bt_manager.management_enabled is False


def test_check_reconnect_churn_disabled_threshold_zero(bt_manager):
    """Churn detection is disabled when threshold is 0."""
    bt_manager._CHURN_THRESHOLD = 0
    bt_manager._reconnect_timestamps = [time.monotonic()] * 10

    assert bt_manager._check_reconnect_churn() is False


# ---------------------------------------------------------------------------
# _reconnect_cancelled interaction
# ---------------------------------------------------------------------------


def test_reconnect_cancelled_when_cancel_set(bt_manager):
    """_reconnect_cancelled returns True when cancel event is set."""
    bt_manager._cancel_reconnect.set()

    assert bt_manager._reconnect_cancelled() is True


def test_reconnect_cancelled_when_management_disabled(bt_manager):
    """_reconnect_cancelled returns True when management_enabled is False."""
    bt_manager.management_enabled = False

    assert bt_manager._reconnect_cancelled() is True


def test_reconnect_cancelled_false_when_active(bt_manager):
    """_reconnect_cancelled returns False when management is active and cancel is clear."""
    bt_manager._cancel_reconnect.clear()
    bt_manager.management_enabled = True

    assert bt_manager._reconnect_cancelled() is False
