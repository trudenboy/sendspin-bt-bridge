"""Schedule map from output stream time (ns) to the scheduled RAW-clock time (µs)."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sendspin_bridge.services.audio.playout.sensor import PortSample

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


@dataclass(frozen=True, slots=True)
class PortReading:
    t_us: float
    x_port_ns: int
    fill_ns: int
    underrun: bool  # a new underrun episode began at this reading
    stalled: bool
    recovered: bool  # first reading after a stall or an underrun


_STALL_PROGRESS_NS = 100_000  # the audio clock must advance more than 0.1 ms to count as progress
# A delivery is checked against the port (one extra clock read) only when the ring is
# predicted to hold less than this: in steady play it holds the whole sink buffer.
DELIVERY_CHECK_FILL_NS = 20_000_000


class PortObserver:
    """Turns audio-clock readings into port position, buffer fill, underruns and stalls.

    ``fill = delivered + k - a``. A negative fill is an underrun: the port ran past
    our data, so the origin ``k`` moves by the deficit and the port position is
    ``delivered``. An episode lasts until the first reading with ``fill >= 0``.
    A stall is an audio clock that advanced at most 0.1 ms over ``stall_ms``.

    The port keeps playing silence after the last reading of an underrun until our data
    arrives, and the data lands where the port is then. Readings alone miss that part (up
    to one sense interval), and an underrun that begins and ends between two readings
    entirely. ``on_delivery`` closes the gap: at the first delivery into a (predicted)
    near-empty ring the port is read, and when it ran past our data ``k`` takes the rest of
    the deficit then; an underrun seen only this way is reported at the next reading.
    """

    def __init__(self, *, k0_ns: int = 0, stall_ms: float = 300.0) -> None:
        self._k_ns = k0_ns
        self._stall_us = stall_ms * 1000.0
        self._in_underrun = False
        self._recover_pending = False
        self._progress_t_us: float | None = None
        self._progress_a_ns = 0
        self._last: PortSample | None = None  # the last reading
        self._delivery_underrun = False  # an underrun found at a delivery, not yet reported

    @property
    def k_ns(self) -> int:
        return self._k_ns

    def delivery_check_due(self, now_us: float, delivered_ns: int) -> bool:
        """The data after ``delivered_ns`` may land in an empty ring now (checked by ``on_delivery``)."""
        last = self._last
        if last is None:
            return False  # before the first reading the start placement aligns the stream
        if self._in_underrun:
            return True
        a_est = last.a_ns + (now_us - last.t_us) * 1000.0
        return delivered_ns + self._k_ns - a_est < DELIVERY_CHECK_FILL_NS

    def on_delivery(self, sample: PortSample, delivered_ns: int) -> bool:
        """Data from ``delivered_ns`` on just reached the sink, read as ``sample``.

        If the port ran past ``delivered_ns`` the data lands where the port is: ``k`` moves by
        the deficit not yet seen. Returns whether it moved.
        """
        excess = sample.a_ns - (delivered_ns + self._k_ns)
        if excess <= 0:
            return False
        self._k_ns += excess
        if not self._in_underrun:
            self._delivery_underrun = True
        return True

    def observe(self, sample: PortSample, delivered_ns: int) -> PortReading:
        self._last = sample
        a = sample.a_ns
        if self._progress_t_us is None or a - self._progress_a_ns > _STALL_PROGRESS_NS:
            self._progress_t_us = sample.t_us
            self._progress_a_ns = a
            stalled = False
        else:
            stalled = sample.t_us - self._progress_t_us >= self._stall_us

        fill = delivered_ns + self._k_ns - a
        underrun = False
        if fill < 0:
            self._k_ns += -fill
            fill = 0
            x_port = delivered_ns
            underrun = not self._in_underrun
            self._in_underrun = True
            self._delivery_underrun = False
        elif self._delivery_underrun:
            # The ring ran dry and refilled since the last reading: this reading is the
            # underrun (k already holds the deficit), the next one with fill >= 0 recovers.
            self._delivery_underrun = False
            x_port = a - self._k_ns
            underrun = True
            self._in_underrun = True
        else:
            x_port = a - self._k_ns
            self._in_underrun = False

        if stalled or self._in_underrun:
            self._recover_pending = True
            recovered = False
        else:
            recovered = self._recover_pending
            self._recover_pending = False
        return PortReading(sample.t_us, x_port, fill, underrun, stalled, recovered)
