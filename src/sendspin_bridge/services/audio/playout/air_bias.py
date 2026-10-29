"""Hand-off and reading history for the air-sensor bias.

Thread contract (S-1): :class:`AirFeed` is the only object shared between the
daemon loop (producer: pushes windows, lock state, target, key, output delay)
and the feeder thread (consumer: ``take``). It has one ``threading.Lock`` and
every critical section is O(1)-ish and never calls out. :class:`EHistory` and
everything built on it are feeder-only and need no lock.

Usable/coverage rule (S-2): a pulsesink reading is *usable* when no stall or
underrun is in progress and the stream-time mapping ``s(x_port)`` is known.
A window's median is only trusted when the usable readings in its ``x`` span
reach ``E_MIN_COVERAGE`` of the nominal ``SENSE_HZ`` rate.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Literal

logger = logging.getLogger(__name__)

FEED_MAX = 32
E_HISTORY_S = 30.0
E_MIN_COVERAGE = 0.5
SENSE_HZ = 10.0
LEARN_MIN_SAMPLES = 2

SEED_WINDOWS = 3
SEED_AGREE_US = 2_000.0
SEED_MAX_WINDOWS = 12
SEED_FALLBACK_WINDOWS = 5
DEAD_BAND_US = 2_000.0
MIN_CONFIDENCE = 0.5
BIAS_TAU_S = 30.0
DT_MAX_S = 5.0
GATE_MIN_US = 10_000.0
GATE_MAD_K = 4.0
GATE_HISTORY = 20
STEP_WINDOWS = 15
STEP_AGREE_US = 3_000.0
STEP_MIN_AGREE = 12  # of the STEP_WINDOWS gated windows must lie within STEP_AGREE_US of their median
RESEED_TIMEOUT_S = 15.0

BiasState = Literal["none", "seeding", "active", "held"]
ReadingBreak = Literal["underrun", "stall", "pa_step"]
_LOCK_OK = ("locked", "holding")


@dataclass(frozen=True, slots=True)
class AirSample:
    tap_gen: int
    x_lo_ns: float
    x_hi_ns: float
    at_us: float  # air time of the window middle
    h_us: float
    d_out_us: float
    confidence: float


@dataclass(frozen=True, slots=True)
class AirTarget:
    latency_ms: float | None  # the chosen source's L_eff
    d_ref_ms: float | None  # its D_ref (None: latch D_out at the first update)
    source: Literal["calibrated", "learned"] | None
    learned_ms: float | None  # kept so a forgotten calibration can fall back
    learned_d_ref_ms: float | None
    learned_starts: int | None
    version: int  # bumped by every set_target


_NO_TARGET = AirTarget(None, None, None, None, None, None, 0)


@dataclass(frozen=True, slots=True)
class FeedView:
    samples: tuple[AirSample, ...]
    lock: tuple[str, float | None]
    target: AirTarget
    key: tuple[str | None, int | None, str | None]
    d_out_ms: float | None


class AirFeed:
    """The only object shared by the daemon loop (producer) and the feeder thread (consumer)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: deque[AirSample] = deque(maxlen=FEED_MAX)
        self._min_gen = 0
        self._lock_state: tuple[str, float | None] = ("no_signal", None)
        self._target = _NO_TARGET
        self._key: tuple[str | None, int | None, str | None] = (None, None, None)
        self._d_out_ms: float | None = None
        self._learned_seq = 0
        self.dropped = 0

    def push(self, sample: AirSample) -> None:
        with self._lock:
            if sample.tap_gen < self._min_gen:
                return
            if len(self._samples) == FEED_MAX:
                self.dropped += 1
            self._samples.append(sample)

    def set_lock(self, state: str, confidence: float | None) -> None:
        with self._lock:
            self._lock_state = (state, confidence)

    def set_target(
        self,
        *,
        latency_ms: float | None,
        d_ref_ms: float | None = None,
        learned_ms: float | None = None,
        learned_d_ref_ms: float | None = None,
        learned_starts: int | None = None,
    ) -> None:
        source: Literal["calibrated", "learned"] | None
        if latency_ms is not None:
            source, lat, ref = "calibrated", latency_ms, d_ref_ms
        elif learned_ms is not None and learned_starts is not None and learned_starts >= LEARN_MIN_SAMPLES:
            source, lat, ref = "learned", learned_ms, learned_d_ref_ms
        else:
            source, lat, ref = None, None, None
        with self._lock:
            self._target = AirTarget(
                lat, ref, source, learned_ms, learned_d_ref_ms, learned_starts, self._target.version + 1
            )

    def set_key(self, codec: str | None, rate: int | None, adapter: str | None) -> None:
        with self._lock:
            self._key = (codec, rate, adapter)

    def set_output_delay(self, d_out_ms: float) -> None:
        with self._lock:
            self._d_out_ms = d_out_ms

    def reopen(self, min_tap_gen: int) -> None:
        with self._lock:
            self._min_gen = max(self._min_gen, min_tap_gen)
            self._samples.clear()

    def take(self) -> FeedView:
        with self._lock:
            samples = tuple(self._samples)
            self._samples.clear()
            return FeedView(samples, self._lock_state, self._target, self._key, self._d_out_ms)

    def next_learned_seq(self) -> int:
        with self._lock:
            self._learned_seq += 1
            return self._learned_seq


