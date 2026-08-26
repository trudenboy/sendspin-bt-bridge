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

from sendspin_bridge.bluetooth.adapter_session import bt_executor
from sendspin_bridge.services.ipc.commands import Pause

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
    evt = mgr.standby_wake_event
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
    await loop.run_in_executor(bt_executor(), mgr.configure_bluetooth_audio)
    mgr.policy.record_reconnect()
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
    connected = await loop.run_in_executor(bt_executor(), mgr.is_device_connected)
    if not connected:
        return False
    return await _finish_auto_reclaim(mgr, loop, connected=True)


async def monitor_and_reconnect(mgr: BluetoothManager) -> None:
    """Watch this speaker's link and bring it back when it drops.

    Connection state arrives as `PropertiesChanged` signals from the speaker's
    device module, which owns the bus and re-establishes it when bluetoothd
    restarts. There is no polling path any more: an unresolvable device object
    is now an answer ("BlueZ has no such speaker on this controller"), not a
    transport failure, and a bus that will not come back is reported as one
    rather than worked around by asking bluetoothctl the same question every
    five seconds.
    """
    logger.info("[%s] monitor_and_reconnect task started", mgr.device_name)
    # Create an asyncio.Event in the running loop for standby-wake signaling.
    mgr.attach_standby_wake_event(asyncio.Event())
    await _monitor_dbus(mgr)


async def _monitor_dbus(mgr: BluetoothManager) -> None:
    """Watch the speaker's link through its device module.

    The module owns the bus, the subscription and the reconnect; this owns
    what to do about what it reports. A bus that cannot be reached is said
    out loud on the device card after three attempts rather than silently
    retried for ever — there is no polling path behind it any more.
    """
    loop = asyncio.get_running_loop()
    device = mgr.device
    logger.info("[%s] D-Bus monitor started (controller=%s)", mgr.device_name, device.controller)
    unavailable_reported = False

    while mgr.running:
        try:
            state = await device.state()
            if state.object_path is None and not device.transport_available:
                if not unavailable_reported and mgr.host:
                    unavailable_reported = True
                    mgr.host.update_status(
                        {
                            "bluetooth_available": False,
                            "last_error": "bluetooth_transport_unavailable",
                            "last_error_at": datetime.now(tz=UTC).isoformat(),
                        }
                    )
                    logger.warning(
                        "[%s] Cannot reach the system bus — the speaker's state is unknown until it returns",
                        mgr.device_name,
                    )
                await asyncio.sleep(5)
                continue

            if unavailable_reported and mgr.host:
                unavailable_reported = False
                mgr.host.update_status({"bluetooth_available": True})

            # ``apply_connected_state`` routes the assignment through the
            # on_connected / on_disconnected fire, so MPRIS registration lands
            # on the monitor's own startup as well as on later signals.
            mgr.apply_connected_state(state.connected)
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
            # Connected signal arriving during the failed-reconnect backoff
            # wakes the loop immediately (#312 — battery-powered speakers that
            # auto-reconnect would otherwise wait out the remaining backoff).
            connect_event = asyncio.Event()

            def _on_property(
                name: str,
                value: object,
                *,
                # Bound now: the loop builds a fresh pair of events every cycle,
                # and a handler that closed over the names would signal the
                # cycle that is running rather than the one it belongs to.
                disconnect_event: asyncio.Event = disconnect_event,
                connect_event: asyncio.Event = connect_event,
            ) -> None:
                if name != "Connected":
                    return
                new_connected = bool(value)
                if new_connected == mgr.connected:
                    return
                mgr.apply_connected_state(new_connected)
                if mgr.host:
                    mgr.host.update_status(
                        {
                            "bluetooth_connected": new_connected,
                            "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                        }
                    )
                if not new_connected:
                    logger.warning("[%s] PropertiesChanged: Disconnected!", mgr.device_name)
                    loop.call_soon_threadsafe(disconnect_event.set)
                    return
                logger.info("[%s] PropertiesChanged: Connected!", mgr.device_name)
                loop.call_soon_threadsafe(connect_event.set)
                # Correct sink routing for other devices that module-rescue-streams
                # may have disrupted when this sink appeared.
                loop.call_soon_threadsafe(asyncio.ensure_future, _correct_other_devices_routing(mgr))

            device.watch(_on_property)
            logger.info("[%s] D-Bus monitoring active (connected=%s)", mgr.device_name, mgr.connected)
            await _inner_dbus_monitor(mgr, device, disconnect_event, connect_event, loop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[%s] D-Bus monitor cycle failed: %s", mgr.device_name, exc)
            await asyncio.sleep(5)

    await device.close()


async def _inner_dbus_monitor(
    mgr: BluetoothManager,
    device,
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
    reconnect_attempt = 0
    while mgr.running:
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
                    current_val = await device.is_connected()
                    if not current_val and mgr.connected:
                        logger.warning("[%s] Heartbeat: missed disconnect signal", mgr.device_name)
                        mgr.apply_connected_state(False)
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
                mgr.battery_level = await mgr.device.battery_level()
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

            lease = mgr.adapter_handle.try_lease(f"reconnect {mgr.device_name}")
            if lease is None:
                # A UI scan/pair/reconnect holds the adapter — defer instead
                # of contending (same rationale as the polling path). The
                # defer is not a device failure and must not feed the
                # auto-disable counter. Sleep first — this branch is the
                # loop bottom, so without a cadence the defer would spin hot.
                logger.info(
                    "[%s] BT operation in progress — deferring reconnect poll",
                    mgr.device_name,
                )
                await asyncio.sleep(min(mgr.check_interval, 5))
                continue
            try:
                paired = await loop.run_in_executor(bt_executor(), mgr.is_device_paired)
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
                if await loop.run_in_executor(bt_executor(), mgr.handle_reconnect_failure, reconnect_attempt):
                    return

                # Stop sendspin (BT sink is gone — would flood PortAudioErrors)
                # Skip when waking from standby — daemon must stay alive for reroute.
                is_waking = mgr.host and mgr.host.get_status_value("bt_waking")
                if not is_waking and mgr.host and mgr.host.is_subprocess_running():
                    logger.info("BT disconnected for %s, stopping sendspin daemon...", mgr.device_name)
                    is_grouped = bool(mgr.host.get_status_value("group_id"))
                    if not is_grouped:
                        await mgr.host.send_subprocess_command(Pause())
                        await asyncio.sleep(0.2)
                    await mgr.host.stop_subprocess()

                _log_reconnect_attempt(mgr.device_name, reconnect_attempt)
                success = await loop.run_in_executor(bt_executor(), mgr.connect_device)
            finally:
                lease.release()
            if mgr.reconnect_cancelled():
                reconnect_attempt = 0
                continue

            if success:
                reconnect_attempt = 0
                mgr.policy.record_reconnect()
                mgr.apply_connected_state(True)
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
                delay = mgr.policy.delay_for(reconnect_attempt)
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
                    mgr.apply_connected_state(await device.is_connected())
                except Exception as exc:
                    logger.debug("re-read connected state failed: %s", exc)
                if mgr.connected:
                    logger.info("[%s] External reconnect detected, configuring audio...", mgr.device_name)
                    await loop.run_in_executor(bt_executor(), mgr.configure_bluetooth_audio)
                    reconnect_attempt = 0
                    mgr.policy.record_reconnect()
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
