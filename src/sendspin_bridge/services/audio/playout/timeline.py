"""Schedule map from output stream time (ns) to the scheduled RAW-clock time (µs)."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

SegmentKind = Literal["program", "silence"]


@dataclass(frozen=True, slots=True)
class Segment:
    x0_ns: int  # output stream time where the segment starts
    s0_us: float  # RAW time (µs) at which x0 is scheduled to leave the port
    kind: SegmentKind


class ScheduleMap:
    """Piecewise-linear map x (output stream time, ns) -> s (RAW time, µs).

    Within a segment, ``s(x) = s0 + (x - x0) / 1000``. Segments are appended in
    increasing ``x0`` order; the map keeps at most ``max_segments`` of them and
    forgets the oldest, so lookups before the oldest retained segment are unknown.
    """

    def __init__(self, *, max_segments: int = 4096) -> None:
        self._max_segments = max_segments
        self._x0s: list[int] = []
        self._segments: list[Segment] = []

    def append(self, x0_ns: int, s0_us: float, kind: SegmentKind) -> None:
        """Start a new segment at ``x0_ns``; an append at the same ``x0_ns`` replaces the last one."""
        if self._x0s:
            last = self._x0s[-1]
            if x0_ns < last:
                raise ValueError(f"x0 must not go backwards: {x0_ns} < {last}")
            if x0_ns == last:
                self._segments[-1] = Segment(x0_ns, s0_us, kind)
                return
        self._x0s.append(x0_ns)
        self._segments.append(Segment(x0_ns, s0_us, kind))
        while len(self._segments) > self._max_segments:
            del self._x0s[0]
            del self._segments[0]

    def at(self, x_ns: int) -> float | None:
        """Scheduled RAW time (µs) of output time ``x_ns``, or None if it precedes the map."""
        seg = self._containing(x_ns)
        if seg is None:
            return None
        return seg.s0_us + (x_ns - seg.x0_ns) / 1000.0

    def kind_at(self, x_ns: int) -> SegmentKind | None:
        """Kind of the segment containing ``x_ns``, or None if it precedes the map."""
        seg = self._containing(x_ns)
        return None if seg is None else seg.kind

    def prune_before(self, x_ns: int) -> None:
        """Drop segments that end before ``x_ns``, keeping the one that contains it."""
        idx = bisect_right(self._x0s, x_ns) - 1
        if idx <= 0:
            return
        del self._x0s[:idx]
        del self._segments[:idx]

    def __len__(self) -> int:
        return len(self._segments)

    def _containing(self, x_ns: int) -> Segment | None:
        idx = bisect_right(self._x0s, x_ns) - 1
        if idx < 0:
            return None
        return self._segments[idx]
