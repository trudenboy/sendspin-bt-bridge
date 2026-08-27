"""Tests for bt_monitor.py — D-Bus signal and polling fallback monitoring loops.

bt_monitor functions receive a BluetoothManager instance and operate on its
attributes.  All D-Bus, Bluetooth, and PulseAudio interactions are mocked.

Note: bt_monitor uses *lazy imports* inside function bodies (e.g.
``from services.device_registry import get_active_clients_snapshot``),
so patches must target the *source module*, not ``bt_monitor.<name>``.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.support.fake_dbus import FakeBlueZ, attach, bluez_knowing

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
def bt_manager(installed_bluez):
    """Create a BluetoothManager with reasonable defaults for testing.

    The fake reports no default controller (``show`` → empty) so adapter
    resolution lands on no adapter / no D-Bus path, matching the
    historical ``check_output("")`` guard.
    """
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
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
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = bt_manager
    client.get_subprocess_pid.return_value = 123
    client.bluetooth_sink_name = "bluez_sink.AA_BB"

    with (
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_not_called()


@pytest.mark.asyncio
async def test_correct_other_devices_routing_moves_other_clients(bt_manager):
    """Clients belonging to other managers get their sink routing corrected."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    other_mgr = MagicMock()
    client = MagicMock()
    client.bt_manager = other_mgr
    client.get_subprocess_pid.return_value = 456
    client.bluetooth_sink_name = "bluez_sink.CC_DD"
    client.player_name = "OtherSpeaker"

    with (
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
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
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = MagicMock()  # different manager
    client.get_subprocess_pid.return_value = None
    client.bluetooth_sink_name = "bluez_sink.XX_YY"

    with (
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_not_called()


@pytest.mark.asyncio
async def test_correct_other_devices_routing_skips_client_without_sink(bt_manager):
    """Clients with empty bluetooth_sink_name are skipped."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = MagicMock()
    client.get_subprocess_pid.return_value = 789
    client.bluetooth_sink_name = ""

    with (
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_not_called()


@pytest.mark.asyncio
async def test_correct_other_devices_routing_handles_move_exception(bt_manager):
    """amove_pid_sink_inputs exceptions are caught and logged, not propagated."""
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = MagicMock()
    client.get_subprocess_pid.return_value = 111
    client.bluetooth_sink_name = "bluez_sink.EE_FF"
    client.player_name = "FailSpeaker"

    with (
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
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
    from sendspin_bridge.bluetooth.monitor import _correct_other_devices_routing

    client = MagicMock()
    client.bt_manager = None
    client.get_subprocess_pid.return_value = 222
    client.bluetooth_sink_name = "bluez_sink.GG_HH"

    with (
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.services.bluetooth.device_registry.get_active_clients_snapshot", return_value=[client]),
        patch(
            "sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", new_callable=AsyncMock, return_value=0
        ) as mock_move,
    ):
        await _correct_other_devices_routing(bt_manager)

    mock_move.assert_awaited_once_with(222, "bluez_sink.GG_HH")


# ---------------------------------------------------------------------------
# monitor_and_reconnect — one transport, and what it says when it fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bus_that_cannot_be_reached_is_said_on_the_device_card(bt_manager):
    """There is no polling path behind it any more, so it must be reported.

    Three failed attempts is what used to drop the monitor into bluetoothctl
    polling; now it marks the speaker's Bluetooth as unavailable and says why,
    instead of asking a second transport the same question every five seconds.
    """
    from sendspin_bridge.bluetooth.monitor import _monitor_dbus
    from tests.support.fake_dbus import attach

    updates: list[dict] = []
    bt_manager.host = MagicMock()
    bt_manager.host.update_status.side_effect = lambda payload: updates.append(payload)

    bluez = FakeBlueZ()
    bluez.connected = False  # the system bus is not there
    device = attach(bt_manager, bluez)
    for _ in range(3):
        await device.state()  # three failed attempts is the threshold

    async def _stop_soon():
        await asyncio.sleep(0.05)
        bt_manager.shutdown()

    asyncio.ensure_future(_stop_soon())
    await _monitor_dbus(bt_manager)

    reported = [u for u in updates if u.get("bluetooth_available") is False]
    assert reported, f"nothing told the card the bus is unreachable: {updates}"
    assert reported[0]["last_error"] == "bluetooth_transport_unavailable"


# ---------------------------------------------------------------------------
# _monitor_dbus — device path validation and connect failures
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _monitor_polling — management_enabled gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inner_dbus_monitor_defers_reconnect_while_bt_operation_held(bt_manager):
    """D-Bus path: with the lock held, the monitor defers — connect_device is
    never called and the attempt does not feed the auto-disable counter."""
    from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.check_interval = 0.01
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)
    bt_manager.host.is_subprocess_running = MagicMock(return_value=False)

    calls = {"paired": 0, "connect": 0, "handle_failure": 0}
    sleeps = {"n": 0}

    async def _counting_sleep(duration):
        sleeps["n"] += 1
        if sleeps["n"] >= 3:
            bt_manager._running = False

    held = AdapterHandle().try_lease("scan")  # hold the adapter like a UI scan would
    assert held is not None
    try:
        with (
            patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", side_effect=_counting_sleep),
            patch.object(
                bt_manager,
                "is_device_paired",
                side_effect=lambda: (calls.__setitem__("paired", calls["paired"] + 1), True)[1],
            ),
            patch.object(
                bt_manager,
                "connect_device",
                side_effect=lambda: (calls.__setitem__("connect", calls["connect"] + 1), False)[1],
            ),
            patch.object(
                bt_manager,
                "_handle_reconnect_failure",
                side_effect=lambda _n: (calls.__setitem__("handle_failure", calls["handle_failure"] + 1), False)[1],
            ),
        ):
            await _inner_dbus_monitor(
                bt_manager, MagicMock(), asyncio.Event(), asyncio.Event(), asyncio.get_running_loop()
            )
    finally:
        held.release()

    assert calls == {"paired": 0, "connect": 0, "handle_failure": 0}
    assert sleeps["n"] >= 1  # the defer slept instead of spinning hot


# ---------------------------------------------------------------------------
# _inner_dbus_monitor — reconnect, churn, heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inner_dbus_monitor_reconnect_cancelled_resets_attempt(bt_manager):
    """When _reconnect_cancelled() is True after connect, reconnect_attempt resets."""
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

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
        patch("sendspin_bridge.bluetooth.manager._bt_executor", new=None),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "connect_device", side_effect=_connect_side_effect),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
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
                await _inner_dbus_monitor(bt_manager, MagicMock(), disconnect_event, asyncio.Event(), loop)


@pytest.mark.asyncio
async def test_inner_dbus_monitor_heartbeat_detects_missed_disconnect(bt_manager):
    """Heartbeat timeout should detect a missed disconnect signal."""
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

    bt_manager.connected = True
    bt_manager.management_enabled = True
    bt_manager.check_interval = 0.01

    # BlueZ says the speaker is gone; the signal that should have said so
    # never arrived, which is what the heartbeat exists to catch.
    device = attach(bt_manager, bluez_knowing(bt_manager, connected=False))

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
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.wait_for", side_effect=_fake_wait_for),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        # After heartbeat detects disconnect, _handle_reconnect_failure → True exits immediately
        patch.object(bt_manager, "handle_reconnect_failure", return_value=True),
    ):
        bt_manager.host = MagicMock()
        bt_manager.host.get_status_value = MagicMock(return_value=False)
        bt_manager.host.is_subprocess_running = MagicMock(return_value=False)
        bt_manager.host.send_subprocess_command = AsyncMock()
        bt_manager.host.stop_subprocess = AsyncMock()

        await _inner_dbus_monitor(bt_manager, device, disconnect_event, asyncio.Event(), loop)

    assert bt_manager.connected is False
    assert disconnect_detected is True


@pytest.mark.asyncio
async def test_inner_dbus_monitor_successful_reconnect_starts_subprocess(bt_manager):
    """Successful reconnect should start the subprocess and return for re-subscription."""
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)
    bt_manager.host.is_subprocess_running = MagicMock(return_value=False)
    bt_manager.host.start_subprocess = AsyncMock()

    disconnect_event = asyncio.Event()
    disconnect_event.set()

    loop = asyncio.get_running_loop()

    # Transparent executor: run the (patched) method so behaviour follows the
    # return values, not a fixed call count.  ``_handle_reconnect_failure`` is
    # now offloaded via ``run_in_executor`` too, so a canned-sequence mock would
    # be coupled to the exact number of executor calls.
    async def _mock_run_in_executor(executor, fn, *args):
        return fn(*args)

    with (
        patch("sendspin_bridge.bluetooth.manager._bt_executor", new=None),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "connect_device", return_value=True),
        patch.object(bt_manager, "_handle_reconnect_failure", return_value=False),
        patch.object(bt_manager, "_reconnect_cancelled", return_value=False),
        patch.object(bt_manager, "_record_reconnect"),
        patch("sendspin_bridge.bluetooth.monitor._correct_other_devices_routing", new_callable=AsyncMock),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.ensure_future"),
    ):
        await _inner_dbus_monitor(bt_manager, AsyncMock(), disconnect_event, asyncio.Event(), loop)

    assert bt_manager.connected is True
    bt_manager.host.start_subprocess.assert_awaited_once()


@pytest.mark.asyncio
async def test_inner_dbus_monitor_handle_reconnect_failure_returns(bt_manager):
    """When _handle_reconnect_failure returns True, _inner_dbus_monitor should exit."""
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

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
        patch("sendspin_bridge.bluetooth.manager._bt_executor", new=None),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "handle_reconnect_failure", return_value=True),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
    ):
        # Should return without attempting connect
        await _inner_dbus_monitor(bt_manager, AsyncMock(), disconnect_event, asyncio.Event(), loop)


@pytest.mark.asyncio
async def test_handle_reconnect_failure_runs_off_the_loop(bt_manager):
    """The recovery ladder + config write in ``_handle_reconnect_failure`` must
    be dispatched via ``run_in_executor`` (off the loop), never inline — an
    inline call would freeze every other device's IPC during a USB reset."""
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)

    disconnect_event = asyncio.Event()
    disconnect_event.set()
    loop = asyncio.get_running_loop()

    dispatched = []

    async def _recording_executor(executor, fn, *args):
        dispatched.append(getattr(fn, "_mock_name", None) or getattr(fn, "__name__", None))
        return fn(*args)

    with (
        patch("sendspin_bridge.bluetooth.manager._bt_executor", new=None),
        patch.object(loop, "run_in_executor", side_effect=_recording_executor),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "handle_reconnect_failure", return_value=True),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
    ):
        await _inner_dbus_monitor(bt_manager, AsyncMock(), disconnect_event, asyncio.Event(), loop)

    assert "handle_reconnect_failure" in dispatched, "reconnect-failure handling was not offloaded"


