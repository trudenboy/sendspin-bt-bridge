"""Loop controller of the playout engine: outlier gate, filter and steady jumps.

Sign convention: positive error = late, ``jump_us`` > 0 drops audio (catch up),
< 0 inserts. In measure mode the rate stays 0.0; the PI loop is added later.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Literal

T_S = 0.1
HAMPEL_N, HAMPEL_K, STEP_CONFIRM = 9, 3.0, 5
# 2 x the measured sink-clock noise (sigma = 4 ms on PulseAudio 17).
HAMPEL_FLOOR_US = 8000.0
TAU_F_S = 1.0
JUMP_US = {"measure": 20_000.0, "loop": 10_000.0}
JUMP_CONFIRM = 5  # the jump size is the median of the last 5 raw samples
# A steady jump needs the raw error beyond the threshold, with one sign, in at least
# JUMP_PERSIST_FRACTION of the samples over the last JUMP_PERSIST_S (a low percentile of the
# error in that direction exceeds the threshold). The pulsesink clock under PipeWire is a
# sawtooth of up to ~30 ms that resets every few seconds (E1, #556): it crosses the
# threshold but falls back inside it within the window, so it is never corrected. A real
# drift stays beyond it: at 3.5 ms/s (#546) the jump comes at ~36 ms, inside the clamp.
JUMP_PERSIST_S = 5.0
JUMP_PERSIST_FRACTION = 0.9
# Steady jumps remove a small residual error: one jump moves the audio by at most this much
# (a 459 ms jump followed two underruns in #556). A larger persistent error is corrected in
# clamped steps, each after the usual persistence and interval.
MAX_STEADY_JUMP_US = 50_000.0
JUMP_MIN_INTERVAL_S = 10.0
HOLDOFF_S = 1.0
_MAD_SCALE = 1.4826
# Stage B2 (#546): inside the jump threshold the error is removed by playing a hair fast or slow
# instead of by a jump that clicks. 1 ms is corrected in ~30 s (333 ppm at 10 ms), which is 0.6
# cents of pitch at most; an error under the dead band is left alone (the sink clock jitters more).
RATE_GAIN_PER_US = 1.0 / 30e6
RATE_MAX = 4e-4
RATE_DEADBAND_US = 500.0

Mode = Literal["measure", "loop"]


@dataclass(frozen=True, slots=True)
class Decision:
    rate: float  # s/s; + drops (catch up when late); always 0.0 in measure
    jump_us: float | None  # + drop this much, - insert this much; None = no jump
    error_us: float | None  # filtered error (EMA), None before the first accepted sample
    rejected: bool
    saturated: bool
    degraded: bool  # a steady jump was refused by the 10 s interval
    clamped_from_us: float | None = None  # the error a jump clamped to MAX_STEADY_JUMP_US was for


class LoopController:
    def __init__(self, *, mode: Mode) -> None:
        self._mode: Mode = mode
        self._alpha = T_S / TAU_F_S
        self._window: deque[float] = deque(maxlen=HAMPEL_N)
        self._ema: float | None = None
        self._outliers = 0
        self._run: deque[tuple[float, float]] = deque()  # (t_s, raw e_w) of the last JUMP_PERSIST_S
        self._last_event_s: float | None = None
        self._holdoff_until_s = 0.0
        self._quiet_wait = False
        # The error a placement left behind: the rate correction works around it, so a delay change
        # or an underrun baseline is kept (the contract tests pin that) and only drift is removed.
        self._rate_ref_us = 0.0  # waiting for the interval after the last event is expected

    @property
    def rate_estimate(self) -> float:
        return 0.0

    @property
    def holdoff_until_s(self) -> float:
        return self._holdoff_until_s

    def placed(self, t_s: float, residual_us: float = 0.0, *, quiet_wait: bool = False) -> None:
        """A placement or steady jump happened: reset EMA and Hampel to residual, hold P off.

        ``quiet_wait``: a persistent error that waits for the 10 s interval after this event is
        expected (a placement, or a clamped jump with more steps to come), not "degraded".
        """
        self._ema = residual_us
        self._window.clear()
        self._window.append(residual_us)
        self._outliers = 0
        self._run.clear()
        self._last_event_s = t_s
        self._rate_ref_us = residual_us
        self._holdoff_until_s = t_s + HOLDOFF_S
        self._quiet_wait = quiet_wait

    def _check_jump(self, t_s: float, e_w_us: float) -> tuple[float | None, bool]:
        threshold = JUMP_US[self._mode]
        run = self._run
        run.append((t_s, e_w_us))
        while t_s - run[0][0] > JUMP_PERSIST_S:
            run.popleft()
        # The window (cleared by every event) must span JUMP_PERSIST_S of readings.
        if t_s - run[0][0] < JUMP_PERSIST_S - T_S * 1.5:
            return None, False
        last = statistics.median(e for _, e in list(run)[-JUMP_CONFIRM:])
        if abs(last) <= threshold:
            return None, False
        beyond = sum(1 for _, e in run if (e > threshold if last > 0 else e < -threshold))
        if beyond < JUMP_PERSIST_FRACTION * len(run):
            return None, False
        if self._last_event_s is not None and t_s - self._last_event_s < JUMP_MIN_INTERVAL_S:
            return None, not self._quiet_wait
        return last, False

    def _gate(self, e_w_us: float) -> bool:
        """Return True when the sample is accepted."""
        if not self._window:
            self._window.append(e_w_us)
            return True
        med = statistics.median(self._window)
        mad = statistics.median(abs(v - med) for v in self._window)
        limit = max(HAMPEL_FLOOR_US, HAMPEL_K * _MAD_SCALE * mad)
        if abs(e_w_us - med) <= limit:
            self._outliers = 0
            self._window.append(e_w_us)
            return True
        self._outliers += 1
        if self._outliers >= STEP_CONFIRM:
            self._outliers = 0
            self._window.clear()
            self._window.append(e_w_us)
            return True
        return False

    def update(self, t_s: float, e_w_us: float) -> Decision:
        jump, degraded = self._check_jump(t_s, e_w_us)
        if jump is not None:
            clamped = abs(jump) > MAX_STEADY_JUMP_US
            self.placed(t_s, quiet_wait=clamped)
            if clamped:
                return Decision(0.0, math.copysign(MAX_STEADY_JUMP_US, jump), self._ema, False, False, False, jump)
            return Decision(0.0, jump, self._ema, False, False, False)
        accepted = self._gate(e_w_us)
        if accepted:
            self._ema = e_w_us if self._ema is None else self._ema + self._alpha * (e_w_us - self._ema)
        return Decision(self._rate_for(self._ema), None, self._ema, not accepted, False, degraded)

    def _rate_for(self, e_us: float | None) -> float:
        """The rate correction for the error *around the error a placement left*.

        Zero in measure mode, inside the dead band, and while a jump is the answer (the caller
        returns before asking then).
        """
        if self._mode != "loop" or e_us is None:
            return 0.0
        drift = e_us - self._rate_ref_us
        if abs(drift) < RATE_DEADBAND_US:
            return 0.0
        return max(-RATE_MAX, min(RATE_MAX, RATE_GAIN_PER_US * drift))
