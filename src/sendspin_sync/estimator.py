"""Find the click track in a phone recording and return its phase in the 10 s cycle."""

from __future__ import annotations

import math
import operator
import struct
from dataclasses import dataclass

from sendspin_sync import click_track as ct
from sendspin_sync.models import Measurement, Recording

MIN_CLICKS = 2
MAX_SPREAD_MS = 3.0
MIN_SNR_DB = 10.0
MIN_SAMPLE_RATE = 12000
MAX_SAMPLE_RATE = 48000
MIN_DURATION_S = 2.0
MAX_DURATION_S = 20.0
_CANDIDATE_RATIO = 3.0  # band energy over the median floor that opens a candidate event (4.8 dB)
_ABS_FLOOR_LSB = 4.0  # floor never drops below the band energy of ±4 LSB white noise (digital silence, dither)
_REFINE_MS = 3.0  # matched-filter search around an event's centre
_MIN_NCC = 0.5  # normalised correlation an event needs to count as a chirp
_EARLIER_RATIO = 0.7  # an earlier peak this strong is the direct sound, the later one an overlapping reflection
_PAIR_DB = 10.0  # both chirps of a pair are played equally loud; allows a reflection landing on one of them
_GAP_TOL_MS = 5.0
_CLUSTER_S = 0.4  # one click (pair + reflections) fits in this window; clicks are PERIOD_S apart
_EVENTS_PER_PERIOD = 6  # 2 chirps x direct + 2 reflections; more than this is kept-strongest
_BAIL_FACTOR = 4  # event density beyond _BAIL_FACTOR x plausible means the room is too noisy to try


def pcm_to_floats(pcm: bytes) -> list[float]:
    return [v / 32768.0 for v in struct.unpack(f"<{len(pcm) // 2}h", pcm)]


def band_envelope(x: list[float], sample_rate: int, frame_ms: float = 1.0) -> list[float]:
    """Energy of the chirp band per frame.

    The comb difference ``x[i] - x[i-d]`` with ``d = sr / 8000`` has its first lobe centred on 4 kHz and zeros at
    DC and 8 kHz, so it keeps the 2–6 kHz chirp and drops bass and hiss; frames sum squares."""
    frame = max(1, round(sample_rate * frame_ms / 1000))
    d = max(1, round(sample_rate / 8000))
    bp = [x[i] - x[i - d] for i in range(d, len(x))]
    return [sum(v * v for v in bp[i : i + frame]) for i in range(0, len(bp), frame)]


def matched_peak(
    x: list[float], template: list[float], centre: int, half_window: int, step: int = 1
) -> tuple[int, float]:
    """Offset in ``centre ± half_window`` where ``template`` correlates best (by magnitude).

    ``step`` > 1 searches a coarse grid first and then refines around its winner."""
    n = len(template)
    lo, hi = max(0, centre - half_window), min(len(x) - n, centre + half_window)
    best_i, best = lo, -1.0
    for i in range(lo, hi + 1, max(1, step)):
        acc = abs(sum(map(operator.mul, x[i : i + n], template)))
        if acc > best:
            best, best_i = acc, i
    if step > 1:
        return matched_peak(x, template, best_i, step - 1)
    return best_i, best


def circular_mean_ms(phases_ms: list[float], cycle_ms: float) -> tuple[float, float]:
    s = sum(math.sin(2 * math.pi * p / cycle_ms) for p in phases_ms)
    c = sum(math.cos(2 * math.pi * p / cycle_ms) for p in phases_ms)
    mean = (math.atan2(s, c) / (2 * math.pi) * cycle_ms) % cycle_ms
    devs = [((p - mean + cycle_ms / 2) % cycle_ms) - cycle_ms / 2 for p in phases_ms]
    return mean, math.sqrt(sum(d * d for d in devs) / len(devs))


def _invalid(member: str) -> Measurement:
    return Measurement(member=member, status="not_heard", reason="invalid_recording")


