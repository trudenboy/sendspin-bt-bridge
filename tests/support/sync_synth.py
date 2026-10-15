"""Recordings a phone would make of the click track, with controllable impairments."""

from __future__ import annotations

import math
import random
import struct
from typing import TYPE_CHECKING

from sendspin_sync import click_track as ct
from sendspin_sync.models import Recording

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet


def synth(
    member: str,
    *,
    lag_s: float,
    start_time_s: float = 20.0,
    duration_s: float = 6.0,
    sample_rate: int = 16000,
    noise: float = 0.01,
    echo: tuple[float, float] | None = None,
    gain: float = 0.3,
    drop: AbstractSet[int] = frozenset(),
    agc: bool = False,
    seed: int = 1,
) -> Recording:
    """A recording that starts at capture time ``start_time_s`` of a track whose epoch is 0 and which this
    speaker plays ``lag_s`` late (positive = later). ``echo`` is (delay_s, amplitude)."""
    rnd = random.Random(seed)
    n = round(duration_s * sample_rate)
    x = [rnd.gauss(0.0, noise) for _ in range(n)]
    template = ct.chirp(sample_rate)
    taps: list[tuple[float, float]] = [(0.0, gain)]
    if echo:
        taps.append(echo)
    for idx, (t, k) in enumerate(ct.click_times(ct.DURATION_S)):
        if idx in drop:
            continue
        for chirp_t in (t, t + ct.gap_ms(k) / 1000):
            at = chirp_t + lag_s - start_time_s
            for d, a in taps:
                start = round((at + d) * sample_rate)
                if -len(template) < start < n:
                    for i, v in enumerate(template):
                        if 0 <= start + i < n:
                            x[start + i] += a * v
    if agc:  # crude: boost quiet parts, squash loud ones
        x = [math.tanh(3 * v) for v in x]
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, v * 32767))) for v in x))
    return Recording(member=member, start_time_s=start_time_s, sample_rate=sample_rate, pcm=pcm)
