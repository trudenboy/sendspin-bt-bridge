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

The hub hands the consumer only the records its parser reacts to (``opcodes`` / ``event_codes``,
plus any record that changes the drop count). The parser's time-based release of held completions
(``HELD_RELEASE_S`` after a drain on a link that stops sending) therefore happens at the next such
record rather than the next record of any kind; it is not driven from the hub's 250 ms tick, whose
RAW "now" can run ahead of records still waiting for a clock pair and would release them early.

Calibration (the sync wizard): :meth:`AirSensorService.arm_fold` attaches an :class:`AirClickFold`
to a speaker for its solo; on the hub thread every parsed SBC frame of that speaker's link is added
with its cell time from the tracker's send anchor (``count·frame_us + β``) and its chirp-band energy
(the phase tool's ``frame_energy``), nothing else. :meth:`AirSensorService.finish_fold` detaches it
under the lock and folds on the caller's thread (never the hub thread; the wizard runs it off the
bridge loop). The phase is on the RAW axis (epoch 0, chirp start, the SBC encoder delay removed);
the fold must hold SBC frames (``not_sbc``) of one epoch (``air_epoch_changed``) with the click
pattern (``no_air_clicks``).

Monitor records are never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.bluetooth.a2dp_air import AIR_EVENT_CODES, AIR_MONITOR_OPCODES, A2dpAirParser
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirLinkTracker, TrackerState
from sendspin_bridge.services.bluetooth.a2dp_air_fold import (
    click_fold_found,
    click_fold_quality,
    click_phase_ms,
    frame_energy,
    sbc_encoder_delay_samples,
)
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

    from sendspin_bridge.services.bluetooth.a2dp_air import MediaPacket, SbcFrame
    from sendspin_bridge.services.bluetooth.hci_monitor_hub import HubRecord

logger = logging.getLogger(__name__)

SOURCE_REPEAT_S = 5.0
CPU_WINDOW_S = 30.0
CPU_OVER_WINDOWS = 2
_LOG_INTERVAL_S = 600.0


_current: AirSensorService | UnavailableAirSensorService | None = None


def get_air_sensor_service() -> AirSensorService | UnavailableAirSensorService | None:
    """The running service (switch on and started; the stand-in if it failed to start), else ``None``."""
    return _current


def set_air_sensor_service(service: AirSensorService | UnavailableAirSensorService | None) -> None:
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


FOLD_MAX_S = 30.0


@dataclass(frozen=True, slots=True)
class AirFoldResult:
    """The air side of a calibration fold."""

    phase_ms: float | None  # RAW axis, epoch 0, PCM instant (d_enc removed); None unless found
    found: bool
    epochs: int  # epochs seen inside the span (must be 1 for a calibration)
    codec: str | None
    rate: int | None
    adapter: str | None
    reason: str | None = None  # not_sbc | air_epoch_changed | no_air_clicks | not_armed; None when found
    frames: int = 0  # SBC frames folded
    clicks: int = 0  # click instants (at the found phase) inside the span
    quality: float | None = None  # pattern score peak / median (found needs ≥ 3)


