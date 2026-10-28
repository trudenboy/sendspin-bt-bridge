"""AirSensorService: the parent side of the air sensor (only with ``EXPERIMENTAL_AIR_SENSOR``).

A hub consumer (``wants_kernel_time``, ``wants_acl_tx``) on the hub thread feeds one
``A2dpAirParser(check_crc="first", decode_frames=False)`` with RAW seconds (``rec.t_raw_us / 1e6``; no other time
conversion exists here) and hands every media packet of a link whose peer is a subscribed speaker
to that speaker's :class:`AirLinkTracker`. A drop increase, a change of the hub's clock epoch and a
hub reopen start a new epoch on every tracker.

On the bridge loop, every ``tick_s`` the trackers' batches move into a bounded per-device queue
(drop-oldest, counted as ``ipc_dropped``) and each device's **own sender task** writes them with the
client's non-blocking ``send_air_command``; a refused write (daemon not advertising ``accepts``, or
its stdin buffer full) leaves the batch queued for the next round, so one stalled daemon never
delays another. An ``air_source`` goes out on change and every 5 s, ahead of the batches.

The service is a hub consumer only while at least one device is subscribed. CPU guard (per
streaming speaker): every ``cpu_window_s`` the hub's ``cpu_fraction`` is compared with
``cpu_per_speaker × max(1, streaming_links)``; two consecutive windows over budget remove the
consumer and every device reports ``unavailable: cpu_budget`` until the next restart.

Subscriptions follow the clients (``SendspinClient`` subscribes its speaker while its daemon
advertises ``accepts``, the speaker is connected, managed and not in standby). ``subscribe`` and
``unsubscribe`` may be called from any thread; sender tasks are only created and cancelled on the
bridge loop. A hub reopen after an error starts a new epoch on every tracker and sends a fresh
``air_source`` to every subscribed device at once.

Monitor records are never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.bluetooth.a2dp_air import AIR_EVENT_CODES, AIR_MONITOR_OPCODES, A2dpAirParser
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirLinkTracker, TrackerState
from sendspin_bridge.services.bluetooth.hci_clock import _raw_ns
from sendspin_bridge.services.ipc.commands import (
    AIR_CODEC_MAX,
    AIR_ENVELOPE_VERSION,
    AirEnvelope,
    AirSource,
    air_envelope_from_batch,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.bluetooth.hci_monitor_hub import HubRecord

logger = logging.getLogger(__name__)

SOURCE_REPEAT_S = 5.0
CPU_WINDOW_S = 30.0
CPU_OVER_WINDOWS = 2
_LOG_INTERVAL_S = 600.0


_current: AirSensorService | None = None


def get_air_sensor_service() -> AirSensorService | None:
    """The running service (switch on and started), else ``None``."""
    return _current


def set_air_sensor_service(service: AirSensorService | None) -> None:
    global _current
    _current = service


def _default_clock() -> float:
    return _raw_ns() / 1000.0


def air_accepts(client: Any) -> bool:
    """True when the client's daemon advertises the air payload version this parent speaks (R-6)."""
    accepts = getattr(client, "air_accepts", None) if client is not None else None
    try:
        return AIR_ENVELOPE_VERSION in (accepts or ())
    except TypeError:
        return False


