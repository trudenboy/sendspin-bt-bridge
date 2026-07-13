"""Bluetooth monitoring loops — D-Bus signal and polling fallback.

Extracted from ``bluetooth_manager.py``.  Each function receives the
``BluetoothManager`` instance as its first argument so it can access the
same attributes and helpers as the original methods.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sendspin_bridge.bluetooth.dbus import _dbus_get_battery_level
from sendspin_bridge.services.diagnostics.internal_events import DeviceEventType

if TYPE_CHECKING:
    from sendspin_bridge.bluetooth.manager import BluetoothManager

UTC = timezone.utc

logger = logging.getLogger(__name__)

# Delay (seconds) after a BT device connects before correcting sink routing.
# Gives PulseAudio time to create the new sink and module-rescue-streams to act.
_SINK_CORRECTION_DELAY = 3

# Strong references to fire-and-forget background tasks.  A bare
# ``asyncio.ensure_future`` keeps no reference, so the event loop may garbage
# collect the task mid-flight and any exception it raises is silently dropped.
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:
    """Schedule *coro* fire-and-forget while retaining a reference and logging
    any exception it raises (with traceback) when it finishes."""
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("Background sink-routing task failed: %s", t.exception(), exc_info=t.exception())

    task.add_done_callback(_on_done)


def _log_reconnect_attempt(device_name: str, attempt: int) -> None:
    """Log a reconnect attempt at WARNING on the first try, DEBUG thereafter.

    A speaker left off for a long time keeps retrying with a saturated
    back-off; logging every attempt at WARNING floods the log (#322).  The
    first attempt still surfaces as a warning so the disconnect is visible.
    """
    level = logging.WARNING if attempt <= 1 else logging.DEBUG
    logger.log(level, "[%s] Disconnected, reconnecting... (attempt %s)", device_name, attempt)


async def _standby_sleep(mgr: BluetoothManager, seconds: float = 5) -> None:
    """Sleep interruptibly — returns early when ``signal_standby_wake()`` fires."""
    evt = mgr._standby_wake_event
    if evt is None:
        await asyncio.sleep(seconds)
        return
    evt.clear()
    try:
        await asyncio.wait_for(evt.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def _correct_other_devices_routing(triggering_mgr: BluetoothManager) -> None:
    """After a BT device connects, correct PA sink routing for all OTHER running players.

    PulseAudio's ``module-rescue-streams`` may silently move an existing
    stream to a newly-appeared sink.  We wait briefly for PA to settle,
    then verify each running subprocess is still on its expected sink.
    """
    await asyncio.sleep(_SINK_CORRECTION_DELAY)

    from sendspin_bridge.services.audio.pulse import amove_pid_sink_inputs
    from sendspin_bridge.services.bluetooth.device_registry import get_active_clients_snapshot

    clients = get_active_clients_snapshot()
    for client in clients:
        if getattr(client, "bt_manager", None) is triggering_mgr:
            continue
        pid = client.get_subprocess_pid()
        sink = client.bluetooth_sink_name
        if pid is None or not sink:
            continue
        try:
            moved = await amove_pid_sink_inputs(pid, sink)
            if moved:
                logger.info(
                    "[%s] Sink routing corrected: %d stream(s) moved back → %s",
                    client.player_name,
                    moved,
                    sink,
                )
        except Exception as exc:
            logger.debug("[%s] Sink routing correction failed: %s", client.player_name, exc)


async def _finish_auto_reclaim(mgr: BluetoothManager, loop, *, connected: bool | None = None) -> bool:
    """Reclaim after auto-release and bring the player back up (#349/#350).

    ``BluetoothManager.maybe_auto_reclaim`` performs the state flip
    (gated on ``bt_released_by == "auto"``, an established link and the
    quiet period); this helper then mirrors the external-reconnect path:
    configure audio, restart the player subprocess and correct sink
    routing for the other devices.
    """
    if not mgr.maybe_auto_reclaim(connected=connected):
        return False
    from sendspin_bridge.bluetooth.manager import _bt_executor

    await loop.run_in_executor(_bt_executor, mgr.configure_bluetooth_audio)
    mgr._record_reconnect()
    if mgr.host:
        mgr.host.update_status(
            {
                "bluetooth_connected": True,
                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
            }
        )
        logger.info("BT management reclaimed for %s, starting sendspin...", mgr.device_name)
        await mgr.host.start_subprocess()
    _spawn_background(_correct_other_devices_routing(mgr))
    return True


async def _poll_auto_reclaim(mgr: BluetoothManager, loop) -> bool:
    """Polling-monitor variant of the auto-reclaim check.

    The polling path has no PropertiesChanged handler keeping
    ``mgr.connected`` fresh while management is released, so poll the
    live state — rate-limited to the regular ``check_interval`` cadence
    via ``mgr.last_check`` (idle while released, so it's free to reuse).
    """
    if mgr.host is None or mgr.host.get_status_value("bt_released_by") != "auto":
        return False
    now = time.time()
    if now - mgr.last_check < mgr.check_interval:
        return False
    mgr.last_check = now
    from sendspin_bridge.bluetooth.manager import _bt_executor

    connected = await loop.run_in_executor(_bt_executor, mgr.is_device_connected)
    if not connected:
        return False
    return await _finish_auto_reclaim(mgr, loop, connected=True)


async def monitor_and_reconnect(mgr: BluetoothManager) -> None:
    """Continuously monitor BT connection and reconnect if needed.

    Tries D-Bus PropertiesChanged signals (dbus-fast) for instant disconnect
    detection; falls back to bluetoothctl polling if dbus-fast is unavailable
    or if the D-Bus environment doesn't support signal subscriptions.
    """
    logger.info("[%s] monitor_and_reconnect task started", mgr.device_name)
    # Create an asyncio.Event in the running loop for standby-wake signaling.
    mgr._standby_wake_event = asyncio.Event()
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        await _monitor_dbus(mgr, MessageBus, BusType)
    except (ImportError, RuntimeError) as e:
        logger.info("[%s] D-Bus monitor unavailable (%s) — using bluetoothctl polling", mgr.device_name, e)
        await _monitor_polling(mgr)


async def _monitor_polling(mgr: BluetoothManager) -> None:
    """Legacy bluetoothctl polling-based monitor (fallback)."""
    from sendspin_bridge.bluetooth.manager import _bt_executor

    loop = asyncio.get_running_loop()
    iteration = 0
    reconnect_attempt = 0
    while mgr._running:
        iteration += 1
        try:
            if not mgr.management_enabled:
                if await _poll_auto_reclaim(mgr, loop):
                    continue
                await asyncio.sleep(5)
                continue

            if mgr.host and mgr.host.get_status_value("bt_standby") and not mgr.host.get_status_value("bt_waking"):
                await _standby_sleep(mgr)
                continue

            current_time = time.time()
            if current_time - mgr.last_check >= mgr.check_interval:
                mgr.last_check = current_time
                logger.debug("[%s] BT poll #%s", mgr.device_name, iteration)

                connected = await loop.run_in_executor(_bt_executor, mgr.is_device_connected)
                logger.debug("[%s] BT connected=%s", mgr.device_name, connected)

                if mgr.host:
                    if connected != mgr.host.get_status_value("bluetooth_connected"):
                        mgr.host.update_status(
                            {
                                "bluetooth_connected": connected,
                                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                            }
                        )

                if not connected:
                    mgr.battery_level = None
                    paired = await loop.run_in_executor(_bt_executor, mgr.is_device_paired)
                    mgr.paired = paired
                    reconnect_attempt += 1
                    if mgr.host:
                        mgr.host.update_status(
                            {
                                "reconnecting": True,
                                "reconnect_attempt": reconnect_attempt,
                            }
                        )

                    # Offload: this may run the adapter-recovery ladder
                    # (USB unbind/rebind) and a config write — never inline
                    # on the loop.
                    if await loop.run_in_executor(_bt_executor, mgr._handle_reconnect_failure, reconnect_attempt):
                        reconnect_attempt = 0
                        continue

                    if mgr.host and mgr.host.is_subprocess_running():
                        logger.info("BT disconnected for %s, stopping sendspin daemon...", mgr.device_name)
                        is_grouped = bool(mgr.host.get_status_value("group_id"))
                        if not is_grouped:
                            await mgr.host.send_subprocess_command({"cmd": "pause"})
                            await asyncio.sleep(0.2)
                        await mgr.host.stop_subprocess()

                    _log_reconnect_attempt(mgr.device_name, reconnect_attempt)
                    success = await loop.run_in_executor(_bt_executor, mgr.connect_device)
                    if mgr._reconnect_cancelled():
                        reconnect_attempt = 0
                        continue
                    if success and mgr.host:
                        completed_attempt = reconnect_attempt
                        reconnect_attempt = 0
                        mgr._record_reconnect()
                        mgr.host.update_status({"reconnecting": False, "reconnect_attempt": 0})
                        mgr._publish_client_event(
                            DeviceEventType.BLUETOOTH_RECONNECTED,
                            message="Bluetooth reconnect succeeded",
                            details={"attempt": completed_attempt},
                        )
                        logger.info("BT reconnected for %s, starting sendspin...", mgr.device_name)
                        await mgr.host.start_subprocess()
                        _spawn_background(_correct_other_devices_routing(mgr))
                    else:
                        delay = mgr._reconnect_delay(reconnect_attempt)
                        mgr._publish_client_event(
                            DeviceEventType.BLUETOOTH_RECONNECT_FAILED,
                            level="warning",
                            message="Bluetooth reconnect attempt failed",
                            details={"attempt": reconnect_attempt, "next_retry_delay": delay},
                        )
                        mgr.last_check = time.time() + delay - mgr.check_interval
                        logger.debug("[%s] Backoff: next attempt in %.0fs", mgr.device_name, delay)
                else:
                    if mgr.host and mgr.host.get_status_value("reconnecting"):
                        mgr.host.update_status({"reconnecting": False, "reconnect_attempt": 0})
                    reconnect_attempt = 0

                    # Handle auto-reconnect: device connected externally
                    if mgr.host and not mgr.host.is_subprocess_running():
                        logger.info(
                            "[%s] Device connected but player not running — configuring audio...",
                            mgr.device_name,
                        )
                        await loop.run_in_executor(_bt_executor, mgr.configure_bluetooth_audio)
                        mgr._record_reconnect()
                        if mgr.host.bluetooth_sink_name:
                            logger.info("[%s] Auto-reconnect: starting player", mgr.device_name)
                            await mgr.host.start_subprocess()
                            _spawn_background(_correct_other_devices_routing(mgr))

                    # Read battery level (None if device doesn't support it).
                    # Synchronous D-Bus round-trip → run off the loop.
                    mgr.battery_level = await loop.run_in_executor(None, _dbus_get_battery_level, mgr._dbus_device_path)

            await asyncio.sleep(5)
        except Exception:
            logger.exception("Error in Bluetooth poll monitor")
            await asyncio.sleep(10)


async def _monitor_dbus(mgr: BluetoothManager, MessageBus, BusType) -> None:
    """D-Bus PropertiesChanged signal-based monitor (preferred path).

    Raises RuntimeError after 3 consecutive connection failures so
    monitor_and_reconnect() can fall back to bluetoothctl polling.
    """
    if not mgr._dbus_device_path:
        raise RuntimeError("D-Bus device path unavailable because adapter resolution failed")
    loop = asyncio.get_running_loop()
    connect_failures = 0
    _MAX_CONNECT_FAILURES = 3
    logger.info("[%s] D-Bus monitor started (path=%s)", mgr.device_name, mgr._dbus_device_path)

    bus = None

    while mgr._running:
        bus_needs_reconnect = bus is None or not bus.connected
        try:
            if bus_needs_reconnect:
                if bus is not None:
                    try:
                        bus.disconnect()
                    except Exception as exc:
                        logger.debug("D-Bus disconnect before reconnect failed: %s", exc)
                bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

            # Introspect the device object
            try:
                assert bus is not None  # guaranteed by reconnect block above
                introspection = await bus.introspect("org.bluez", mgr._dbus_device_path)
                proxy = bus.get_proxy_object("org.bluez", mgr._dbus_device_path, introspection)
                device_iface = proxy.get_interface("org.bluez.Device1")
                props_iface = proxy.get_interface("org.freedesktop.DBus.Properties")
            except Exception as e:
                connect_failures += 1
                logger.debug(
                    "[%s] D-Bus device not available (%s), attempt %s/%s",
                    mgr.device_name,
                    e,
                    connect_failures,
                    _MAX_CONNECT_FAILURES,
                )
                if connect_failures >= _MAX_CONNECT_FAILURES:
                    raise RuntimeError(f"D-Bus device introspection failed {connect_failures} times: {e}")
                await asyncio.sleep(5)
                continue

            connect_failures = 0

            # Read initial connected state.  ``_apply_connected_state``
            # routes the assignment through the on_connected /
            # on_disconnected callback fire so MprisPlayer registration
            # (and any other transition-driven hook) lands on the
            # initial D-Bus monitor startup, not just polling cycles.
            try:
                mgr._apply_connected_state(bool(await device_iface.get_connected()))
            except Exception as exc:
                logger.debug("get_connected() failed: %s", exc)
                mgr._apply_connected_state(False)
            if mgr.host:
                mgr.host.update_status(
                    {
                        "bluetooth_connected": mgr.connected,
                        "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                    }
                )

            disconnect_event = asyncio.Event()
            if not mgr.connected:
                disconnect_event.set()
            # Mirrors disconnect_event for the connect direction so a
            # PropertiesChanged: Connected arriving during the
            # failed-reconnect backoff sleep wakes the loop immediately
            # (#312 — battery-powered speakers that auto-reconnect would
            # otherwise wait out the remainder of the saturated 5-minute
            # backoff before the bridge configured audio).
            connect_event = asyncio.Event()

            def _make_props_handler(disc_evt, conn_evt):
                def on_props_changed(iface_name, changed, _invalidated):
                    if iface_name != "org.bluez.Device1" or "Connected" not in changed:
                        return
                    new_connected = bool(changed["Connected"].value)
                    if new_connected == mgr.connected:
                        return
                    # Routes through the on_connected / on_disconnected
                    # callback fire — the primary path that reaches the
                    # MprisPlayer registration on Linux hosts where D-Bus
                    # PropertiesChanged drives the connect detection.
                    mgr._apply_connected_state(new_connected)
                    ts = datetime.now(tz=UTC).isoformat()
                    if mgr.host:
                        mgr.host.update_status(
                            {
                                "bluetooth_connected": new_connected,
                                "bluetooth_connected_at": ts,
                            }
                        )
                    if not new_connected:
                        loop.call_soon_threadsafe(disc_evt.set)
                        logger.warning("[%s] PropertiesChanged: Disconnected!", mgr.device_name)
                    else:
                        logger.info("[%s] PropertiesChanged: Connected!", mgr.device_name)
                        loop.call_soon_threadsafe(conn_evt.set)
                        # Correct sink routing for other devices that may have been
                        # disrupted by module-rescue-streams when this sink appeared.
                        loop.call_soon_threadsafe(
                            asyncio.ensure_future,
                            _correct_other_devices_routing(mgr),
                        )

                return on_props_changed

            props_handler = _make_props_handler(disconnect_event, connect_event)
            props_iface.on_properties_changed(props_handler)
            logger.info("[%s] D-Bus monitoring active (connected=%s)", mgr.device_name, mgr.connected)

            try:
                await _inner_dbus_monitor(mgr, device_iface, disconnect_event, connect_event, loop)
            finally:
                # Remove our handler before the loop re-subscribes — otherwise
                # each reconnect cycle stacks another PropertiesChanged handler
                # on the bus and every signal fires (and re-registers MPRIS)
                # multiple times.
                try:
                    props_iface.off_properties_changed(props_handler)
                except Exception as exc:
                    logger.debug("[%s] off_properties_changed failed: %s", mgr.device_name, exc)
            # Successful re-subscription cycle — loop immediately.  (The old
            # unconditional 10s sleep here delayed audio setup after every
            # reconnect.)
            continue

        except RuntimeError:
            raise  # propagate to monitor_and_reconnect for polling fallback
        except Exception as e:
            connect_failures += 1
            logger.exception(
                "[%s] D-Bus monitor error (%s/%s)", mgr.device_name, connect_failures, _MAX_CONNECT_FAILURES
            )
            if connect_failures >= _MAX_CONNECT_FAILURES:
                if bus:
                    try:
                        bus.disconnect()
                    except Exception as exc:
                        logger.debug("D-Bus cleanup on failure failed: %s", exc)
                    bus = None
                raise RuntimeError(f"D-Bus monitor failed {connect_failures} consecutive times: {e}") from e
            # Back off before retrying a failed connection only.
            await asyncio.sleep(10)


async def _inner_dbus_monitor(
    mgr: BluetoothManager,
    device_iface,
    disconnect_event,
    connect_event,
    loop,
) -> None:
    """Inner D-Bus monitor loop; returns when D-Bus re-subscription is needed.

    ``connect_event`` is set by the PropertiesChanged handler on the
    outer scope when BlueZ reports the device connected externally
    (e.g. battery-powered speaker waking up). It interrupts the
    failed-reconnect backoff sleep so audio is configured as soon as
    the link is back. Issue #312.
    """
    from sendspin_bridge.bluetooth.manager import _bt_executor

    reconnect_attempt = 0
    while mgr._running:
        if not mgr.management_enabled:
            # ``mgr.connected`` stays fresh here even while released —
            # the PropertiesChanged handler keeps applying state — so an
            # auto-released speaker that reconnects on its own can be
            # reclaimed without polling (#349/#350).
            if await _finish_auto_reclaim(mgr, loop):
                continue
            await asyncio.sleep(5)
            continue

        if mgr.host and mgr.host.get_status_value("bt_standby") and not mgr.host.get_status_value("bt_waking"):
            await _standby_sleep(mgr)
            continue

        if mgr.connected:
            # Standby wake: direct connect already finished — trigger reroute
            if mgr.host and mgr.host.get_status_value("bt_waking"):
                logger.info("[%s] BT already reconnected during wake — triggering reroute", mgr.device_name)
                await mgr.host.start_subprocess()
                continue
            # Clear reconnect state
            if mgr.host and mgr.host.get_status_value("reconnecting"):
                mgr.host.update_status({"reconnecting": False, "reconnect_attempt": 0})
            reconnect_attempt = 0

            # Wait for disconnect signal or heartbeat timeout
            try:
                await asyncio.wait_for(disconnect_event.wait(), timeout=mgr.check_interval * 3)
            except TimeoutError:
                # Heartbeat — verify state directly
                try:
                    current_val = bool(await device_iface.get_connected())
                    if not current_val and mgr.connected:
                        logger.warning("[%s] Heartbeat: missed disconnect signal", mgr.device_name)
                        mgr._apply_connected_state(False)
                        if mgr.host:
                            mgr.host.update_status(
                                {
                                    "bluetooth_connected": False,
                                    "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                                }
                            )
                        disconnect_event.set()
                except Exception as exc:
                    logger.debug("heartbeat connected-state check failed: %s", exc)
                # Read battery level during heartbeat
                mgr.battery_level = await loop.run_in_executor(None, _dbus_get_battery_level, mgr._dbus_device_path)
        else:
            # Device is disconnected — attempt reconnect
            mgr.battery_level = None
            disconnect_event.clear()

            # Phase 2: if in standby the daemon is intentionally parked on a
            # null sink — do NOT kill it or attempt reconnect.
            # Exception: bt_waking means auto-wake requested BT reconnect.
            if mgr.host and mgr.host.get_status_value("bt_standby"):
                if not mgr.host.get_status_value("bt_waking"):
                    await _standby_sleep(mgr)
                    continue
                # bt_waking: reconnect BT but skip daemon kill below
                logger.info("[%s] Standby wake — reconnecting BT (daemon stays alive)", mgr.device_name)

            paired = await loop.run_in_executor(_bt_executor, mgr.is_device_paired)
            mgr.paired = paired
            reconnect_attempt += 1
            if mgr.host:
                mgr.host.update_status(
                    {
                        "reconnecting": True,
                        "reconnect_attempt": reconnect_attempt,
                    }
                )

            # Auto-disable after too many failures.  Offload: may run the
            # adapter-recovery ladder + a config write — never on the loop.
            if await loop.run_in_executor(_bt_executor, mgr._handle_reconnect_failure, reconnect_attempt):
                return

            # Stop sendspin (BT sink is gone — would flood PortAudioErrors)
            # Skip when waking from standby — daemon must stay alive for reroute.
            is_waking = mgr.host and mgr.host.get_status_value("bt_waking")
            if not is_waking and mgr.host and mgr.host.is_subprocess_running():
                logger.info("BT disconnected for %s, stopping sendspin daemon...", mgr.device_name)
                is_grouped = bool(mgr.host.get_status_value("group_id"))
                if not is_grouped:
                    await mgr.host.send_subprocess_command({"cmd": "pause"})
                    await asyncio.sleep(0.2)
                await mgr.host.stop_subprocess()

            _log_reconnect_attempt(mgr.device_name, reconnect_attempt)
            success = await loop.run_in_executor(_bt_executor, mgr.connect_device)
            if mgr._reconnect_cancelled():
                reconnect_attempt = 0
                continue

            if success:
                reconnect_attempt = 0
                mgr._record_reconnect()
                mgr._apply_connected_state(True)
                if mgr.host:
                    mgr.host.update_status(
                        {
                            "reconnecting": False,
                            "reconnect_attempt": 0,
                            "bluetooth_connected": True,
                            "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                        }
                    )
                # Re-subscribe signals — device object may have changed
                logger.info("[%s] Reconnected, restarting D-Bus subscription...", mgr.device_name)
                if mgr.host:
                    logger.info("BT reconnected for %s, starting sendspin...", mgr.device_name)
                    await mgr.host.start_subprocess()
                _spawn_background(_correct_other_devices_routing(mgr))
                return
            else:
                # Failed — back off proportional to failure count.  An
                # external PropertiesChanged: Connected interrupts the
                # sleep so we don't waste the remainder of a saturated
                # backoff window on a speaker that's already back (#312).
                delay = mgr._reconnect_delay(reconnect_attempt)
                logger.debug("[%s] Backoff: next attempt in %.0fs", mgr.device_name, delay)
                try:
                    await asyncio.wait_for(connect_event.wait(), timeout=delay)
                    logger.info(
                        "[%s] External connect detected during backoff — waking early",
                        mgr.device_name,
                    )
                except TimeoutError:
                    pass
                connect_event.clear()
                # Re-read state in case external reconnect happened
                try:
                    mgr._apply_connected_state(bool(await device_iface.get_connected()))
                except Exception as exc:
                    logger.debug("re-read connected state failed: %s", exc)
                if mgr.connected:
                    logger.info("[%s] External reconnect detected, configuring audio...", mgr.device_name)
                    await loop.run_in_executor(_bt_executor, mgr.configure_bluetooth_audio)
                    reconnect_attempt = 0
                    mgr._record_reconnect()
                    if mgr.host:
                        mgr.host.update_status(
                            {
                                "reconnecting": False,
                                "reconnect_attempt": 0,
                                "bluetooth_connected": True,
                                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                            }
                        )
                        logger.info("BT reconnected for %s, starting sendspin...", mgr.device_name)
                        await mgr.host.start_subprocess()
                    _spawn_background(_correct_other_devices_routing(mgr))
                    return
