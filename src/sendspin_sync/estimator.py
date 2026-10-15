"""Find the click track in a phone recording and return its phase in the 10 s cycle."""

from __future__ import annotations

import math
import struct

from sendspin_sync import click_track as ct
from sendspin_sync.models import Measurement, Recording

MIN_CLICKS = 2
MAX_SPREAD_MS = 3.0
MIN_SNR_DB = 10.0
MIN_SAMPLE_RATE = 12000
MAX_SAMPLE_RATE = 48000
MIN_DURATION_S = 2.0
MAX_DURATION_S = 20.0
_CANDIDATE_RATIO = 6.0
_FINE_HALF_MS = 15.0
_GAP_TOL_MS = 5.0
_DEDUP_MS = 20.0  # half the shortest chirp gap (40 ms), so both chirps of a k=0 pair survive


def pcm_to_floats(pcm: bytes) -> list[float]:
    return [v / 32768.0 for v in struct.unpack(f"<{len(pcm) // 2}h", pcm)]


def band_envelope(x: list[float], sample_rate: int, frame_ms: float = 1.0) -> list[float]:
    """Energy of the 2–6 kHz band per frame: first difference removes bass, frames sum squares."""
    frame = max(1, round(sample_rate * frame_ms / 1000))
    hp = [x[i] - x[i - 1] for i in range(1, len(x))]
    return [sum(v * v for v in hp[i : i + frame]) for i in range(0, len(hp), frame)]


def matched_peak(x: list[float], template: list[float], centre: int, half_window: int) -> tuple[int, float]:
    lo, hi = max(0, centre - half_window), min(len(x) - len(template), centre + half_window)
    best_i, best = lo, -1.0
    for i in range(lo, hi + 1):
        acc = 0.0
        seg = x[i : i + len(template)]
        for a, b in zip(seg, template, strict=False):
            acc += a * b
        if abs(acc) > best:
            best, best_i = abs(acc), i
    return best_i, best


def circular_mean_ms(phases_ms: list[float], cycle_ms: float) -> tuple[float, float]:
    s = sum(math.sin(2 * math.pi * p / cycle_ms) for p in phases_ms)
    c = sum(math.cos(2 * math.pi * p / cycle_ms) for p in phases_ms)
    mean = (math.atan2(s, c) / (2 * math.pi) * cycle_ms) % cycle_ms
    devs = [((p - mean + cycle_ms / 2) % cycle_ms) - cycle_ms / 2 for p in phases_ms]
    return mean, math.sqrt(sum(d * d for d in devs) / len(devs))


def _invalid(member: str) -> Measurement:
    return Measurement(member=member, status="not_heard", reason="invalid_recording")


def estimate(recording: Recording, *, epoch_s: float = 0.0) -> Measurement:
    sr, member = recording.sample_rate, recording.member
    if not MIN_SAMPLE_RATE <= sr <= MAX_SAMPLE_RATE or len(recording.pcm) % 2:
        return _invalid(member)
    n = len(recording.pcm) // 2
    if not MIN_DURATION_S * sr <= n <= MAX_DURATION_S * sr:
        return _invalid(member)
    x = pcm_to_floats(recording.pcm)
    env = band_envelope(x, sr)
    floor = sorted(env)[len(env) // 2] or 1e-12
    frame = max(1, round(sr / 1000))
    candidates = [i * frame for i, e in enumerate(env) if e > _CANDIDATE_RATIO * floor]
    if not candidates:
        return Measurement(member=member, status="not_heard", reason="no_clicks")
    template = ct.chirp(sr)
    half, dedup = round(sr * _FINE_HALF_MS / 1000), round(sr * _DEDUP_MS / 1000)
    peaks: list[tuple[int, float]] = []
    for c in candidates:
        if peaks and c - peaks[-1][0] < dedup:
            continue
        i, p = matched_peak(x, template, c, half)
        if not peaks or i - peaks[-1][0] > len(template):
            peaks.append((i, p))
    noise = math.sqrt(floor / frame) * math.sqrt(sum(v * v for v in template)) or 1e-9
    snr_db = 20 * math.log10(max(p for _, p in peaks) / noise) if peaks else 0.0
    tol = round(sr * _GAP_TOL_MS / 1000)
    cycle_ms = ct.CYCLE_S * 1000.0
    phases: list[float] = []
    used: set[int] = set()
    for idx, (i, _p) in enumerate(peaks):
        if idx in used:
            continue
        best_k: int | None = None
        best_j: int | None = None
        best_p = 0.0
        for k in range(ct.CYCLE_CLICKS):
            target = i + round(sr * ct.gap_ms(k) / 1000)
            for j, (i2, p2) in enumerate(peaks):
                if j != idx and j not in used and abs(i2 - target) <= tol and p2 > best_p:
                    best_k, best_p, best_j = k, p2, j
        if best_k is None or best_j is None:
            continue
        used.update({idx, best_j})
        t = recording.start_time_s + i / sr
        phases.append(((t - ct.LEAD_IN_S - best_k * ct.PERIOD_S - epoch_s) * 1000.0) % cycle_ms)
    if len(phases) < MIN_CLICKS:
        return Measurement(member=member, status="not_heard", reason="too_few_clicks", clicks=len(phases))
    mean, spread = circular_mean_ms(phases, cycle_ms)
    confidence = max(0.0, min(1.0, (snr_db - MIN_SNR_DB) / 30.0)) * (1.0 if spread <= 1.0 else 0.7)
    if snr_db < MIN_SNR_DB:
        return Measurement(member=member, status="not_heard", reason="low_snr", clicks=len(phases), confidence=0.0)
    if spread > MAX_SPREAD_MS:
        return Measurement(
            member=member,
            status="unstable",
            reason="spread",
            phase_ms=round(mean, 2),
            clicks=len(phases),
            spread_ms=round(spread, 2),
            confidence=round(confidence, 3),
        )
    return Measurement(
        member=member,
        status="ok",
        phase_ms=round(mean, 2),
        clicks=len(phases),
        spread_ms=round(spread, 2),
        confidence=round(confidence, 3),
    )
