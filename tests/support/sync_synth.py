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
    masked: float = 0.0,
) -> Recording:
    """A recording that starts at capture time ``start_time_s`` of a track whose epoch is 0 and which this
    speaker plays ``lag_s`` late (positive = later). ``echo`` is (delay_s, amplitude). ``masked`` rides white
    noise on each chirp (standard deviation ``masked / 2`` of its amplitude), roughening its shape."""
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
                            x[start + i] += a * v + (rnd.gauss(0.0, masked * a * 0.5) if masked else 0.0)
    if agc:  # crude: boost quiet parts, squash loud ones
        x = [math.tanh(3 * v) for v in x]
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, v * 32767))) for v in x))
    return Recording(member=member, start_time_s=start_time_s, sample_rate=sample_rate, pcm=pcm)


def _to_recording(member: str, samples: list[float], sample_rate: int, start_time_s: float) -> Recording:
    n = len(samples)
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, round(v * 32767)))) for v in samples))
    return Recording(member=member, start_time_s=start_time_s, sample_rate=sample_rate, pcm=pcm)


def sparse_lsb_noise(
    member: str, *, duration_s: float = 12.0, sample_rate: int = 16000, density: float = 0.01, seed: int = 3
) -> Recording:
    """Digital silence with isolated ±1 LSB ticks — a dithered or near-silent input."""
    rnd = random.Random(seed)
    lsb = 1 / 32767
    x = [rnd.choice((-lsb, lsb)) if rnd.random() < density else 0.0 for _ in range(round(duration_s * sample_rate))]
    return _to_recording(member, x, sample_rate, 20.0)


def bursty_noise(
    member: str,
    *,
    duration_s: float = 12.0,
    sample_rate: int = 16000,
    rate_hz: float = 5.0,
    level: float = 0.1,
    seed: int = 4,
) -> Recording:
    """Silence broken by short (5–30 ms) bursts of white noise — taps, rustle, clatter."""
    rnd = random.Random(seed)
    n = round(duration_s * sample_rate)
    x = [0.0] * n
    i = 0
    while True:
        i += int(rnd.expovariate(rate_hz) * sample_rate)
        length = int(rnd.uniform(0.005, 0.030) * sample_rate)
        if i + length >= n:
            break
        for j in range(i, i + length):
            x[j] = rnd.gauss(0.0, level)
        i += length
    return _to_recording(member, x, sample_rate, 20.0)


def band_chatter(
    member: str,
    *,
    duration_s: float = 6.0,
    sample_rate: int = 48000,
    rate_hz: float = 25.0,
    level: float = 0.02,
    seed: int = 7,
) -> Recording:
    """Silence broken by very short (2–6 ms) tone blips inside the chirp band — the band-envelope texture of a real
    phone recording (AAC artefacts, desk and room noise): many weak events, each a few dB over the floor."""
    rnd = random.Random(seed)
    n = round(duration_s * sample_rate)
    x = [0.0] * n
    i = 0
    while True:
        i += int(rnd.expovariate(rate_hz) * sample_rate)
        length = int(rnd.uniform(0.002, 0.006) * sample_rate)
        if i + length >= n:
            break
        f, ph = rnd.uniform(2500.0, 5500.0), rnd.uniform(0.0, 2 * math.pi)
        for j in range(length):
            x[i + j] = level * math.sin(2 * math.pi * f * j / sample_rate + ph) * math.sin(math.pi * j / length)
        i += length
    return _to_recording(member, x, sample_rate, 20.0)