class EHistory:
    """Pulsesink readings keyed by written stream time (feeder-only)."""

    def __init__(self) -> None:
        self._rows: deque[tuple[float, float, float, bool]] = deque()

    def add(self, t_us: float, x_port_ns: float, e_us: float, *, usable: bool) -> None:
        self._rows.append((t_us, x_port_ns, e_us, usable))
        cutoff = t_us - E_HISTORY_S * 1e6
        while self._rows and self._rows[0][0] < cutoff:
            self._rows.popleft()

    def median_over(self, x_lo_ns: float, x_hi_ns: float) -> tuple[float, int] | None:
        vals = [e for _, x, e, ok in self._rows if ok and x_lo_ns <= x < x_hi_ns]
        if not vals or len(vals) < E_MIN_COVERAGE * SENSE_HZ * (x_hi_ns - x_lo_ns) / 1e9:
            return None
        return float(median(vals)), len(vals)


@dataclass(frozen=True, slots=True)
class AirAction:
    jump_us: float  # + drop (late), - insert
    reason: Literal["air_seed", "air_target"]


@dataclass(frozen=True, slots=True)
class LearnedSample:
    seq: int
    x_ms: float  # median(d_out_i - h_i) of the triple
    d_out_ms: float  # median(d_out_i)
    codec: str
    rate: int
    adapter: str


def _mad(values: list[float]) -> float:
    centre = median(values)
    return float(median([abs(v - centre) for v in values]))


