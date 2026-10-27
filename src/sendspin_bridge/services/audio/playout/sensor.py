"""Sensor-side types of the playout engine."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

# Same value as ``Gst.CLOCK_TIME_NONE``; defined here so the module imports without GStreamer.
CLOCK_TIME_NONE = 2**64 - 1


def _raw_ns() -> int:
    clock_id = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)
    return time.clock_gettime_ns(clock_id)


@dataclass(frozen=True, slots=True)
class PortSample:
    """One bracketed audio-clock reading of the sink."""

    t_us: float  # RAW midpoint of the bracket
    a_ns: int  # audio clock internal time
    mono_us: float
    bracket_us: float


class PortClock(Protocol):
    def read(self) -> PortSample | None: ...


class GstPortClock:
    """Reads the sink's audio clock bracketed between two RAW clock reads."""

    MAX_BRACKET_US = 500.0
    TRIES = 3

    def __init__(
        self,
        sink: object,
        *,
        raw_ns: Callable[[], int] = _raw_ns,
        mono_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._sink = sink
        self._raw_ns = raw_ns
        self._mono_ns = mono_ns
        self._rejected = 0

    @property
    def rejected(self) -> int:
        return self._rejected

    def read(self) -> PortSample | None:
        """None while the sink provides no clock or every try exceeded the bracket."""
        clock = self._sink.provide_clock()  # type: ignore[attr-defined]
        if clock is None:
            return None
        for _ in range(self.TRIES):
            t0 = self._raw_ns()
            a_ns = int(clock.get_internal_time())
            t1 = self._raw_ns()
            if a_ns == CLOCK_TIME_NONE:
                return None
            bracket_us = (t1 - t0) / 1000.0
            if bracket_us > self.MAX_BRACKET_US:
                self._rejected += 1
                continue
            return PortSample(
                t_us=(t0 + t1) / 2000.0,
                a_ns=a_ns,
                mono_us=self._mono_ns() / 1000.0,
                bracket_us=bracket_us,
            )
        return None
