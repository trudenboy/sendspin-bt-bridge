"""Sensor-side types of the playout engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PortSample:
    """One bracketed audio-clock reading of the sink."""

    t_us: float  # RAW midpoint of the bracket
    a_ns: int  # audio clock internal time
    mono_us: float
    bracket_us: float
