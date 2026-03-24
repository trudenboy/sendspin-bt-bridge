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

from bt_dbus import _dbus_get_battery_level
from services.internal_events import DeviceEventType

if TYPE_CHECKING:
    from bluetooth_manager import BluetoothManager

UTC = timezone.utc

logger = logging.getLogger(__name__)


async def monitor_and_reconnect(mgr: BluetoothManager) -> None:
    """Continuously monitor BT connection and reconnect if needed.

    Tries D-Bus PropertiesChanged signals (dbus-fast) for instant disconnect
    detection; falls back to bluetoothctl polling if dbus-fast is unavailable
    or if the D-Bus environment doesn't support signal subscriptions.
    """
    logger.info("[%s] monitor_and_reconnect task started", mgr.device_name)
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        await _monitor_dbus(mgr, MessageBus, BusType)
    except (ImportError, RuntimeError) as e:
        logger.info("[%s] D-Bus monitor unavailable (%s) — using bluetoothctl polling", mgr.device_name, e)
        await _monitor_polling(mgr)


async def _monitor_polling(mgr: BluetoothManager) -> None:
    """Legacy bluetoothctl polling-based monitor (fallback)."""
    from bluetooth_manager import _bt_executor

    loop = asyncio.get_running_loop()
    iteration = 0
    reconnect_attempt = 0
    while mgr._running:
        iteration += 1
        try:
            if not mgr.management_enabled:
                await asyncio.sleep(5)
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

                    if mgr._handle_reconnect_failure(reconnect_attempt):
                        reconnect_attempt = 0
                        continue

                    if mgr.host and mgr.host.is_subprocess_running():
                        logger.info("BT disconnected for %s, stopping sendspin daemon...", mgr.device_name)
                        is_grouped = bool(mgr.host.get_status_value("group_id"))
                        if not is_grouped:
                            await mgr.host.send_subprocess_command({"cmd": "pause"})
                            await asyncio.sleep(0.2)
                        await mgr.host.stop_subprocess()

                    logger.warning(
                        "Bluetooth device %s disconnected, reconnecting... (attempt %s)",
                        mgr.device_name,
                        reconnect_attempt,
                    )
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

                    # Read battery level (None if device doesn't support it)
                    mgr.battery_level = _dbus_get_battery_level(mgr._dbus_device_path)

            await asyncio.sleep(5)
        except Exception as e:
            logger.error("Error in Bluetooth poll monitor: %s", e)
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

            # Read initial connected state
            try:
                mgr.connected = bool(await device_iface.get_connected())
            except Exception as exc:
                logger.debug("get_connected() failed: %s", exc)
                mgr.connected = False
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

            def _make_props_handler(evt):
                def on_props_changed(iface_name, changed, _invalidated):
                    if iface_name != "org.bluez.Device1" or "Connected" not in changed:
                        return
                    new_connected = bool(changed["Connected"].value)
                    if new_connected == mgr.connected:
                        return
                    mgr.connected = new_connected
                    ts = datetime.now(tz=UTC).isoformat()
                    if mgr.host:
                        mgr.host.update_status(
                            {
                                "bluetooth_connected": new_connected,
                                "bluetooth_connected_at": ts,
                            }
                        )
                    if not new_connected:
                        loop.call_soon_threadsafe(evt.set)
                        logger.warning("[%s] PropertiesChanged: Disconnected!", mgr.device_name)
                    else:
                        logger.info("[%s] PropertiesChanged: Connected!", mgr.device_name)

                return on_props_changed

            props_iface.on_properties_changed(_make_props_handler(disconnect_event))
            logger.info("[%s] D-Bus monitoring active (connected=%s)", mgr.device_name, mgr.connected)

            await _inner_dbus_monitor(mgr, device_iface, disconnect_event, loop)

        except RuntimeError:
            raise  # propagate to monitor_and_reconnect for polling fallback
        except Exception as e:
            connect_failures += 1
            logger.error(
                "[%s] D-Bus monitor error (%s/%s): %s", mgr.device_name, connect_failures, _MAX_CONNECT_FAILURES, e
            )
            if connect_failures >= _MAX_CONNECT_FAILURES:
                if bus:
                    try:
                        bus.disconnect()
                    except Exception as exc:
                        logger.debug("D-Bus cleanup on failure failed: %s", exc)
                    bus = None
                raise RuntimeError(f"D-Bus monitor failed {connect_failures} consecutive times: {e}")
        await asyncio.sleep(10)


async def _inner_dbus_monitor(mgr: BluetoothManager, device_iface, disconnect_event, loop) -> None:
    """Inner D-Bus monitor loop; returns when D-Bus re-subscription is needed."""
    from bluetooth_manager import _bt_executor

    reconnect_attempt = 0
    while mgr._running:
        if not mgr.management_enabled:
            await asyncio.sleep(5)
            continue

        if mgr.connected:
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
                        mgr.connected = False
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

            # Auto-disable after too many failures
            if mgr._handle_reconnect_failure(reconnect_attempt):
                return

            # Stop sendspin (BT sink is gone — would flood PortAudioErrors)
            if mgr.host and mgr.host.is_subprocess_running():
                logger.info("BT disconnected for %s, stopping sendspin daemon...", mgr.device_name)
                is_grouped = bool(mgr.host.get_status_value("group_id"))
                if not is_grouped:
                    await mgr.host.send_subprocess_command({"cmd": "pause"})
                    await asyncio.sleep(0.2)
                await mgr.host.stop_subprocess()

            logger.warning("[%s] Disconnected, reconnecting... (attempt %s)", mgr.device_name, reconnect_attempt)
            success = await loop.run_in_executor(_bt_executor, mgr.connect_device)
            if mgr._reconnect_cancelled():
                reconnect_attempt = 0
                continue

            if success:
                reconnect_attempt = 0
                mgr._record_reconnect()
                mgr.connected = True
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
                return
            else:
                # Failed — back off proportional to failure count
                delay = mgr._reconnect_delay(reconnect_attempt)
                logger.debug("[%s] Backoff: next attempt in %.0fs", mgr.device_name, delay)
                await asyncio.sleep(delay)
                # Re-read state in case external reconnect happened
                try:
                    mgr.connected = bool(await device_iface.get_connected())
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
                    return
