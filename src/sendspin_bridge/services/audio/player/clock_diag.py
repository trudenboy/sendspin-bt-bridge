"""Rate of one clock against a reference clock, for diagnostics.

Samples are ``(reference_us, clock_us)`` pairs. A least-squares line over the
last ``window_s`` seconds gives the rate (ppm away from 1.0) and the residual
standard deviation (how much the clock jitters around that rate).
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

_MIN_SAMPLES = 3


@dataclass(frozen=True, slots=True)
class RateStats:
    ppm: float
    sigma_us: float
    span_s: float
    samples: int


class RateTracker:
    def __init__(self, *, window_s: float) -> None:
        self._window_us = int(window_s * 1_000_000)
        self._samples: deque[tuple[int, int]] = deque()

    def add(self, reference_us: int, clock_us: int) -> None:
        self._samples.append((int(reference_us), int(clock_us)))
        while self._samples and reference_us - self._samples[0][0] > self._window_us:
            self._samples.popleft()

    def reset(self) -> None:
        self._samples.clear()

    def stats(self) -> RateStats | None:
        n = len(self._samples)
        if n < _MIN_SAMPLES:
            return None
        x0, y0 = self._samples[0]
        xs = [float(x - x0) for x, _ in self._samples]
        ys = [float(y - y0) for _, y in self._samples]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        sxx = sum((x - mean_x) ** 2 for x in xs)
        if sxx == 0:
            return None
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
        intercept = mean_y - slope * mean_x
        residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys, strict=True)]
        sigma = math.sqrt(sum(r * r for r in residuals) / max(1, n - 2))
        return RateStats(
            ppm=(slope - 1.0) * 1_000_000,
            sigma_us=sigma,
            span_s=(xs[-1] - xs[0]) / 1_000_000,
            samples=n,
        )


_DEBUG_EVERY_US = 10_000_000
_INFO_EVERY_US = 60_000_000


class ClockDiagnostics:
    """Clock rates the daemon logs while playing: DEBUG every 10 s, INFO every minute.

    MONOTONIC vs RAW is what NTP slews (GStreamer and PulseAudio run on
    MONOTONIC, the Sendspin schedule on RAW). The audio clock is the sink's
    playback clock, read only when the sink was built to provide it.
    """

    def __init__(self, *, audio_clock_enabled: bool, window_s: float = 60.0) -> None:
        self._audio_enabled = audio_clock_enabled
        self._mono = RateTracker(window_s=window_s)
        self._audio = RateTracker(window_s=window_s)
        self._generation: int | None = None
        self._last_audio_ns: int | None = None
        self._next_due_us: int | None = None
        self._ticks = 0

    def sample(self, *, raw_us: int, mono_us: int, audio_ns: int | None, generation: int) -> None:
        self._mono.add(raw_us, mono_us)
        if self._next_due_us is None:
            self._next_due_us = raw_us + _DEBUG_EVERY_US
        if generation != self._generation or audio_ns is None:
            self._audio.reset()
            self._generation = generation
            self._last_audio_ns = None
        if audio_ns is None:
            return
        if self._last_audio_ns is not None and audio_ns < self._last_audio_ns:
            self._audio.reset()
        if self._last_audio_ns is None or audio_ns != self._last_audio_ns:
            # A clock that does not move yet (ring buffer not started) says nothing.
            self._audio.add(raw_us, audio_ns // 1000)
        self._last_audio_ns = audio_ns

    def due(self, *, raw_us: int) -> tuple[int, str] | None:
        """The line to log now, with its level, or None."""
        if self._next_due_us is None or raw_us < self._next_due_us:
            return None
        self._next_due_us += _DEBUG_EVERY_US
        if self._next_due_us <= raw_us:  # after a pause: no burst of overdue lines
            self._next_due_us = raw_us + _DEBUG_EVERY_US
        self._ticks += 1
        level = logging.INFO if self._ticks % (_INFO_EVERY_US // _DEBUG_EVERY_US) == 0 else logging.DEBUG
        return level, self.line()

    def reset_schedule(self) -> None:
        self._next_due_us = None
        self._ticks = 0

    def line(self) -> str:
        return f"Clock diag: MONOTONIC vs RAW {_rate(self._mono.stats())}; audio clock vs RAW {self._audio_part()}"

    def _audio_part(self) -> str:
        if not self._audio_enabled:
            return "n/a (set SENDSPIN_PLAYOUT_DIAG=1 to read the sink's audio clock)"
        return _rate(self._audio.stats())


def _rate(stats: RateStats | None) -> str:
    if stats is None:
        return "warming up"
    return f"{stats.ppm:+.2f} ppm (sd {stats.sigma_us:.1f} us over {stats.span_s:.0f} s, {stats.samples} samples)"