@dataclass(eq=False)
class _Device:
    mac: str
    tracker: AirLinkTracker
    queue: deque[AirEnvelope]
    link: tuple[int, int] | None = None
    ipc_dropped: int = 0
    source_last: AirSource | None = None
    source_at: float | None = None
    source_pending: AirSource | None = None
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class AirSensorService:
    """Hub consumer (hub thread) + per-device trackers + one sender task per device (bridge loop)."""

    wants_acl_tx = True
    wants_kernel_time = True
    opcodes = AIR_MONITOR_OPCODES  # the hub skips records the parser ignores (R-19)
    event_codes = AIR_EVENT_CODES

    def __init__(
        self,
        *,
        hub: Any,
        loop: asyncio.AbstractEventLoop,
        resolve_client: Callable[[str], Any | None],
        parser: A2dpAirParser | None = None,
        tick_s: float = 0.25,
        queue_len: int = 16,
        cpu_per_speaker: float = 0.04,
        cpu_window_s: float = CPU_WINDOW_S,
        clock: Callable[[], float] = _default_clock,
    ) -> None:
        self._hub = hub
        self._loop = loop
        self._resolve_client = resolve_client
        self._parser = parser if parser is not None else A2dpAirParser(check_crc="first", decode_frames=False)
        self._tick_s = tick_s
        self._queue_len = queue_len
        self._cpu_per_speaker = cpu_per_speaker
        self._cpu_window_us = cpu_window_s * 1e6
        self._clock = clock

        # Guards the parser, the trackers and the device map (hub thread vs bridge loop).
        self._lock = threading.Lock()
        self._devices: dict[str, _Device] = {}
        self._registered = False
        self._registration_lock = threading.Lock()
        self._last_drops: int | None = None
        self._last_clock_epoch: int | None = None
        self._hub_state: str | None = None
        self._hub_reason: str | None = None
        self._resend_sources = False

        self._tripped = False
        self._over = 0
        self._cpu_checked_at: float | None = None
        self._logged_at: dict[str, float] = {}
        self._stopped = False

    # ------------------------------------------------------------------ subscriptions

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def subscribe(self, mac: str) -> None:
        """Any thread. The device's sender task is created by the next ``tick`` on the loop."""
        key = mac.upper()
        with self._lock:
            if key not in self._devices:
                self._devices[key] = _Device(
                    mac=key,
                    # codec_hint is called by the tracker on the hub thread: it only reads the
                    # client's status (no writes, no locks of the client).
                    tracker=AirLinkTracker(codec_hint=lambda: self._codec_hint(key)),
                    queue=deque(maxlen=self._queue_len),
                )
        self._sync_registration()

    def unsubscribe(self, mac: str) -> None:
        """Any thread. The device leaves the map and its sender task is cancelled on the loop."""
        with self._lock:
            dev = self._devices.pop(mac.upper(), None)
            # Read under the lock: tick() creates a task only for a device still in the map.
            task = dev.task if dev is not None else None
            if dev is not None:
                dev.task = None
        if task is not None:
            self._call_on_loop(task.cancel)
        self._sync_registration()

    def is_subscribed(self, mac: str) -> bool:
        with self._lock:
            return mac.upper() in self._devices

    def queued(self, mac: str) -> int:
        dev = self._devices.get(mac.upper())
        return len(dev.queue) if dev is not None else 0

    def tracker_epoch(self, mac: str) -> int | None:
        state = self.tracker_state(mac)
        return state.epoch if state is not None else None

    def tracker_state(self, mac: str) -> TrackerState | None:
        with self._lock:
            dev = self._devices.get(mac.upper())
            return dev.tracker.state(self._clock()) if dev is not None else None

    def _sync_registration(self) -> None:
        # Hub calls happen outside self._lock: a hub may greet a consumer synchronously.
        with self._registration_lock:
            with self._lock:
                want = bool(self._devices) and not self._tripped and not self._stopped
            if want and not self._registered:
                self._hub.add_consumer(self)
                self._registered = True
            elif not want and self._registered:
                self._hub.remove_consumer(self)
                self._registered = False

    # ------------------------------------------------------------------ hub thread

    def on_record(self, rec: HubRecord) -> None:
        with self._lock:
            if not self._devices:
                return
            if rec.drops is not None:
                if self._last_drops is not None and rec.drops > self._last_drops:
                    self._discontinuity()
                self._last_drops = rec.drops
            if self._last_clock_epoch is not None and rec.clock_epoch != self._last_clock_epoch:
                self._discontinuity()
            self._last_clock_epoch = rec.clock_epoch
            packets = self._parser.feed(rec.t_raw_us / 1e6, rec.index, rec.opcode, rec.payload, rec.drops)
            if not packets:
                return
            for p in packets:
                key = (p.index, p.handle)
                mac = self._hub.peer(p.index, p.handle)
                dev = self._devices.get(mac.upper()) if mac else None
                if dev is None:
                    continue
                if dev.link != key:
                    if dev.link is not None:
                        dev.tracker.on_link_reset()
                    dev.link = key
                dev.tracker.on_packet(
                    p, timestamp=rec.timestamp, in_step=self._parser.link_in_step(key), clock_epoch=rec.clock_epoch
                )

    def on_hub_state(self, state: str, reason: str | None) -> None:
        reopened = False
        with self._lock:
            if state == "open":
                reopened = self._hub_state not in (None, "open")
                self._hub_reason = None
                self._discontinuity()  # a reopen restarts every epoch (no-op before any traffic)
                if reopened:
                    self._resend_sources = True
            elif state == "error":
                self._hub_reason = reason or "monitor_error"
            else:
                self._hub_reason = self._hub_reason or reason or "monitor_error"
            self._hub_state = state
        if reopened:
            # A fresh air_source to every subscribed device now, not at the next tick. Always
            # scheduled (never inline): a hub may report "open" from inside add_consumer.
            with contextlib.suppress(RuntimeError):  # loop closed at shutdown
                self._loop.call_soon_threadsafe(self._tick_safely)

    def _discontinuity(self) -> None:
        # Caller holds self._lock.
        for dev in self._devices.values():
            dev.tracker.on_discontinuity()

    # ------------------------------------------------------------------ bridge loop

    async def run(self) -> None:
        """Tick every ``tick_s`` until cancelled; the sender tasks stop with it."""
        try:
            while True:
                try:
                    self.tick()
                except Exception:
                    self._log_once("tick", "AirSensorService: tick failed", exc_info=True)
                await asyncio.sleep(self._tick_s)
        finally:
            await self._cancel_senders()

    async def stop(self) -> None:
        self._stopped = True
        self._sync_registration()
        await self._cancel_senders()

    def _tick_safely(self) -> None:
        try:
            self.tick()
        except Exception:
            self._log_once("tick", "AirSensorService: tick failed", exc_info=True)

    def tick(self) -> None:
        """Move batches into the per-device queues, refresh sources, wake the senders (loop only)."""
        now = self._clock()
        self._cpu_guard(now)
        with self._lock:
            devices = list(self._devices.values())
            work = [(dev, dev.tracker.take_batches(now), dev.tracker.state(now)) for dev in devices]
            hub_state, hub_reason = self._hub_state, self._hub_reason
            resend, self._resend_sources = self._resend_sources, False
        if not work:
            return
        counters = self._safe(self._hub.counters, {})
        peers = self._safe(self._hub.peers, {})
        adapters = self._safe(self._hub.adapters, {})
        for dev, batches, tstate in work:
            with self._lock:
                # Re-checked under the lock: an unsubscribe (any thread) since the snapshot above
                # must not leave an orphan sender behind.
                if dev.task is None and not self._stopped and self._devices.get(dev.mac) is dev:
                    dev.task = self._loop.create_task(self._sender(dev), name=f"air-sender-{dev.mac}")
            if resend:
                dev.source_at = None
            client = self._resolve_client(dev.mac)
            if not air_accepts(client):
                # Nothing for a daemon that has not built its sensor; a fresh source goes out
                # as soon as it advertises.
                dev.queue.clear()
                dev.source_last = dev.source_pending = None
                dev.source_at = None
                continue
            for batch in batches:
                if len(dev.queue) >= self._queue_len:
                    dev.ipc_dropped += 1
                dev.queue.append(air_envelope_from_batch(batch))
            src = self._build_source(dev, tstate, client, hub_state, hub_reason, counters, peers, adapters)
            if src != dev.source_last or dev.source_at is None or now - dev.source_at >= SOURCE_REPEAT_S * 1e6:
                dev.source_pending = dev.source_last = src
                dev.source_at = now
            if dev.queue or dev.source_pending is not None:
                dev.wake.set()

    async def _sender(self, dev: _Device) -> None:
        while True:
            await dev.wake.wait()
            dev.wake.clear()
            client = self._resolve_client(dev.mac)
            if not air_accepts(client):
                continue
            if dev.source_pending is not None:
                if not self._send(client, dev.source_pending):
                    continue
                dev.source_pending = None
            while dev.queue:
                if not self._send(client, dev.queue[0]):
                    break
                dev.queue.popleft()

    def _call_on_loop(self, fn: Callable[[], Any]) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            fn()
            return
        with contextlib.suppress(RuntimeError):  # loop closed at shutdown
            self._loop.call_soon_threadsafe(fn)

    def _send(self, client: Any, cmd: Any) -> bool:
        try:
            return bool(client.send_air_command(cmd))
        except Exception:
            self._log_once("send", "AirSensorService: air command write failed", exc_info=True)
            return False

    async def _cancel_senders(self) -> None:
        with self._lock:
            tasks = [dev.task for dev in self._devices.values() if dev.task is not None]
            for dev in self._devices.values():
                dev.task = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def _cpu_guard(self, now: float) -> None:
        if self._tripped or not self._registered:
            return
        if self._cpu_checked_at is not None and now - self._cpu_checked_at < self._cpu_window_us:
            return
        self._cpu_checked_at = now
        counters = self._safe(self._hub.counters, {})
        cpu = counters.get("cpu_fraction")
        if not isinstance(cpu, (int, float)):
            self._over = 0
            return
        streaming = counters.get("streaming_links")
        n = streaming if isinstance(streaming, int) else 0
        budget = self._cpu_per_speaker * max(1, n)
        self._over = self._over + 1 if cpu > budget else 0
        if self._over >= CPU_OVER_WINDOWS:
            self._tripped = True
            self._sync_registration()
            logger.warning(
                "Air sensor: monitor thread CPU %.3f above the budget %.3f for %d windows — air sensor off "
                "until the next restart (cpu_budget)",
                cpu,
                budget,
                CPU_OVER_WINDOWS,
            )

    def _build_source(
        self,
        dev: _Device,
        tstate: TrackerState,
        client: Any,
        hub_state: str | None,
        hub_reason: str | None,
        counters: dict[str, Any],
        peers: dict[tuple[int, int], str],
        adapters: dict[int, str],
    ) -> AirSource:
        reason: str | None
        if self._tripped:
            state, reason = "unavailable", "cpu_budget"
        else:
            unavailable = hub_reason
            if unavailable is None and hub_state != "open":
                unavailable = getattr(self._hub, "unavailable_reason", None)
            if unavailable is not None:
                state, reason = "unavailable", str(unavailable)
            else:
                state, reason = tstate.state, tstate.reason
        link = dev.link
        if link is None or peers.get(link, "").upper() != dev.mac:
            link = next((k for k, m in peers.items() if m.upper() == dev.mac), None)
        drops = counters.get("drops")
        return AirSource(
            state=state,
            reason=reason,
            codec=tstate.codec or self._codec_of(client),
            adapter=adapters.get(link[0]) if link is not None else None,
            index=link[0] if link is not None else None,
            handle=link[1] if link is not None else None,
            in_step=tstate.in_step,
            monitor_drops=drops if isinstance(drops, int) and drops >= 0 else None,
            ipc_dropped=dev.ipc_dropped,
        )

    # ------------------------------------------------------------------ helpers

    def _codec_hint(self, mac: str) -> str | None:
        return self._codec_of(self._resolve_client(mac))

    @staticmethod
    def _codec_of(client: Any) -> str | None:
        status = getattr(client, "status", None) if client is not None else None
        name = status.get("bt_codec_name") if status is not None else None
        if not isinstance(name, str) or not name or not name.isprintable():
            return None
        return name.lower()[:AIR_CODEC_MAX]

    @staticmethod
    def _safe(fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:
            return default

    def _log_once(self, kind: str, msg: str, *, exc_info: bool = False) -> None:
        now = self._clock() / 1e6
        last = self._logged_at.get(kind)
        if last is not None and now - last < _LOG_INTERVAL_S:
            return
        self._logged_at[kind] = now
        logger.warning(msg, exc_info=exc_info)