# ---------------------------------------------------------------------------
# Issue #312: external connect interrupts the failed-reconnect backoff sleep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inner_dbus_monitor_connect_event_wakes_backoff(bt_manager):
    """A `PropertiesChanged: Connected` arriving during the backoff sleep
    must wake the loop immediately so audio is configured at once,
    rather than waiting out the full (potentially 5-minute) delay
    (issue #312).
    """
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)
    bt_manager.host.is_subprocess_running = MagicMock(return_value=False)
    bt_manager.host.start_subprocess = AsyncMock()

    disconnect_event = asyncio.Event()
    disconnect_event.set()
    connect_event = asyncio.Event()
    # Pre-set: the wait_for() inside the backoff path resolves immediately.
    connect_event.set()

    device_iface = AsyncMock()
    # After the early wake, the re-read finds the device connected.
    device_iface.get_connected = AsyncMock(return_value=True)

    loop = asyncio.get_running_loop()

    # is_device_paired → True, connect_device → False (forces the
    # failed-reconnect branch where the backoff sleep lives).
    executor_results = iter([True, False])

    async def _mock_run_in_executor(executor, fn, *args):
        try:
            return next(executor_results)
        except StopIteration:
            return None

    sleep_calls: list[float] = []

    async def _track_sleep(delay):
        sleep_calls.append(delay)

    with (
        patch("sendspin_bridge.bluetooth.manager._bt_executor", new=None),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "_handle_reconnect_failure", return_value=False),
        patch.object(bt_manager, "_reconnect_cancelled", return_value=False),
        patch.object(bt_manager, "_record_reconnect"),
        patch.object(bt_manager, "_reconnect_delay", return_value=300.0),
        patch("sendspin_bridge.bluetooth.monitor._correct_other_devices_routing", new_callable=AsyncMock),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", side_effect=_track_sleep),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.ensure_future"),
    ):
        await _inner_dbus_monitor(
            bt_manager,
            attach(bt_manager, bluez_knowing(bt_manager, connected=True)),
            disconnect_event,
            connect_event,
            loop,
        )

    # The function must have configured audio and started the subprocess
    # (the "External reconnect detected" path), which only happens when
    # the backoff was woken early via connect_event.
    bt_manager.host.start_subprocess.assert_awaited_once()
    # The event is cleared after consumption so subsequent backoffs aren't
    # spuriously interrupted by a stale wake.
    assert connect_event.is_set() is False