class AirClickFold:
    """One speaker's SBC frames over a calibration span, folded over the click cycle on demand.

    ``add_frame`` (hub thread, under the service's lock) only records ``(t_cell, energy, epoch)``;
    ``result`` does the fold (on the caller's thread). At most ``max_s`` of frames are kept.
    """

    def __init__(self, *, from_us: float, max_s: float = FOLD_MAX_S) -> None:
        self._from_us = from_us
        self._max_us = max_s * 1e6
        self._rows: deque[tuple[float, float, int]] = deque()  # (t_cell_us, energy, epoch)
        self._formats: dict[int, tuple[int, int, int]] = {}  # epoch -> (rate, sub-bands, samples per frame)

    def add_frame(self, t_cell_us: float, frame: SbcFrame, epoch: int) -> None:
        if t_cell_us < self._from_us:
            return
        rows = self._rows
        rows.append((t_cell_us, frame_energy(frame), epoch))
        self._formats.setdefault(epoch, (frame.sampling_rate, frame.subbands, frame.samples))
        while rows and rows[-1][0] - rows[0][0] > self._max_us:
            rows.popleft()

    def held_s(self) -> float:
        rows = self._rows
        return (rows[-1][0] - rows[0][0]) / 1e6 if rows else 0.0

    def result(self, to_us: float) -> AirFoldResult:
        rows = [r for r in self._rows if r[0] <= to_us]
        if not rows:
            return AirFoldResult(None, False, 0, None, None, None, reason="not_sbc")
        epochs = len({r[2] for r in rows})
        rate, subbands, samples = self._formats[rows[-1][2]]

        def out(
            phase: float | None, reason: str | None, clicks: int = 0, quality: float | None = None
        ) -> AirFoldResult:
            return AirFoldResult(
                phase, phase is not None, epochs, "sbc", rate, None, reason, len(rows), clicks, quality
            )

        if epochs > 1:
            return out(None, "air_epoch_changed")
        frame_s = samples / rate
        pts = [(t / 1e6, e) for t, e, _ in rows]
        quality = click_fold_quality(pts, frame_s)
        d_enc = sbc_encoder_delay_samples(subbands) / rate
        phase = click_phase_ms(pts, frame_s, 0.0, d_enc) if click_fold_found(pts, frame_s, 0.0, d_enc) else None
        if phase is None:
            return out(None, "no_air_clicks", quality=quality)
        return out(phase, None, _clicks_inside(phase, pts[0][0], pts[-1][0] + frame_s), quality)


def _clicks_inside(phase_ms: float, lo_s: float, hi_s: float) -> int:
    """Click instants ``phase + LEAD_IN + k·PERIOD`` (the cycle's every click) between two times."""
    from sendspin_sync import click_track as ct

    first = phase_ms / 1000.0 + ct.LEAD_IN_S
    k0 = math.ceil((lo_s - first) / ct.PERIOD_S)
    k1 = math.floor((hi_s - first) / ct.PERIOD_S)
    return max(0, k1 - k0 + 1)


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
        # Calibration folds (guarded by self._lock): id -> (mac, fold), and each mac's armed id.
        self._folds: dict[str, tuple[str, AirClickFold]] = {}
        self._fold_of: dict[str, str] = {}

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

    def status(self) -> dict[str, str | None]:
        """For diagnostics: ``running``, or ``tripped`` (``cpu_budget``) until the next restart."""
        if self._tripped:
            return {"state": "tripped", "reason": "cpu_budget"}
        return {"state": "running", "reason": None}

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
                    self._log_once(
                        "drops",
                        f"Air sensor: the HCI monitor dropped {rec.drops - self._last_drops} record(s) "
                        f"({rec.drops} in total); air epochs restart (one warning per 10 min)",
                    )
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
                fold_id = self._fold_of.get(dev.mac)
                if fold_id is not None:
                    self._feed_fold(self._folds[fold_id][1], dev.tracker, p)

    @staticmethod
    def _feed_fold(fold: AirClickFold, tracker: AirLinkTracker, p: MediaPacket) -> None:
        """Hub thread, under the lock: the packet's parsed frames at their cell times (no folding)."""
        cells = tracker.last_cells
        beta = tracker.anchor_us()
        if cells is None or beta is None:
            return
        epoch, seq0, frame_us = cells
        try:
            frames = p.frames or (p.sbc.decode() if p.sbc is not None else ())
        except Exception:
            return
        for k, frame in enumerate(frames):
            fold.add_frame((seq0 + k) * frame_us + beta, frame, epoch)

    # ------------------------------------------------------------------ calibration folds

    def arm_fold(self, mac: str, from_us: float) -> str:
        """Any thread: start collecting *mac*'s air frames from RAW ``from_us``; returns the fold id.
        Arming the speaker again replaces its previous fold."""
        key = mac.upper()
        fold_id = uuid.uuid4().hex
        with self._lock:
            old = self._fold_of.pop(key, None)
            if old is not None:
                self._folds.pop(old, None)
            self._folds[fold_id] = (key, AirClickFold(from_us=from_us))
            self._fold_of[key] = fold_id
        return fold_id

    def finish_fold(self, fold_id: str, to_us: float) -> AirFoldResult:
        """Detach the fold and fold its frames up to RAW ``to_us`` on the caller's thread (call it
        off the bridge loop; never from the hub thread)."""
        with self._lock:
            entry = self._folds.pop(fold_id, None)
            if entry is not None and self._fold_of.get(entry[0]) == fold_id:
                del self._fold_of[entry[0]]
            dev = self._devices.get(entry[0]) if entry is not None else None
            link = dev.link if dev is not None else None
        if entry is None:
            return AirFoldResult(None, False, 0, None, None, None, reason="not_armed")
        adapters = self._safe(self._hub.adapters, {})
        res = entry[1].result(to_us)
        return replace(res, adapter=adapters.get(link[0]) if link is not None else None)

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
        _call_on_loop(self._loop, fn)

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


