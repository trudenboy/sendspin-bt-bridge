"""Small statistics helpers for playout-sync telemetry."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable


class LinearTrend:
    """Least-squares slope of y(t) over a sliding time window."""

    def __init__(self, window_s: float) -> None:
        self._window_s = float(window_s)
        self._points: deque[tuple[float, float]] = deque()

    def add(self, t_s: float, y: float) -> None:
        self._points.append((t_s, y))
        cutoff = t_s - self._window_s
        while self._points and self._points[0][0] < cutoff:
            self._points.popleft()

    def slope(self) -> float | None:
        """y units per second; None with < 3 points or a span < window/4."""
        n = len(self._points)
        if n < 3:
            return None
        if self._points[-1][0] - self._points[0][0] < self._window_s / 4.0:
            return None
        # Centre on the first point to keep the sums well conditioned.
        t0 = self._points[0][0]
        y0 = self._points[0][1]
        sum_t = sum_y = 0.0
        for t, y in self._points:
            sum_t += t - t0
            sum_y += y - y0
        mean_t = sum_t / n
        mean_y = sum_y / n
        sxx = sxy = 0.0
        for t, y in self._points:
            dt = (t - t0) - mean_t
            sxx += dt * dt
            sxy += dt * ((y - y0) - mean_y)
        if sxx <= 0.0:
            return None
        return sxy / sxx

    def reset(self) -> None:
        self._points.clear()


class ClockSlewEstimator:
    """CLOCK_MONOTONIC rate against CLOCK_MONOTONIC_RAW, in ppm (60 s window)."""

    def __init__(
        self,
        *,
        raw_ns: Callable[[], int] | None = None,
        mono_ns: Callable[[], int] | None = None,
        window_s: float = 60.0,
    ) -> None:
        self._raw_ns = raw_ns or (lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW))
        self._mono_ns = mono_ns or time.monotonic_ns
        self._trend = LinearTrend(window_s)

    def sample(self) -> None:
        raw = self._raw_ns()
        mono = self._mono_ns()
        # x = RAW seconds, y = (MONO - RAW) seconds; the slope is the relative rate.
        self._trend.add(raw / 1e9, (mono - raw) / 1e9)

    def ppm(self) -> float | None:
        slope = self._trend.slope()
        return None if slope is None else slope * 1e6


class PlayoutStats:
    """Per-tick telemetry aggregates for the playout engine."""

    def __init__(self, *, window_s: float = 60.0, rate_window_s: float = 10.0) -> None:
        self._window_s = float(window_s)
        self._rate_window_s = float(rate_window_s)
        self._errors: deque[tuple[float, float]] = deque()  # (t_s, e_us)
        self._error_trend = LinearTrend(window_s)
        self._port_trend = LinearTrend(window_s)
        self._corrections: deque[tuple[float, int]] = deque()  # (t_s, frames)
        self._last_t: float | None = None
        self.inserted = 0
        self.dropped = 0
        self.jumps = 0
        self.underruns = 0
        self.pa_steps = 0
        self.rejected_samples = 0
        self._last_jump_us: float | None = None
        self._last_jump_reason: str | None = None
        self._last_jump_at: str | None = None

    def _seen(self, t_s: float) -> None:
        if self._last_t is None or t_s > self._last_t:
            self._last_t = t_s

    def error(self, t_s: float, e_us: float) -> None:
        self._seen(t_s)
        self._errors.append((t_s, e_us))
        cutoff = t_s - self._window_s
        while self._errors and self._errors[0][0] < cutoff:
            self._errors.popleft()
        self._error_trend.add(t_s, e_us / 1000.0)

    def port(self, t_s: float, a_minus_t_us: float) -> None:
        self._seen(t_s)
        self._port_trend.add(t_s, a_minus_t_us)

    def correction(self, t_s: float, inserted: int, dropped: int) -> None:
        self._seen(t_s)
        self.inserted += inserted
        self.dropped += dropped
        if inserted or dropped:
            self._corrections.append((t_s, inserted + dropped))

    def jump(self, at_iso: str, size_us: float, reason: str) -> None:
        self.jumps += 1
        self._last_jump_us = size_us
        self._last_jump_reason = reason
        self._last_jump_at = at_iso

    def underrun(self) -> None:
        self.underruns += 1

    def pa_step(self) -> None:
        self.pa_steps += 1

    def rejected(self) -> None:
        self.rejected_samples += 1

    def reset_window(self) -> None:
        """Forget the error window (counters and the sink-rate trend are kept)."""
        self._errors.clear()
        self._error_trend.reset()

    def _p95_ms(self) -> float | None:
        if not self._errors:
            return None
        mags = sorted(abs(e) for _, e in self._errors)
        rank = max(1, math.ceil(0.95 * len(mags)))
        return mags[rank - 1] / 1000.0

    def _corrections_per_s(self) -> float:
        if self._last_t is None:
            return 0.0
        cutoff = self._last_t - self._rate_window_s
        while self._corrections and self._corrections[0][0] <= cutoff:
            self._corrections.popleft()
        return sum(n for _, n in self._corrections) / self._rate_window_s

    def snapshot(self) -> dict[str, object]:
        """error_ms, error_p95_ms, error_slope_ms_per_s, corrections_per_s, counters, jump info
        and sink_rate_ppm; ms rounded to 3 decimals, ppm to 1. Unknown values are None."""
        slope = self._error_trend.slope()
        port_slope = self._port_trend.slope()  # µs per s == ppm
        p95 = self._p95_ms()
        return {
            "error_ms": round(self._errors[-1][1] / 1000.0, 3) if self._errors else None,
            "error_p95_ms": None if p95 is None else round(p95, 3),
            "error_slope_ms_per_s": None if slope is None else round(slope, 3),
            "corrections_per_s": round(self._corrections_per_s(), 3),
            "inserted": self.inserted,
            "dropped": self.dropped,
            "jumps": self.jumps,
            "last_jump_ms": None if self._last_jump_us is None else round(self._last_jump_us / 1000.0, 3),
            "last_jump_reason": self._last_jump_reason,
            "last_jump_at": self._last_jump_at,
            "underruns": self.underruns,
            "pa_steps": self.pa_steps,
            "rejected_samples": self.rejected_samples,
            "sink_rate_ppm": None if port_slope is None else round(port_slope, 1),
        }


class RateLimitedLog:
    """Emit a WARNING of one ``kind`` at most once per ``window_s`` (default 10 min)."""

    def __init__(self, log: logging.Logger, *, clock: Callable[[], float], window_s: float = 600.0) -> None:
        self._log = log
        self._clock = clock
        self._window_s = float(window_s)
        self._last: dict[str, float] = {}

    def warning(self, kind: str, msg: str, *args: object) -> bool:
        """Log the warning unless one of this kind went out within the window; True if logged."""
        now = self._clock()
        last = self._last.get(kind)
        if last is not None and now - last < self._window_s:
            return False
        self._last[kind] = now
        self._log.warning(msg, *args)
        return True
