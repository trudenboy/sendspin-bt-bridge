"""Learned air-sensor reference (pure, stdlib only).

A learned target is built from the per-start samples ``X_i`` (the placement of
the speaker's air time relative to the written stream, in ms) and the output
delay ``d_i`` each was taken at. ``off_i = X_i - d_i`` does not depend on the
delay of its start, so the robust mean of the offsets is the reference.
"""

from __future__ import annotations

from statistics import mean, median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

LEARN_BAND_MS = 15.0
LEARN_OFFSET_MAX_MS = 1000.0


def learned_reference(samples_ms: Sequence[float], d_out_ms: Sequence[float]) -> tuple[float, float]:
    """Return ``(latency_ms, d_ref_ms)`` from learned samples and their delays."""
    if not samples_ms or len(samples_ms) != len(d_out_ms):
        raise ValueError("samples and delays must be non-empty and of equal length")
    offs = [float(x) - float(d) for x, d in zip(samples_ms, d_out_ms, strict=True)]
    if any(abs(o) > LEARN_OFFSET_MAX_MS for o in offs):
        raise ValueError("learned offset out of range")
    centre = median(offs)
    off_ref = mean(o for o in offs if abs(o - centre) <= LEARN_BAND_MS)
    d_ref = float(median(d_out_ms))
    return d_ref + off_ref, d_ref
