"""Map kernel ``CLOCK_REALTIME`` monitor stamps onto ``CLOCK_MONOTONIC_RAW``.

The kernel stamps every HCI monitor frame in ``CLOCK_REALTIME`` (``SO_TIMESTAMPNS``), while the air
sensor works in ``CLOCK_MONOTONIC_RAW`` µs. :class:`RawRealtimeMap` takes bracketed pairs
``(raw0, real, raw1)`` — accepted when ``raw1 - raw0 <= max_bracket_ns``, up to three tries — and
keeps the offset ``o = real - (raw0 + raw1) / 2``. A stamp's RAW time is ``t_real - o`` with ``o``
linearly interpolated between the two pairs that bracket it; a stamp outside every bracket has no
RAW time yet (the caller waits for the next pair). An offset step larger than ``step_us`` between
consecutive pairs is a clock step: it bumps :attr:`epoch` and no stamp is interpolated across it.

The caller (the monitor hub) samples every 250 ms; this class keeps no thread and no timer.
"""

from __future__ import annotations

import itertools
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_RAW_CLOCK: int = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)
_BRACKET_TRIES = 3
# Pairs kept within one epoch: two are enough to interpolate, a few more let a stamp that waited
# in the socket queue past the next sample still find its bracket (8 x 250 ms = 2 s).
_MAX_PAIRS = 8


def _raw_ns() -> int:
    return time.clock_gettime_ns(_RAW_CLOCK)


class RawRealtimeMap:
    """REALTIME ns -> RAW µs from bracketed pairs (bracket <= 50 µs, 3 tries); detects steps > 5 ms."""

    def __init__(
        self,
        *,
        raw_ns: Callable[[], int] = _raw_ns,
        real_ns: Callable[[], int] = time.time_ns,
        max_bracket_ns: int = 50_000,
        step_us: float = 5_000.0,
    ) -> None:
        self._raw_ns = raw_ns
        self._real_ns = real_ns
        self._max_bracket_ns = max_bracket_ns
        self._step_ns = step_us * 1000.0
        # (real_ns, offset_ns) with offset = real - raw_mid, oldest first, all in the current epoch.
        # Integers on purpose: REALTIME ns (~1.7e18) does not fit a float's 53-bit mantissa.
        self._pairs: deque[tuple[int, int]] = deque(maxlen=_MAX_PAIRS)
        self._epoch = 0

    @property
    def epoch(self) -> int:
        return self._epoch

    def sample(self) -> bool:
        """Take one bracketed pair; ``False`` when every try's bracket was too wide."""
        for _ in range(_BRACKET_TRIES):
            raw0 = self._raw_ns()
            real = self._real_ns()
            raw1 = self._raw_ns()
            if raw1 - raw0 <= self._max_bracket_ns:
                self._add(real, real - (raw0 + raw1) // 2)
                return True
        return False

    def _add(self, real: int, offset: int) -> None:
        if self._pairs:
            last_real, last_offset = self._pairs[-1]
            if abs(offset - last_offset) > self._step_ns or real <= last_real:
                self._epoch += 1
                self._pairs.clear()
        self._pairs.append((real, offset))

    def to_raw_us(self, real_ns: int) -> float | None:
        """RAW µs for a REALTIME stamp; ``None`` until two pairs of the current epoch bracket it."""
        pairs = self._pairs
        if len(pairs) < 2 or not pairs[0][0] <= real_ns <= pairs[-1][0]:
            return None
        r0, o0 = pairs[-2]
        if r0 < real_ns:  # the newest bracket, where nearly every stamp lands: no earlier pair holds it
            r1, o1 = pairs[-1]
            return ((real_ns - o0) - (o1 - o0) * (real_ns - r0) / (r1 - r0)) / 1000.0
        for (r0, o0), (r1, o1) in itertools.pairwise(pairs):
            if r0 <= real_ns <= r1:
                slew = (o1 - o0) * (real_ns - r0) / (r1 - r0)
                return ((real_ns - o0) - slew) / 1000.0
        return None