@dataclass(frozen=True)
class _Chirp:
    pos: int  # sample offset of the chirp start
    strength: float  # matched-filter magnitude
    env_peak: float  # band-energy peak of the event


def _events(env: list[float], threshold: float) -> list[tuple[int, float]]:
    """Runs of frames above ``threshold``, each as (loudest frame, its energy).

    The loudest frame, not the run's middle, locates the chirp: noise touching a chirp merges into its run."""
    out: list[tuple[int, float]] = []
    peak_i, peak = -1, 0.0
    for i, e in enumerate(env):
        if e > threshold:
            if peak_i < 0 or e > peak:
                peak_i, peak = i, e
        elif peak_i >= 0:
            out.append((peak_i, peak))
            peak_i = -1
    if peak_i >= 0:
        out.append((peak_i, peak))
    return out


def _chirps(x: list[float], sr: int, events: list[tuple[int, float]], frame: int) -> list[_Chirp]:
    template = ct.chirp(sr)
    t_norm = math.sqrt(sum(v * v for v in template))
    n = len(template)
    half, step = round(sr * _REFINE_MS / 1000), max(1, sr // 16000)
    found: list[_Chirp] = []
    for peak_frame, env_peak in events:
        centre = round((peak_frame + 0.5) * frame) - n // 2  # the band peaks mid-chirp (4 kHz)
        pos, strength = matched_peak(x, template, centre, half, step)
        # a reflection overlapping the chirp can out-correlate it; the direct sound is the earliest strong peak
        lo = max(0, pos - n + 1)
        hi = pos - max(1, round(sr * 0.0005))
        if hi > lo:
            prev, prev_strength = matched_peak(x, template, (lo + hi) // 2, (hi - lo) // 2, step)
            if prev < pos and prev_strength >= _EARLIER_RATIO * strength:
                pos, strength = prev, prev_strength
        seg_norm = math.sqrt(sum(v * v for v in x[pos : pos + n]))
        if seg_norm <= 0.0 or strength / (seg_norm * t_norm) < _MIN_NCC:
            continue
        if found and pos - found[-1].pos < n // 2:  # two fragments of the same chirp
            if strength > found[-1].strength:
                found[-1] = _Chirp(pos, strength, max(env_peak, found[-1].env_peak))
            continue
        found.append(_Chirp(pos, strength, env_peak))
    return found


def _clusters(chirps: list[_Chirp], sr: int) -> list[list[_Chirp]]:
    out: list[list[_Chirp]] = []
    for c in chirps:
        if out and c.pos - out[-1][0].pos <= _CLUSTER_S * sr:
            out[-1].append(c)
        else:
            out.append([c])
    return out


def _pair(cluster: list[_Chirp], k: int, sr: int) -> tuple[_Chirp, _Chirp] | None:
    """Earliest chirp with a credible partner ``gap_ms(k)`` later. Earliest, because the direct sound
    arrives before its reflections; credible, because both chirps of a pair are played equally loud."""
    gap, tol = round(sr * ct.gap_ms(k) / 1000), round(sr * _GAP_TOL_MS / 1000)
    ratio = 10 ** (-_PAIR_DB / 20)
    for a in cluster:
        partners = [
            b
            for b in cluster
            if abs(b.pos - a.pos - gap) <= tol and min(a.strength, b.strength) >= ratio * max(a.strength, b.strength)
        ]
        if partners:
            return a, max(partners, key=lambda b: b.strength)
    return None


def _click_sequence(clusters: list[list[_Chirp]], sr: int) -> list[tuple[_Chirp, _Chirp, int]]:
    """Pairs whose indexes advance by one per PERIOD_S — the hypothesis that explains the most clusters.
    A reflection that happens to sit one valid gap after a chirp fits one cluster, never the sequence."""
    if not clusters:
        return []
    t0 = clusters[0][0].pos
    steps = [round((c[0].pos - t0) / (sr * ct.PERIOD_S)) for c in clusters]
    best: list[tuple[_Chirp, _Chirp, int]] = []
    best_score: tuple[int, int, int, float] | None = None
    for k0 in range(ct.CYCLE_CLICKS):
        found: list[tuple[_Chirp, _Chirp, int]] = []
        for cluster, s in zip(clusters, steps, strict=True):
            k = (k0 + s) % ct.CYCLE_CLICKS
            pair = _pair(cluster, k, sr)
            if pair is not None:
                found.append((pair[0], pair[1], k))
        # most clicks; then earliest first chirps and shortest gaps (reflections only ever arrive later, so a
        # hypothesis built from them starts later or pairs with a later partner); then the louder pairs
        score = (
            len(found),
            -sum(a.pos for a, _b, _k in found),
            -sum(b.pos - a.pos for a, b, _k in found),
            sum(a.strength + b.strength for a, b, _k in found),
        )
        if found and (best_score is None or score > best_score):
            best, best_score = found, score
    return best


def estimate(recording: Recording, *, epoch_s: float = 0.0) -> Measurement:
    sr, member = recording.sample_rate, recording.member
    if not MIN_SAMPLE_RATE <= sr <= MAX_SAMPLE_RATE or len(recording.pcm) % 2:
        return _invalid(member)
    if not (math.isfinite(recording.start_time_s) and math.isfinite(epoch_s)):  # a phase of nan poisons planning
        return _invalid(member)
    n = len(recording.pcm) // 2
    if not MIN_DURATION_S * sr <= n <= MAX_DURATION_S * sr:
        return _invalid(member)
    x = pcm_to_floats(recording.pcm)
    frame = max(1, round(sr / 1000))
    env = band_envelope(x, sr)
    abs_floor = 2.0 * (_ABS_FLOOR_LSB / 32768.0) ** 2 * frame
    floor = max(sorted(env)[len(env) // 2], abs_floor)
    events = _events(env, _CANDIDATE_RATIO * floor)
    plausible = _EVENTS_PER_PERIOD * (math.ceil(n / sr / ct.PERIOD_S) + 1)
    if len(events) > _BAIL_FACTOR * plausible:
        return Measurement(member=member, status="not_heard", reason="low_snr")
    if len(events) > plausible:
        events = sorted(sorted(events, key=lambda e: e[1], reverse=True)[:plausible])
    pairs = _click_sequence(_clusters(_chirps(x, sr, events, frame), sr), sr)
    if not pairs:
        return Measurement(member=member, status="not_heard", reason="no_clicks")
    if len(pairs) < MIN_CLICKS:
        return Measurement(member=member, status="not_heard", reason="too_few_clicks", clicks=len(pairs))
    cycle_ms = ct.CYCLE_S * 1000.0
    phases = [
        ((recording.start_time_s + a.pos / sr - ct.LEAD_IN_S - k * ct.PERIOD_S - epoch_s) * 1000.0) % cycle_ms
        for a, _b, k in pairs
    ]
    # SNR: band energy of the weaker chirp of each pair over the room's floor, median over clicks
    snrs = sorted(10 * math.log10(max(min(a.env_peak, b.env_peak), 1e-12) / floor) for a, b, _ in pairs)
    snr_db = snrs[len(snrs) // 2]
    if snr_db < MIN_SNR_DB:
        return Measurement(member=member, status="not_heard", reason="low_snr", clicks=len(pairs))
    mean, spread = circular_mean_ms(phases, cycle_ms)
    confidence = round(max(0.0, min(1.0, (snr_db - MIN_SNR_DB) / 20.0)) * (1.0 if spread <= 1.0 else 0.7), 3)
    if spread > MAX_SPREAD_MS:
        return Measurement(
            member=member,
            status="unstable",
            reason="spread",
            phase_ms=round(mean, 2),
            clicks=len(pairs),
            spread_ms=round(spread, 2),
            confidence=confidence,
        )
    return Measurement(
        member=member,
        status="ok",
        phase_ms=round(mean, 2),
        clicks=len(pairs),
        spread_ms=round(spread, 2),
        confidence=confidence,
    )