class AirBias:
    """The air-sensor bias ``b``: seeding, tracking, gate, learned sample (feeder-only)."""

    def __init__(self, feed: AirFeed, *, log: logging.Logger = logger) -> None:
        self._feed = feed
        self._log = log
        self._state: BiasState = "none"
        self._b: float | None = None
        self._seeded_now = False
        self._learned: LearnedSample | None = None
        self._learned_tried = False
        self._target = _NO_TARGET
        self._target_taken = False
        self._d_out_latched_ms: float | None = None
        self._sum_dd_us = 0.0
        self._event_x = -math.inf
        self._def_x = -math.inf
        self._cands: deque[tuple[AirSample, float]] = deque(maxlen=SEED_FALLBACK_WINDOWS)
        self._n_elig = 0
        self._history: deque[float] = deque(maxlen=GATE_HISTORY)
        self._gated_run: deque[float] = deque(maxlen=STEP_WINDOWS)
        self._at_prev: float | None = None
        self._corrections = 0
        self._last_correction_us: float | None = None
        self._prev_last_correction_us: float | None = None
        self._gated = 0
        self._skipped = 0
        self._frozen = False
        self._frozen_since = 0.0
        self._reseed_pending = False
        self._resume_beta_us = 0.0

    # -- properties ---------------------------------------------------
    @property
    def state(self) -> BiasState:
        return self._state

    @property
    def value_us(self) -> float | None:
        return self._b

    @property
    def seeded_now(self) -> bool:
        return self._seeded_now

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def learned(self) -> LearnedSample | None:
        return self._learned

    @property
    def reseed_pending(self) -> bool:
        return self._reseed_pending

    @property
    def handles_breaks(self) -> bool:
        """``b`` has a value outside ``held``: the engine measures no baseline (C1)."""
        return self._b is not None and self._state != "held"

    def resume_beta(self) -> float:
        """The ``beta`` folded into ``b`` by the last update's resume from ``held``; the engine clears it."""
        return self._resume_beta_us

    def drop_action(self) -> None:
        """The engine did not execute the last action (a placement was pending): it does not count."""
        if self._corrections:
            self._corrections -= 1
            self._last_correction_us = self._prev_last_correction_us

    def on_event(self, x_ns: float) -> None:
        """A placement or a delay/steady/air jump written at ``x`` (S-3)."""
        self._event_x = max(self._event_x, x_ns)
        self._n_elig = 0
        self._drop_stale_candidates()

    def on_delay(self, delta_d_us: float, x_ns: float) -> None:
        """An output delay change ``dD``: ``b`` is unchanged, the sound moves by ``-dD`` (S-14)."""
        before = self._sum_dd_us
        self._sum_dd_us += delta_d_us
        self.on_event(x_ns)
        ref = self._target.d_ref_ms if self._target.d_ref_ms is not None else self._d_out_latched_ms
        if self._target.latency_ms is not None and self._d_out_latched_ms is not None and ref is not None:
            d_old = self._d_out_latched_ms + before / 1000.0
            d_new = self._d_out_latched_ms + self._sum_dd_us / 1000.0
            self._log.info(
                "Output delay changed %.0f\u2192%.0f ms; air target trim \u2212(D_out \u2212 D_ref) = %.1f ms",
                d_old,
                d_new,
                -(d_new - ref),
            )

    def _restart_triple(self, x_ns: float) -> None:
        self._def_x = max(self._def_x, x_ns)
        self._cands.clear()
        self._n_elig = 0
        self._gated_run.clear()

    def on_format(self, x_ns: float) -> None:
        """The stream format changed at ``x``: reseed provisionally (the value is kept)."""
        self._restart_triple(x_ns)
        if self._b is None:
            return
        if self._state == "held":
            self._reseed_pending = True
        else:
            self._state = "seeding"

    def on_reading_break(self, kind: ReadingBreak, x_ns: float, now_s: float) -> None:
        """An underrun, stall or pulse step broke the pulsesink readings at ``x``."""
        if self._state == "held":
            self._reseed_pending = True
            self._def_x = max(self._def_x, x_ns)
            return
        if self._b is None and kind == "pa_step":
            return  # the initial triple is air-only; a clock sawtooth (PipeWire) steps every few seconds
        self._restart_triple(x_ns)
        if self._b is None:
            return
        self._state = "seeding"
        self._frozen = True
        self._frozen_since = now_s

    # -- helpers ------------------------------------------------------
    def _floor_x(self) -> float:
        return max(self._event_x, self._def_x)

    def _drop_stale_candidates(self) -> None:
        floor = self._floor_x()
        if any(s.x_lo_ns < floor for s, _ in self._cands):
            kept = [(s, e) for s, e in self._cands if s.x_lo_ns >= floor]
            self._cands.clear()
            self._cands.extend(kept)

    def _l_eff_minus_d_ref_us(self) -> float:
        lat = self._target.latency_ms
        ref = self._target.d_ref_ms
        if ref is None:
            ref = self._d_out_latched_ms
        if lat is None or ref is None:
            return 0.0
        return (lat - ref) * 1000.0

    def _e_equiv(self, h_us: float) -> float:
        return h_us + self._l_eff_minus_d_ref_us()

    def _accepts(self, target: AirTarget) -> bool:
        """I3: a learned target is taken until this output published its own sample."""
        if target.source != "learned" or self._learned is None:
            return True
        return self._target.source == "calibrated"  # a forgotten calibration falls back

    def _adopt_target(self, target: AirTarget) -> AirAction | None:
        if target.version == self._target.version and self._target_taken:
            return None
        if not self._accepts(target):
            return None
        old_delta = self._l_eff_minus_d_ref_us()
        self._target = target
        self._target_taken = True
        if self._b is None:
            if self._state == "none" and target.latency_ms is not None:
                self._state = "seeding"
                self._n_elig = 0
            elif self._state == "seeding" and target.latency_ms is None:
                self._state = "none"
            return None
        if target.latency_ms is None:  # forgotten without a fallback: the bias goes to none
            self._b = None
            self._state = "none"
            self._frozen = False
            self._reseed_pending = False
            self._cands.clear()
            self._n_elig = 0
            return None
        delta_l = self._l_eff_minus_d_ref_us() - old_delta
        self._b -= delta_l
        for i, v in enumerate(self._history):
            self._history[i] = v - delta_l
        if abs(delta_l) <= DEAD_BAND_US:
            return None
        self._prev_last_correction_us = self._last_correction_us
        self._corrections += 1
        self._last_correction_us = delta_l
        return AirAction(delta_l, "air_target")

    def _lose_lock(self, lock_state: str) -> None:
        if self._b is None or self._state in ("none", "held"):
            return  # initial seeding waits
        self._reseed_pending = self._state == "seeding"
        self._state = "held"
        self._frozen = False
        self._cands.clear()
        self._n_elig = 0
        self._log.info("air bias held (sensor %s)", lock_state)

    def _eligible(self, s: AirSample, hist: EHistory) -> float | None:
        """Return ê for an eligible sample, None (counted) otherwise."""
        if (
            s.confidence < MIN_CONFIDENCE
            or s.x_lo_ns < self._floor_x()
            or self._event_x > s.x_lo_ns  # (x_lo, x_hi) holds no event
        ):
            self._skipped += 1
            return None
        got = hist.median_over(s.x_lo_ns, s.x_hi_ns)
        if got is None:
            self._skipped += 1
            return None
        return float(got[0])

    # -- the update ---------------------------------------------------
    def update(self, now_s: float, history: EHistory, *, beta_us: float = 0.0) -> AirAction | None:
        self._seeded_now = False
        self._resume_beta_us = 0.0
        view = self._feed.take()
        if self._d_out_latched_ms is None:
            if view.d_out_ms is not None:
                self._d_out_latched_ms = view.d_out_ms
            elif view.samples:
                self._d_out_latched_ms = view.samples[0].d_out_us / 1000.0
        action = self._adopt_target(view.target)
        locked = view.lock[0] in _LOCK_OK
        if not locked:
            self._lose_lock(view.lock[0])
        if not locked or action is not None:
            # windows drained while unlocked, or across a target jump, are not used
            self._skipped += len(view.samples)
            return action
        for s in view.samples:
            e_hat = self._eligible(s, history)
            if e_hat is None:
                continue
            if self._state == "held":
                a = self._resume(s, e_hat, view.key, beta_us)
            elif self._state == "active":
                self._track(s, e_hat)
                continue
            else:
                a = self._seeding(s, e_hat, view.key)
            if a is not None:
                action = a
        if self._frozen and now_s - self._frozen_since >= RESEED_TIMEOUT_S:
            # no reseed in time: keep the provisional value, track it (no baseline)
            self._state = "active"
            self._frozen = False
            self._cands.clear()
            self._n_elig = 0
            self._gated_run.clear()
            self._at_prev = None
        return action

    def _resume(
        self, s: AirSample, e_hat: float, key: tuple[str | None, int | None, str | None], beta_us: float
    ) -> AirAction | None:
        assert self._b is not None
        self._b += beta_us  # C1: the baseline measured while held belongs to b
        self._resume_beta_us = beta_us
        if self._reseed_pending:
            self._reseed_pending = False
            self._state = "seeding"
            self._cands.clear()
            self._n_elig = 0
            return self._seeding(s, e_hat, key)
        self._state = "active"
        self._track(s, e_hat)
        return None

    def _seeding(self, s: AirSample, e_hat: float, key: tuple[str | None, int | None, str | None]) -> AirAction | None:
        self._cands.append((s, e_hat))
        self._n_elig += 1
        last = list(self._cands)[-SEED_WINDOWS:]
        if len(last) == SEED_WINDOWS:
            if self._state == "none":
                vals = [c.h_us for c, _ in last]
            else:
                vals = [self._e_equiv(c.h_us) for c, _ in last]
            if max(vals) - min(vals) <= SEED_AGREE_US:
                self._maybe_learn(last, key)
                if self._state == "seeding":
                    return self._seed(last)
        if self._state == "seeding" and self._n_elig >= SEED_MAX_WINDOWS and len(self._cands) >= SEED_FALLBACK_WINDOWS:
            recent = list(self._cands)[-SEED_FALLBACK_WINDOWS:]
            bs = [e - self._e_equiv(c.h_us) for c, e in recent]
            self._b = float(median(bs))
            self._history.clear()
            self._history.extend(bs)
            self._enter_active(s)
        return None

    def _maybe_learn(self, last: list[tuple[AirSample, float]], key: tuple[str | None, int | None, str | None]) -> None:
        if self._learned is not None or self._learned_tried or self._b is not None:
            return
        self._learned_tried = True
        codec, rate, adapter = key
        if codec is None or rate is None or adapter is None:
            return
        x_us = float(median([c.d_out_us - c.h_us for c, _ in last]))
        d_us = float(median([c.d_out_us for c, _ in last]))
        self._learned = LearnedSample(self._feed.next_learned_seq(), x_us / 1000.0, d_us / 1000.0, codec, rate, adapter)

    def _enter_active(self, s: AirSample) -> None:
        self._state = "active"
        self._seeded_now = True
        self._frozen = False
        self._learned_tried = True
        self._at_prev = s.at_us
        self._gated_run.clear()
        self._cands.clear()
        self._n_elig = 0

    def _seed(self, last: list[tuple[AirSample, float]]) -> AirAction | None:
        es = [self._e_equiv(c.h_us) for c, _ in last]
        bs = [e - ei for (_, e), ei in zip(last, es, strict=True)]
        self._b = float(median(bs))
        self._history.clear()
        self._history.extend(bs)
        self._enter_active(last[-1][0])
        jump = float(median(es))
        self._log.info(
            "air bias seeded at %.1f ms (E %+.1f ms, %d windows within %.1f ms)",
            self._b / 1000.0,
            jump / 1000.0,
            SEED_WINDOWS,
            (max(es) - min(es)) / 1000.0,
        )
        if abs(jump) <= DEAD_BAND_US:
            return None
        self._prev_last_correction_us = self._last_correction_us
        self._corrections += 1
        self._last_correction_us = jump
        return AirAction(jump, "air_seed")

    def _track(self, s: AirSample, e_hat: float) -> None:
        assert self._b is not None
        b_i = e_hat - self._e_equiv(s.h_us)
        dt = 0.0 if self._at_prev is None else min(max((s.at_us - self._at_prev) / 1e6, 0.0), DT_MAX_S)
        self._at_prev = s.at_us
        hist = list(self._history)
        gate = max(GATE_MIN_US, GATE_MAD_K * 1.4826 * _mad(hist)) if hist else GATE_MIN_US
        if abs(b_i - self._b) <= gate:
            self._b += (1.0 - math.exp(-dt / BIAS_TAU_S)) * (b_i - self._b)
            self._history.append(b_i)
            self._gated_run.clear()
            return
        self._gated += 1
        self._gated_run.append(b_i)
        if len(self._gated_run) == STEP_WINDOWS:
            centre = float(median(self._gated_run))
            if sum(abs(v - centre) <= STEP_AGREE_US for v in self._gated_run) < STEP_MIN_AGREE:
                return
            old = self._b
            self._b = centre
            self._history.clear()
            self._history.extend(self._gated_run)
            self._gated_run.clear()
            self._log.info("air bias step: %.1f \u2192 %.1f ms", old / 1000.0, self._b / 1000.0)

    # -- telemetry ----------------------------------------------------
    def snapshot(self) -> dict[str, object]:
        t = self._target
        ref = t.d_ref_ms if t.d_ref_ms is not None else self._d_out_latched_ms
        trim: float | None = None
        if ref is not None and self._d_out_latched_ms is not None and t.latency_ms is not None:
            trim = self._d_out_latched_ms + self._sum_dd_us / 1000.0 - ref
        learned = self._learned
        return {
            "air_bias_state": self._state,
            "air_bias_ms": None if self._b is None else self._b / 1000.0,
            "air_bias_reason": {"none": "no_target", "held": "not_locked"}.get(self._state),
            "air_target": t.source,
            "air_target_ms": t.latency_ms,
            "air_d_ref_ms": ref if t.latency_ms is not None else None,
            "air_trim_ms": trim,
            "air_learned_starts": t.learned_starts,
            "air_corrections": self._corrections,
            "air_last_correction_ms": None if self._last_correction_us is None else self._last_correction_us / 1000.0,
            "air_bias_gated": self._gated,
            "air_bias_skipped": self._skipped,
            "air_learned": None
            if learned is None
            else {
                "seq": learned.seq,
                "x_ms": learned.x_ms,
                "d_out_ms": learned.d_out_ms,
                "codec": learned.codec,
                "rate": learned.rate,
                "adapter": learned.adapter,
            },
        }
