"""Small statistics helpers for playout-sync telemetry."""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