@pytest.mark.asyncio
async def test_inner_dbus_monitor_backoff_falls_through_on_timeout(bt_manager):
    """Without an external connect, the backoff branch must still
    fall through into the post-sleep state re-check (i.e. wait_for
    raising TimeoutError is swallowed). Issue #312."""
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

    bt_manager.connected = False
    bt_manager.management_enabled = True
    bt_manager.host = MagicMock()
    bt_manager.host.get_status_value = MagicMock(return_value=False)
    bt_manager.host.is_subprocess_running = MagicMock(return_value=False)
    bt_manager.host.start_subprocess = AsyncMock()

    disconnect_event = asyncio.Event()
    disconnect_event.set()
    connect_event = asyncio.Event()  # never set — wait_for will time out

    device_iface = AsyncMock()
    # After the timeout, the re-read still reports disconnected.
    device_iface.get_connected = AsyncMock(return_value=False)

    loop = asyncio.get_running_loop()

    # Each disconnected-branch iteration reconnects: is_device_paired → True,
    # connect_device → False so the backoff path is exercised.  Stop the outer
    # loop after one full fall-through to keep the test deterministic.  The
    # executor mock runs the (patched) method directly, so this is robust to
    # ``_handle_reconnect_failure`` now being offloaded via run_in_executor too.
    iteration = {"n": 0}

    def _is_paired_and_maybe_stop():
        iteration["n"] += 1
        if iteration["n"] > 1:
            bt_manager._running = False
        return True

    async def _mock_run_in_executor(executor, fn, *args):
        return fn(*args)

    async def _instant_sleep(_delay):
        return None

    async def _instant_wait_for(_coro, *, timeout=None):
        _coro.close()
        raise TimeoutError

    with (
        patch("sendspin_bridge.bluetooth.manager._bt_executor", new=None),
        patch.object(loop, "run_in_executor", side_effect=_mock_run_in_executor),
        patch.object(bt_manager, "is_device_paired", side_effect=_is_paired_and_maybe_stop),
        # The real connect ladder is what makes this test's backoff happen; it
        # now reaches the audio server on its way, which a unit test has none of.
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=False),
        patch.object(bt_manager, "connect_device", return_value=False),
        patch.object(bt_manager, "_handle_reconnect_failure", return_value=False),
        patch.object(bt_manager, "_reconnect_cancelled", return_value=False),
        patch.object(bt_manager, "_record_reconnect"),
        patch.object(bt_manager, "_reconnect_delay", return_value=0.001),
        patch("sendspin_bridge.bluetooth.monitor._correct_other_devices_routing", new_callable=AsyncMock),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", side_effect=_instant_sleep),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.wait_for", side_effect=_instant_wait_for),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.ensure_future"),
    ):
        await _inner_dbus_monitor(
            bt_manager,
            attach(bt_manager, bluez_knowing(bt_manager, connected=False)),
            disconnect_event,
            connect_event,
            loop,
        )

    # No external connect arrived → audio configuration must NOT have
    # been called via the backoff-wake path (the loop exits via
    # _running=False on the second iteration).
    bt_manager.host.start_subprocess.assert_not_called()


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


