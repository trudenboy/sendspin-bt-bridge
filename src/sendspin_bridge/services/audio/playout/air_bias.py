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

import threading
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Literal

FEED_MAX = 32
E_HISTORY_S = 30.0
E_MIN_COVERAGE = 0.5
SENSE_HZ = 10.0
LEARN_MIN_SAMPLES = 2


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
            self._min_gen = min_tap_gen
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