class UnavailableAirSensorService:
    """Stands in for the service when it could not start (switch on, start failed).

    Subscribes like the service, so the clients' subscription logic is unchanged, and tells every
    subscribed daemon ``air_source unavailable`` (the hub's own reason when it has one, else
    *reason*) at once and every ``repeat_s``. Without it an effective speaker would report its
    own ``no_signal`` and hide that the bridge never read the air.
    """

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        resolve_client: Callable[[str], Any | None],
        reason: str,
        hub: Any = None,
        repeat_s: float = SOURCE_REPEAT_S,
    ) -> None:
        self._loop = loop
        self._resolve_client = resolve_client
        self._reason = reason
        self._hub = hub
        self._repeat_s = repeat_s
        self._lock = threading.Lock()
        self._macs: set[str] = set()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    @property
    def reason(self) -> str:
        hub_reason = getattr(self._hub, "unavailable_reason", None) if self._hub is not None else None
        return str(hub_reason) if hub_reason else self._reason

    def status(self) -> dict[str, str | None]:
        return {"state": "failed", "reason": self.reason}

    def subscribe(self, mac: str) -> None:
        key = mac.upper()
        with self._lock:
            self._macs.add(key)
        _call_on_loop(self._loop, lambda: self._send(key))

    def unsubscribe(self, mac: str) -> None:
        with self._lock:
            self._macs.discard(mac.upper())

    def is_subscribed(self, mac: str) -> bool:
        with self._lock:
            return mac.upper() in self._macs

    def tick(self) -> None:
        """Loop only: the source to every subscribed daemon (a refused write is retried next time)."""
        with self._lock:
            macs = sorted(self._macs)
        for mac in macs:
            self._send(mac)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._repeat_s)
            self.tick()

    async def stop(self) -> None:
        with self._lock:
            self._macs.clear()

    def _send(self, mac: str) -> None:
        if not self.is_subscribed(mac):
            return
        client = self._resolve_client(mac)
        if client is None or not air_accepts(client):
            return
        src = AirSource(
            state="unavailable",
            reason=self.reason,
            codec=None,
            adapter=None,
            index=None,
            handle=None,
            in_step=False,
            monitor_drops=None,
            ipc_dropped=0,
        )
        with contextlib.suppress(Exception):
            client.send_air_command(src)


def _call_on_loop(loop: asyncio.AbstractEventLoop, fn: Callable[[], Any]) -> None:
    """Run *fn* now when called on *loop*, else schedule it there (a closed loop is ignored)."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        fn()
        return
    with contextlib.suppress(RuntimeError):  # loop closed at shutdown
        loop.call_soon_threadsafe(fn)
