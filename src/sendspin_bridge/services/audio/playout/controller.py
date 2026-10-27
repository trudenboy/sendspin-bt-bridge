"""Loop controller of the playout engine: outlier gate, filter and steady jumps.

Sign convention: positive error = late, ``jump_us`` > 0 drops audio (catch up),
< 0 inserts. In measure mode the rate stays 0.0; the PI loop is added later.
"""

from __future__ import annotations

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
JUMP_CONFIRM = 5
JUMP_MIN_INTERVAL_S = 10.0
HOLDOFF_S = 1.0
_MAD_SCALE = 1.4826

Mode = Literal["measure", "loop"]


@dataclass(frozen=True, slots=True)
class Decision:
    rate: float  # s/s; + drops (catch up when late); always 0.0 in measure
    jump_us: float | None  # + drop this much, - insert this much; None = no jump
    error_us: float | None  # filtered error (EMA), None before the first accepted sample
    rejected: bool
    saturated: bool
    degraded: bool  # a steady jump was refused by the 10 s interval


class LoopController:
    def __init__(self, *, mode: Mode) -> None:
        self._mode: Mode = mode
        self._alpha = T_S / TAU_F_S
        self._window: deque[float] = deque(maxlen=HAMPEL_N)
        self._ema: float | None = None
        self._outliers = 0
        self._run: list[float] = []
        self._last_event_s: float | None = None
        self._holdoff_until_s = 0.0

    @property
    def rate_estimate(self) -> float:
        return 0.0

    @property
    def holdoff_until_s(self) -> float:
        return self._holdoff_until_s

    def placed(self, t_s: float, residual_us: float = 0.0) -> None:
        """A placement or steady jump happened: reset EMA and Hampel to residual, hold P off."""
        self._ema = residual_us
        self._window.clear()
        self._window.append(residual_us)
        self._outliers = 0
        self._run.clear()
        self._last_event_s = t_s
        self._holdoff_until_s = t_s + HOLDOFF_S

    def _check_jump(self, t_s: float, e_w_us: float) -> tuple[float | None, bool]:
        threshold = JUMP_US[self._mode]
        if abs(e_w_us) <= threshold:
            self._run.clear()
            return None, False
        if self._run and (self._run[-1] > 0) != (e_w_us > 0):
            self._run.clear()
        self._run.append(e_w_us)
        if len(self._run) < JUMP_CONFIRM:
            return None, False
        if self._last_event_s is not None and t_s - self._last_event_s < JUMP_MIN_INTERVAL_S:
            return None, True
        return statistics.median(self._run[-JUMP_CONFIRM:]), False

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
            self.placed(t_s)
            return Decision(0.0, jump, self._ema, False, False, False)
        accepted = self._gate(e_w_us)
        if accepted:
            self._ema = e_w_us if self._ema is None else self._ema + self._alpha * (e_w_us - self._ema)
        return Decision(0.0, None, self._ema, not accepted, False, degraded)