# ---------------------------------------------------------------------------
# #322: repeated reconnect attempts must not flood the log at WARNING
# ---------------------------------------------------------------------------


def test_reconnect_attempt_logging_downgrades_after_first(caplog):
    import logging

    from sendspin_bridge.bluetooth.monitor import _log_reconnect_attempt

    with caplog.at_level(logging.DEBUG, logger="sendspin_bridge.bluetooth.monitor"):
        _log_reconnect_attempt("Speaker", 1)
        _log_reconnect_attempt("Speaker", 2)
        _log_reconnect_attempt("Speaker", 17)

    levels = [r.levelno for r in caplog.records if "reconnecting" in r.message]
    assert levels[0] == logging.WARNING  # first attempt is visible
    assert levels[1] == logging.DEBUG  # subsequent attempts are quiet
    assert levels[2] == logging.DEBUG


@pytest.mark.asyncio
async def test_a_retried_monitor_cycle_does_not_stack_watchers(bt_manager):
    """Each cycle registers a fresh handler bound to that cycle's events.

    Without taking the last one off, a cycle that fails and retries would have
    every Connected change processed once per attempt — including a duplicate
    routing correction for every other speaker.
    """
    from sendspin_bridge.bluetooth.monitor import _monitor_dbus

    device = attach(bt_manager, bluez_knowing(bt_manager, connected=False))
    cycles = {"n": 0}

    async def _one_cycle_then_stop(*_args, **_kwargs):
        cycles["n"] += 1
        if cycles["n"] >= 3:
            bt_manager.shutdown()
        raise RuntimeError("cycle failed")

    with (
        patch("sendspin_bridge.bluetooth.monitor._inner_dbus_monitor", side_effect=_one_cycle_then_stop),
        patch("sendspin_bridge.bluetooth.monitor.asyncio.sleep", new_callable=AsyncMock),
    ):
        await _monitor_dbus(bt_manager)

    assert cycles["n"] >= 3, "the loop did not retry"
    assert device.watcher_count == 0, "each retried cycle left its handler behind"
