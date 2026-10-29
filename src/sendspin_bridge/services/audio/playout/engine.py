"""Playout engine: composes the scheduler, the port observer, the loop controller and the stats.

Two ticks drive it, both on the feeder thread: ``next_block`` renders the block at the write
head, ``sense`` (10 Hz) reads the port. The feeder also reports what the sink took every tick
(``delivered``) and, while ``arrival_wanted``, when data reached the sink (``note_arrival``,
from its buffer probe), so silence the port plays between our data is seen (R3). With
``x_port = A − k`` (``PortObserver``):

* ``e   = t − s(x_port)`` — the error heard now (positive = late), fed to the stats;
* ``e_w = t + (x_w − x_port)·(1 + ρ̂) − s(x_w)`` — the error predicted at the write head, fed
  to the controller. Corrections already written are inside ``s(x_w)``, so a jump that is
  rendered but not yet at the port is counted once, never again by the engine.

Placements (start, underrun, stall, clear, ``resync(0)`` and the scheduler's own) align the
next frame to ``audible − o``: ``o`` is the residual the engine reads now (``EMA + β``, 0
before the first reading), so a placement keeps the audio where it is relative to its old
schedule and only the event itself (a gap, a clear) moves it (Q6). A delay change
(``resync(shift ≠ 0)``) shifts the queue and is executed as a frame-exact jump of
``ΔD = −shift`` at the write head, keeping the residual (Ruling S-23). Placements, steady and
delay jumps reset the controller and the stats window. Measure mode passes ``rate = 0.0`` to
the scheduler; only placements and jumps act on the audio.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, TypeVar

from sendspin_bridge.services.audio.playout.air_bias import AirBias, AirFeed, EHistory
from sendspin_bridge.services.audio.playout.controller import JUMP_US, LoopController
from sendspin_bridge.services.audio.playout.scheduler import PlayoutScheduler
from sendspin_bridge.services.audio.playout.stats import PlayoutStats, RateLimitedLog
from sendspin_bridge.services.audio.playout.timeline import PortObserver, ScheduleMap, SegmentKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.audio.playout.scheduler import Block, InputFormat
    from sendspin_bridge.services.audio.playout.sensor import PortClock, PortSample
    from sendspin_bridge.services.audio.playout.timeline import PortReading

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# The AirBias keys of the status ``playout`` dict when this engine has no bias (spec "Status").
_NO_AIR_SNAPSHOT: dict[str, object] = {
    "air_bias_state": None,
    "air_bias_ms": None,
    "air_bias_reason": None,
    "air_target": None,
    "air_target_ms": None,
    "air_d_ref_ms": None,
    "air_trim_ms": None,
    "air_learned_starts": None,
    "air_corrections": 0,
    "air_last_correction_ms": None,
    "air_bias_gated": 0,
    "air_bias_skipped": 0,
    "air_learned": None,
}

# Ruling R5: a forward step of (a - t) larger than this between consecutive readings is a PA step.
PA_STEP_US = 15_000.0
SUMMARY_EVERY_S = 10.0  # DEBUG summary cadence; 1 s with SENDSPIN_PLAYOUT_DEBUG=1
SUMMARY_DEBUG_EVERY_S = 1.0
UNDERRUN_WARN_COUNT = 3  # more than this many underruns within UNDERRUN_WARN_WINDOW_S
UNDERRUN_WARN_WINDOW_S = 600.0
REJECTED_WARN_FRACTION = 0.30
REJECTED_WINDOW = 600  # readings (60 s)
SENSOR_DEAD_READS = 10  # consecutive empty readings (1 s) = sensor unavailable
PREDICT_READINGS = 9  # readings whose median offset dates the port for a placement
# R3: a delivery is checked for an underrun (did the port run past our data before it
# arrived?) only when it arrives at least STARVE_MARGIN_US after the ring was predicted to run
# dry; the feeder reports arrivals from ARRIVAL_ARM_EARLY_US before that. In steady play the
# next delivery always comes before it, so steady play costs no extra clock read and a noisy
# clock cannot fake an underrun.
STARVE_MARGIN_US = 3_000.0
ARRIVAL_ARM_EARLY_US = 6_000.0  # > one feeder tick (5 ms)
ARRIVALS_KEPT = 8
MIN_DEFICIT_NS = 3_000_000  # a smaller deficit is the reading's noise (sigma 4 ms per reading)
ARRIVAL_X_TOLERANCE_NS = 5_000  # an arrival dates only the delivery it starts (pts rounding)
# After an underrun or stall placement (R3, R14) the error restarts from a fresh baseline:
# once the port reaches the placed audio, e_w is taken in 1 s medians; the baseline is the
# last of two consecutive medians within BASELINE_STABLE_US (or the last one after
# BASELINE_TIMEOUT_READINGS). Steady jumps then correct e_w minus the baseline. A port clock
# that settles after the event can move the error by hundreds of ms without moving the
# audio (a likely reading of the 459 ms jump in #556); that offset is reported, not
# corrected, and over BASELINE_WARN_US it is a WARNING and state degraded.
BASELINE_MEDIAN_READINGS = 10  # 1 s
BASELINE_STABLE_US = 5_000.0
BASELINE_TIMEOUT_READINGS = 60  # 6 s
BASELINE_WARN_US = 50_000.0
BASELINE_MIN_US = 2_000.0  # a smaller baseline is taken as 0
_BASELINE_REASONS = ("underrun", "stall")


def _wall_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class PlayoutSettings:
    mode: Literal["measure", "loop"]
    warm_hold_s: float = 300.0
    warm_in_group: bool = True
    warm_group_max_s: float = 3600.0
    warmup_s: float = 60.0
    trim_us: int = 0
    keep_alive_method: str = "silence"


class PlayoutEngine:
    def __init__(
        self,
        *,
        settings: PlayoutSettings,
        port: PortClock,
        k0_ns: int = 0,
        wall_iso: Callable[[], str] = _wall_iso,
        air_feed: AirFeed | None = None,
    ) -> None:
        self._settings = settings
        self._port = port
        self._wall_iso = wall_iso
        self._map = ScheduleMap()
        self._sched = PlayoutScheduler(schedule=self._map)
        self._observer = PortObserver(k0_ns=k0_ns)
        self._controller = LoopController(mode=settings.mode)
        self._stats = PlayoutStats()
        self._warn = RateLimitedLog(logger, clock=lambda: (self._now_us or 0.0) / 1e6)
        debug = os.environ.get("SENDSPIN_PLAYOUT_DEBUG", "") not in ("", "0")
        self._summary_every_s = SUMMARY_DEBUG_EVERY_S if debug else SUMMARY_EVERY_S
        self._summary_next_s: float | None = None
        self._underrun_times: deque[float] = deque()
        self._underruns_seen = 0
        self._recent_rejected: deque[bool] = deque(maxlen=REJECTED_WINDOW)
        self._empty_reads = 0
        self._reading: PortReading | None = None
        self._reading_x_w_ns = 0  # write head at the last reading
        self._delivered_ns = 0  # output stream time handed to the sink, as last reported
        # RAW time the ring is predicted to run dry if nothing more is delivered (R3).
        self._empty_at_us: float | None = None
        self._arrivals: deque[tuple[int, PortSample]] = deque(maxlen=ARRIVALS_KEPT)
        self._fmt: InputFormat | None = None
        self._placements_seen = 0
        self._pending_place: str | None = None  # engine-requested placement not yet carried out
        self._pending_jump_us: float | None = None  # steady or delay jump decided, not yet rendered
        self._pending_jump_reason = "steady"
        self._settle_x_ns: int | None = None  # the port reaches the last event's audio here
        self._prev_a_minus_t: float | None = None
        self._suspended = False
        self._in_underrun = False
        self._degraded = False
        self._error_ema_us: float | None = None
        self._baseline_us = 0.0  # reference of e_w for steady jumps (non-zero after an underrun)
        self._baseline_samples: list[float] | None = None  # collecting a fresh baseline
        self._baseline_medians: list[float] = []  # 1 s medians of the baseline being measured
        self._baseline_reason = ""
        self._baseline_degraded = False  # |baseline| > BASELINE_WARN_US until the next placement
        self._opened_us: float | None = None
        self._opened_x_ns = 0
        # x_port/1000 - t of the recent readings since the last k change; their median dates
        # the port for placements, so one glitched reading does not move one.
        self._offsets: deque[float] = deque(maxlen=PREDICT_READINGS)
        self._warm_from_us: float | None = None
        self._now_us: float | None = None
        self._late_start_us: float | None = None
        self._started = False  # the start placement was requested
        self._fallback_reason: str | None = None
        # The air bias (loop mode with a feed only; feeder-thread only). ``_b_dom`` is the bias value
        # the controller and the published error are in the domain of (None: raw domain).
        self._bias: AirBias | None = None
        self._history: EHistory | None = None
        self._b_dom: float | None = None
        self._air_off_reason: str | None = None
        if settings.mode == "loop" and air_feed is not None:
            self._bias = AirBias(air_feed)
            self._history = EHistory()

    # -- commands -------------------------------------------------------------------------

    def set_format(self, fmt: InputFormat) -> None:
        self._sched.set_format(fmt)
        self._fmt = fmt

    def submit(self, play_time_us: int, pcm: bytes) -> None:
        self._sched.submit(play_time_us, pcm)

    def clear(self) -> int:
        """Drop queued program audio; returns ``x_end``, the write head at the clear (ns)."""
        x_end = self._sched.clear()
        self._request_placement("clear")
        return x_end

    def resync(self, shift_us: int) -> bool:
        """Apply an output-delay change of ``ΔD = −shift_us``; True if audio was queued.

        ``shift_us != 0`` (Ruling S-23): the queued play times move by ``shift_us`` without a
        placement; unless a placement is pending, a jump of ``−shift_us`` (reason ``delay``, not
        clamped) is executed at the write head when program is queued. It replaces a decided
        steady jump that is not yet rendered. The schedule stays continuous, so the residual
        is kept and every later frame is heard exactly ``ΔD`` earlier. ``shift_us == 0``: an
        engine-requested ``delay`` placement.
        """
        if shift_us == 0:
            playing = self._sched.shift(0)
            self._request_placement("delay")
            return playing
        playing = self._sched.shift_queue(shift_us)
        if playing and self._pending_place is None and not self._sched.placement_pending:
            # Two changes before a render add up; a decided steady jump is replaced.
            owed = self._pending_jump_us if self._pending_jump_reason == "delay" else None
            total = (owed or 0.0) - shift_us
            self._pending_jump_us = total or None
            self._pending_jump_reason = "delay" if total else "steady"
        return playing

    def fall_back(self, reason: str) -> None:
        """Report that this engine cannot run here (the daemon switches to the legacy player)."""
        if self._fallback_reason is None:
            logger.info("playout: falling back (%s)", reason)
            self._fallback_reason = reason

    @property
    def port_x_ns(self) -> int | None:
        """``x_port`` at the last port reading (written-audio time, ns); None before one."""
        return None if self._reading is None else self._reading.x_port_ns

    def schedule_at(self, x_ns: int) -> float | None:
        """Scheduled RAW port time (µs) of written-audio time ``x_ns``, or None (read-only)."""
        return self._map.at(x_ns)

    def kind_at(self, x_ns: int) -> SegmentKind | None:
        """Kind of the schedule segment containing ``x_ns``, or None (read-only)."""
        return self._map.kind_at(x_ns)

    @property
    def queued(self) -> bool:
        """Program audio is queued and not yet written."""
        return self._sched.queued

    @property
    def queued_chunks(self) -> int:
        return self._sched.queued_chunks

    # -- the two ticks --------------------------------------------------------------------

    def next_block(self, now_us: float) -> Block:
        self._tick(now_us)
        if self._opened_us is None:
            self._opened_us = self._warm_from_us = now_us
            self._opened_x_ns = self._sched.x_w_ns
        # Q6: a placement keeps the residual o, so it aligns the next frame to audible - o.
        o = self._placement_offset_us()
        target = self._audible(now_us) - o
        x_w = self._sched.x_w_ns
        s_old = self._map.at(x_w)
        size_us = 0.0 if s_old is None else target - s_old  # how far a placement moves the audio
        t_s = now_us / 1e6

        if self._pending_place is not None and self._sched.reanchor(target):
            # Nothing queued: the placement moves the silence schedule to the write head.
            self._record_placement(self._pending_place, size_us, t_s, o)

        jump = self._pending_jump_us
        jump_reason = self._pending_jump_reason
        self._pending_jump_us = None
        self._pending_jump_reason = "steady"
        if jump is not None and (self._sched.placement_pending or not self._sched.queued):
            # A placement is due anyway: with program queued it re-aligns the next frame at
            # this render, so the jump is dropped (passing it would drop audio the placement
            # then re-aligns, and count it twice). With only silence a steady jump re-anchors
            # it; a delay jump has nothing to move (the next chunk is placed with o).
            if not self._sched.queued and jump_reason == "steady":
                if self._sched.reanchor(target):
                    self._record_event(jump, "steady", residual_us=self._baseline_us)
                else:  # an insert is still being written; the next readings decide again
                    logger.debug("playout: steady jump of %.1f ms dropped (insert owed)", jump / 1000.0)
            else:
                logger.debug("playout: %s jump of %.1f ms dropped (placement due)", jump_reason, jump / 1000.0)
            jump = None

        block = self._sched.render(audible_us=target, now_us=now_us, rate=0.0, jump_us=jump)
        # R10: frames that belong to a jump are not loop corrections. Every insert and drop the
        # scheduler reports today is a jump's (rate corrections arrive in Stage 3).
        jump_dropped, jump_inserted = block.dropped, block.inserted
        if jump is not None:
            if jump_reason == "delay":
                # The schedule stays continuous across a delay jump: the residual is kept.
                b0 = self._b_dom or 0.0
                self._controller.placed(t_s, o - self._baseline_us - b0, quiet_wait=True)
                self._record_event(jump, "delay", residual_us=o - b0)
            else:
                # The controller reset itself when it decided it; the jump removed e_w - β, so the
                # raw error it leaves is the baseline.
                self._record_event(jump, "steady", residual_us=self._baseline_us)

        count = self._sched.placement_count
        if count != self._placements_seen:
            self._placements_seen = count
            reason = self._sched.last_placement_reason or "start"
            if reason == "start" and block.late_drop_us:
                self._late_start_us = block.late_drop_us
            if self._reading is not None:  # before the first reading a placement is provisional
                self._record_placement(reason, size_us, t_s, o)
        self._stats.correction(t_s, block.inserted - jump_inserted, block.dropped - jump_dropped)
        return block

    def arrival_wanted(self, now_us: float) -> bool:
        """The feeder should report arrivals now: an underrun is on, or the ring may run dry."""
        if self._reading is None:
            return False
        if self._observer.in_underrun:
            return True
        return self._empty_at_us is not None and now_us >= self._empty_at_us - ARRIVAL_ARM_EARLY_US

    def note_arrival(self, x_ns: int, sample: PortSample | None) -> None:
        """Data from stream time ``x_ns`` reached the sink when the port read ``sample``."""
        if sample is not None:
            self._arrivals.append((x_ns, sample))

    def delivered(self, now_us: float, delivered_ns: int) -> None:
        """The feeder's view of what the sink took (``delivered``), every tick (R3)."""
        self._on_delivered(now_us, delivered_ns, None)

    def _on_delivered(self, now_us: float, delivered_ns: int, sample: PortSample | None) -> None:
        """Check a delivery that ends a starve; ``sample`` is a port reading taken now, if any.

        The data from ``prev`` lands where the port is when it arrives: an arrival report
        dates that exactly, else the first sight of it (this report or the sensor's reading).
        """
        prev = self._delivered_ns
        if delivered_ns <= prev:
            return
        self._delivered_ns = delivered_ns
        arrival = next(
            ((x, s) for x, s in self._arrivals if abs(x - prev) <= ARRIVAL_X_TOLERANCE_NS),
            None,
        )
        self._arrivals.clear()
        t_arrival_us = now_us if arrival is None else arrival[1].t_us
        starved = self._observer.in_underrun or (
            self._empty_at_us is not None and t_arrival_us >= self._empty_at_us + STARVE_MARGIN_US
        )
        if self._reading is not None and starved:
            if arrival is None:
                at = sample if sample is not None else self._port.read()
                arrival = None if at is None else (prev, at)
            if arrival is not None:
                moved_ns = self._observer.on_delivery(arrival[1], arrival[0], min_excess_ns=MIN_DEFICIT_NS)
                if moved_ns:
                    # k moved: older readings no longer date the port, and the last one is
                    # re-dated so the prediction does not extrapolate from the old origin.
                    self._offsets.clear()
                    self._reading = dataclasses.replace(self._reading, x_port_ns=self._reading.x_port_ns - moved_ns)
        x_port_ns = self._predicted_x_port_ns(now_us)
        self._empty_at_us = None if x_port_ns is None else now_us + (delivered_ns - x_port_ns) / 1000.0

    def _predicted_x_port_ns(self, now_us: float) -> float | None:
        """``x_port`` now from the recent readings (their median offset), else the last one."""
        r = self._reading
        if r is None:
            return None
        if self._offsets:
            return (now_us + statistics.median(self._offsets)) * 1000.0
        return r.x_port_ns + (now_us - r.t_us) * 1000.0

    def sense(self, now_us: float, delivered_ns: int) -> None:
        self._tick(now_us)
        rejected_before = self._stats.rejected_samples
        self._sense(now_us, delivered_ns)
        self._recent_rejected.append(self._stats.rejected_samples > rejected_before)
        self._check_warnings(now_us / 1e6)

    def _sense(self, now_us: float, delivered_ns: int) -> None:
        sample = self._port.read()
        if sample is None:
            self._stats.rejected()
            self._empty_reads += 1
            return
        self._empty_reads = 0
        # The sensor's level read may be the first to see the data that ends a starve.
        self._on_delivered(now_us, delivered_ns, sample)
        r = self._observer.observe(sample, delivered_ns)
        self._reading = r
        self._reading_x_w_ns = self._sched.x_w_ns
        t_s = r.t_us / 1e6

        a_minus_t = sample.a_ns / 1000.0 - sample.t_us
        if r.stalled and not self._suspended:
            self._air_break("stall", r.x_port_ns, t_s)
        if r.stalled:
            self._suspended = True
            self._prev_a_minus_t = None
        else:
            self._stats.port(t_s, a_minus_t)
            if self._prev_a_minus_t is not None and not r.recovered and a_minus_t - self._prev_a_minus_t > PA_STEP_US:
                self._stats.pa_step()
                self._air_break("pa_step", r.x_port_ns, t_s)
                self._offsets.clear()  # the port moved: older readings no longer date it
            self._prev_a_minus_t = a_minus_t
        if r.underrun:
            self._stats.underrun()
            self._air_break("underrun", r.x_port_ns, t_s)
            self._in_underrun = True
        if r.stalled or self._in_underrun or r.recovered:
            self._offsets.clear()  # k is moving or the port is not on our data
        if not (r.stalled or self._in_underrun):
            self._offsets.append(r.x_port_ns / 1000.0 - r.t_us)
        if r.stalled or self._in_underrun:
            self._warm_from_us = r.t_us
        if r.recovered:
            # Place once the port runs on our data again, with k final (not at the underrun
            # reading itself: data that resumes later than it would be placed off by the gap).
            reason = "stall" if self._suspended else "underrun"
            self._suspended = self._in_underrun = False
            self._warm_from_us = r.t_us
            self._started = True
            self._request_placement(reason)
            return
        if r.stalled or self._in_underrun:
            return  # controller frozen
        if not self._started:
            # The first usable reading re-places what the open-time guess scheduled.
            self._started = True
            self._request_placement("start")
            return

        self._map.prune_before(r.x_port_ns)
        s_port = self._map.at(r.x_port_ns)
        if s_port is not None and (self._settle_x_ns is None or r.x_port_ns >= self._settle_x_ns):
            self._stats.error(t_s, r.t_us - s_port)
        self._air_step(r, s_port, t_s)

        if self._pending_place is not None or self._pending_jump_us is not None or self._sched.insert_owed:
            return  # an event is on its way into s(x_w); the controller waits for it
        if self._sched.placement_pending and self._sched.queued:
            return
        s_w = self._map.at(self._reading_x_w_ns)
        if s_w is None:
            return
        rho = self._controller.rate_estimate
        e_w = r.t_us + (self._reading_x_w_ns - r.x_port_ns) / 1000.0 * (1.0 + rho) - s_w
        if self._baseline_samples is not None:
            if self._settle_x_ns is not None and r.x_port_ns < self._settle_x_ns:
                return  # the port still plays audio from before the placement
            if not self._measure_baseline(e_w - (self._b_dom or 0.0)):
                return  # controller frozen until the baseline is known
        # c = e_w - b - β (without a bias value: e_rel = e_w - β)
        e_rel = e_w - self._baseline_us - (self._b_dom or 0.0)
        bias = self._bias
        if bias is not None and self._air(lambda: bias.frozen, False):
            return  # the readings broke; the controller waits for the bias to be seeded again
        d = self._controller.update(t_s, e_rel)
        if d.rejected:
            self._stats.rejected()
        if d.degraded:
            self._degraded = True
            self._warn.warning(
                "steady_jumps",
                "playout: error %.1f ms persists but a steady jump is refused (one per 10 s)",
                e_rel / 1000.0,
            )
        if d.jump_us is not None:
            wanted = d.jump_us if d.clamped_from_us is None else d.clamped_from_us
            raw_error = wanted + self._baseline_us
            if self._baseline_us and abs(raw_error) <= JUMP_US[self._settings.mode]:
                # The raw reading came back near its own zero: the offset the baseline held has
                # cleared, so measure it again instead of moving the audio. A raw error still
                # beyond the threshold is a real step and is corrected below.
                logger.info(
                    "playout: error is back at %.1f ms of the reading; re-measuring the %.1f ms baseline",
                    raw_error / 1000.0,
                    self._baseline_us / 1000.0,
                )
                self._baseline_samples = []
                self._baseline_medians = []
                self._baseline_reason = "re-measure"
                self._error_ema_us = None if d.error_us is None else d.error_us + self._baseline_us
                return
            if d.clamped_from_us is not None:
                logger.info(
                    "playout: steady correction of %.1f ms clamped to %.1f ms",
                    d.clamped_from_us / 1000.0,
                    d.jump_us / 1000.0,
                )
            self._pending_jump_us = d.jump_us
            self._pending_jump_reason = "steady"
        self._error_ema_us = None if d.error_us is None else d.error_us + self._baseline_us

    # -- the air bias ---------------------------------------------------------------------

    def _air(self, fn: Callable[[], _T], default: _T) -> _T:
        """The one guard (I8): every ``AirBias``/``EHistory`` call goes through it.

        An exception disables the bias for this output (``internal_error``), re-bases the
        controller to the raw domain and never reaches the feeder.
        """
        try:
            return fn()
        except Exception:
            self._disable_air("internal_error")
            return default

    def _air_break(self, kind: Literal["underrun", "stall", "pa_step"], x_ns: int, t_s: float) -> None:
        bias = self._bias
        if bias is not None:
            self._air(lambda: bias.on_reading_break(kind, float(x_ns), t_s), None)

    def _air_handles_breaks(self) -> bool:
        """``b`` has a value outside ``held``: no baseline is collected (C1)."""
        bias = self._bias
        return bias is not None and self._air(lambda: bias.handles_breaks, False)

    def _air_read(self, bias: AirBias, hist: EHistory, r: PortReading, s_port: float | None, t_s: float) -> tuple:
        """Feed the history, update the bias and read back everything the engine needs."""
        if s_port is not None:
            hist.add(r.t_us, float(r.x_port_ns), r.t_us - s_port, usable=True)
        prev_state = bias.state
        action = bias.update(t_s, hist, beta_us=self._baseline_us)
        return prev_state, bias.state, bias.value_us, bias.seeded_now, bias.resume_beta(), action

    def _air_step(self, r: PortReading, s_port: float | None, t_s: float) -> None:
        """One reading's bias update and the residual-domain transitions (S-24, C1)."""
        bias, hist = self._bias, self._history
        if bias is None or hist is None:
            return
        got = self._air(lambda: self._air_read(bias, hist, r, s_port, t_s), None)
        if got is None:
            return
        prev_state, state, value, seeded_now, resume_beta, _action = got  # actions are executed in B5b
        b_eff = self._b_dom
        if prev_state == "held" and state != "held" and value is not None:
            # Resume (C1): b := b + β, β := 0; c = e_w - b - β is unchanged, the published error drops β.
            self._stop_baseline()
            self._baseline_us = 0.0
            if self._error_ema_us is not None:
                self._error_ema_us -= resume_beta
            b_eff = (b_eff or 0.0) + resume_beta
        if value is None:
            if b_eff is not None:  # the bias dropped its value (calibration forgotten, no learned)
                self._rebase_to_raw(b_eff)
            self._b_dom = None
            return
        if seeded_now:
            # Seed (C1): b₀ folds the current β; β := 0 and the baseline collection stops.
            self._stop_baseline()
            self._baseline_us = 0.0
            if _action is None and self._error_ema_us is not None:
                residual = self._error_ema_us + (b_eff or 0.0) - value
                self._controller.placed(t_s, residual, quiet_wait=True)
                self._error_ema_us = residual
        self._b_dom = value

    def _stop_baseline(self) -> None:
        self._baseline_samples = None
        self._baseline_medians = []
        self._baseline_degraded = False

    def _rebase_to_raw(self, b: float) -> None:
        """Bias → none: ``r = EMA + β + b``, ``β := 0``, controller and EMA restart at ``r`` (audio kept)."""
        self._b_dom = None
        if self._error_ema_us is None:
            self._baseline_us = 0.0
            return
        t_s = (self._now_us or 0.0) / 1e6
        raw = self._error_ema_us + b  # the published value already holds β
        self._baseline_us = 0.0
        self._stop_baseline()
        self._controller.placed(t_s, raw, quiet_wait=True)
        self._error_ema_us = raw

    def _disable_air(self, reason: str) -> None:
        """Stop using the bias for this output; ``_air`` calls it from its ``except`` block."""
        self._air_off_reason = reason
        self._bias = None
        self._history = None
        self._warn.warning(
            "air_bias",
            "playout: the air bias is off for this output (%s); the error is measured against the pulsesink only",
            reason,
            exc_info=True,
        )
        if self._b_dom is not None:
            self._rebase_to_raw(self._b_dom)

    def _measure_baseline(self, e_w: float) -> bool:
        """Add a post-event reading to the baseline; True once the baseline is accepted."""
        samples = self._baseline_samples
        assert samples is not None
        samples.append(e_w)
        if len(samples) % BASELINE_MEDIAN_READINGS:
            return False
        medians = self._baseline_medians
        medians.append(statistics.median(samples[-BASELINE_MEDIAN_READINGS:]))
        stable = len(medians) >= 2 and abs(medians[-1] - medians[-2]) <= BASELINE_STABLE_US
        if not stable and len(samples) < BASELINE_TIMEOUT_READINGS:
            return False
        baseline = medians[-1]
        self._baseline_us = 0.0 if abs(baseline) < BASELINE_MIN_US else baseline
        self._baseline_samples = None
        self._baseline_medians = []
        what = "re-measured" if self._baseline_reason == "re-measure" else f"after the {self._baseline_reason}"
        logger.info(
            "playout: error baseline %s is %.1f ms%s",
            what,
            baseline / 1000.0,
            "" if stable else " (not stable after 6 s)",
        )
        self._baseline_degraded = abs(self._baseline_us) > BASELINE_WARN_US
        if self._baseline_degraded:
            self._warn.warning(
                "baseline",
                "playout: error baseline %s is %.1f ms; reported, not corrected",
                what,
                self._baseline_us / 1000.0,
            )
        return True

    def _check_warnings(self, t_s: float) -> None:
        """Rate-limited WARNINGs (once per 10 min per kind) and the periodic DEBUG summary."""
        if self._empty_reads >= SENSOR_DEAD_READS:
            self._warn.warning("sensor", "playout: the port clock is unavailable; no timing readings")
        while self._underruns_seen < self._stats.underruns:
            self._underruns_seen += 1
            self._underrun_times.append(t_s)
        while self._underrun_times and t_s - self._underrun_times[0] > UNDERRUN_WARN_WINDOW_S:
            self._underrun_times.popleft()
        if len(self._underrun_times) > UNDERRUN_WARN_COUNT:
            self._warn.warning(
                "underruns",
                "playout: %d underruns in the last 10 min; the sink is starved",
                len(self._underrun_times),
            )
        n = len(self._recent_rejected)
        if n >= REJECTED_WINDOW // 4:
            frac = sum(self._recent_rejected) / n
            if frac > REJECTED_WARN_FRACTION:
                self._warn.warning("rejected", "playout: %.0f%% of timing samples were rejected", frac * 100.0)
        if self._summary_next_s is None:
            self._summary_next_s = t_s + self._summary_every_s
        elif t_s >= self._summary_next_s:
            self._summary_next_s = t_s + self._summary_every_s
            if logger.isEnabledFor(logging.DEBUG):
                snap = self.snapshot()
                logger.debug(
                    "playout summary: e=%s ms p95=%s ms r=%s ppm corrections/s=%s slope=%s ms/s jumps=%s underruns=%s",
                    snap["error_ms"],
                    snap["error_p95_ms"],
                    snap["correction_ppm"],
                    snap["corrections_per_s"],
                    snap["error_slope_ms_per_s"],
                    snap["jumps"],
                    snap["underruns"],
                )

    def snapshot(self) -> dict[str, object]:
        """The status ``playout`` dict (spec "Status")."""
        stats = self._stats.snapshot()
        warm_s = None
        if self._warm_from_us is not None and self._now_us is not None:
            warm_s = round(max(0.0, self._now_us - self._warm_from_us) / 1e6, 1)
        host_latency_ms = None
        if self._reading is not None:
            host_latency_ms = round((self._reading_x_w_ns - self._reading.x_port_ns) / 1e6, 3)
        error_ms = stats.pop("error_ms")
        if self._error_ema_us is not None:
            error_ms = round(self._error_ema_us / 1000.0, 3)
        snap: dict[str, object] = {
            "mode": self._settings.mode,
            "state": self._state(),
            "error_ms": error_ms,
            **stats,
            "correction_ppm": round(self._controller.rate_estimate * 1e6, 1),
            "host_latency_ms": host_latency_ms,
            "pa_latency_ms": None,
            "clock_slew_ppm": None,
            "k_ns": self._observer.k_ns,
            "warm": warm_s is not None and warm_s >= self._settings.warmup_s,
            "warm_s": warm_s,
            "warmup_s": self._settings.warmup_s,
            "hold_reason": None,
            "hold_left_s": None,
            "late_start_ms": None if self._late_start_us is None else round(self._late_start_us / 1000.0, 3),
            "trim_us": self._settings.trim_us,
            "fallback_reason": self._fallback_reason,
        }
        snap.update(self._air_snapshot())
        return snap

    def _air_snapshot(self) -> dict[str, object]:
        bias = self._bias
        if bias is not None:
            got = self._air(lambda: bias.snapshot(), None)
            if got is not None:
                return got
        out = dict(_NO_AIR_SNAPSHOT)
        if self._air_off_reason is not None:
            out["air_bias_state"] = "none"
            out["air_bias_reason"] = self._air_off_reason
        return out

    # -- internals ------------------------------------------------------------------------

    def _tick(self, now_us: float) -> None:
        if self._now_us is None or now_us > self._now_us:
            self._now_us = now_us

    def _state(self) -> str:
        if self._fallback_reason is not None:
            return "fallback"
        if self._opened_us is None:
            return "idle"
        if self._suspended:
            return "suspended"
        if self._degraded or self._baseline_degraded:
            return "degraded"
        return "playing"

    def _audible(self, now_us: float) -> float:
        """Predicted RAW time at which the write head leaves the port.

        ``t + (x_w − x_port(t))·(1 + ρ̂)`` at the last reading, with ``x_port(t)`` from the
        median offset of the recent readings. Before the first reading the port is taken to
        start at the open: the ring is empty then and fills at once, so the first frames
        leave right away (a buffer-time guess would put the first ring of audio early by
        that much, and the first reading only re-places what is written after it).
        """
        x_w = self._sched.x_w_ns
        r = self._reading
        if r is None or not self._offsets:
            opened = now_us if self._opened_us is None else self._opened_us
            if r is not None:  # no usable reading (stall/underrun): continue from the last one
                return max(now_us, r.t_us + (x_w - r.x_port_ns) / 1000.0)
            return max(now_us, opened + (x_w - self._opened_x_ns) / 1000.0)
        x_port_us = r.t_us + statistics.median(self._offsets)
        rho = self._controller.rate_estimate
        return r.t_us + (x_w / 1000.0 - x_port_us) * (1.0 + rho)

    def _request_placement(self, reason: str) -> None:
        self._sched.request_placement(reason)
        self._pending_place = reason
        self._pending_jump_us = None  # the placement re-aligns; a decided jump is moot
        self._pending_jump_reason = "steady"

    def _placement_offset_us(self) -> float:
        """``o``: the raw residual a placement keeps, 0 before the first reading.

        ``EMA + β`` without a bias value, ``b + β`` with one (spec "Residual domains").
        """
        if self._b_dom is not None:
            return self._b_dom + self._baseline_us
        return 0.0 if self._error_ema_us is None else self._error_ema_us

    def _record_placement(self, reason: str, size_us: float, t_s: float, o: float) -> None:
        """Record a placement that aligned the next frame to ``audible − o`` (spec "Residual domains")."""
        self._pending_place = None
        # The placement kept the residual o, so the error continues from it. After an underrun or
        # a stall the reading may still settle, so the baseline restarts at 0 and a fresh one is
        # measured (and measured again if another placement lands while it is being measured);
        # any other placement keeps the baseline and its degraded state.
        # With a bias value outside ``held`` the bias handles breaks (C1): no baseline, β stays 0.
        handles = self._air_handles_breaks()
        if reason in _BASELINE_REASONS and not handles:
            self._baseline_us = 0.0
            self._baseline_degraded = False
            self._baseline_reason = reason
        collecting = not handles and (reason in _BASELINE_REASONS or self._baseline_samples is not None)
        self._baseline_samples = [] if collecting else None
        self._baseline_medians = []
        # An error right after it waits quietly: the residual o - β - b is 0 with a bias.
        b0 = self._b_dom or 0.0
        self._controller.placed(t_s, o - self._baseline_us - b0, quiet_wait=True)
        self._record_event(size_us, reason, residual_us=o - b0)

    def _record_event(self, size_us: float, reason: str, residual_us: float = 0.0) -> None:
        """Count a placement or jump; the stats window restarts, the published error restarts at
        ``residual_us`` and in-flight audio settles."""
        logger.info("playout jump: %s, %.1f ms", reason, size_us / 1000.0)
        self._stats.jump(self._wall_iso(), size_us, reason)
        self._stats.reset_window()
        self._error_ema_us = residual_us
        self._degraded = False
        self._settle_x_ns = self._sched.x_w_ns
