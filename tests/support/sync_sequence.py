"""A phone recording of a whole sequence: lead-in, each member alone, gaps; one clock."""

from __future__ import annotations

import random
import struct

from sendspin_sync import sequence as seq
from tests.support.sync_synth import synth


def sequence_slots(members: list[str], point: str = "device", *, latency_s: float = 0.25) -> list[seq.Slot]:
    """The slots as the bridge would time them (each solo heard ``latency_s`` after it was sent)."""
    solo = seq.solo_seconds(point)
    out, t = [], seq.LEAD_S
    for m in members:
        out.append(seq.Slot(member=m, start_s=t + latency_s, end_s=t + solo))
        t += solo + seq.gap_seconds(point)
    return out


def sequence_recording(
    lags: dict[str, float],
    *,
    point: str = "device",
    started_at_s: float = 1.7,
    tail_s: float = 2.0,
    sample_rate: int = 16000,
    noise: float = 0.01,
    silent: frozenset[str] = frozenset(),
    capture_start_s: float = 20.0,
) -> tuple[bytes, list[seq.Slot]]:
    """int16 mono PCM of a recording that began ``started_at_s`` before the sequence did; member m
    plays ``lags[m]`` late. Returns the PCM and the slots the bridge timed."""
    slots = sequence_slots(list(lags), point)
    duration = started_at_s + slots[-1].end_s + tail_s
    n = round(duration * sample_rate)
    rnd = random.Random(5)
    x = [rnd.gauss(0.0, noise) for _ in range(n)]
    for s in slots:
        if s.member in silent:
            continue
        rec = synth(
            s.member,
            lag_s=lags[s.member],
            start_time_s=capture_start_s,
            duration_s=duration,
            noise=0.0,
            sample_rate=sample_rate,
        )
        y = struct.unpack(f"<{n}h", rec.pcm[: 2 * n])
        lo, hi = round((started_at_s + s.start_s) * sample_rate), round((started_at_s + s.end_s) * sample_rate)
        for i in range(max(0, lo), min(n, hi)):
            x[i] += y[i] / 32767
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, v * 32767))) for v in x))
    return pcm, slots
