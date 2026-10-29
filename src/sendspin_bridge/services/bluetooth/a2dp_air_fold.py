"""Click-track fold of an SBC energy profile: where the sync clicks sit on the 10 s cycle.

Library form of the helpers the air-phase script uses, so the bridge can fold the air side and
the written side alike. ``rows`` are ``(time, energy)`` per frame, the time being that of the
frame's first sample; the phase follows ``sendspin_sync.estimator`` (click 0 at ``LEAD_IN_S``,
chirp *start*), after subtracting the encoder delay the caller passes (generic: the written side
passes its own delay).
"""

from __future__ import annotations

import math
import statistics
from typing import TYPE_CHECKING

from sendspin_bridge.services.bluetooth.a2dp_air_envelope import sbc_band_totals
from sendspin_sync import click_track as ct

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sendspin_bridge.services.bluetooth.a2dp_air import SbcFrame, SbcFrames, SbcLayout


def chirp_subbands(frame: SbcFrame | SbcLayout) -> range:
    """Sub-bands overlapping the chirp's 2–6 kHz sweep (a band starting at 6 kHz or above is out).

    The tool expects the click track alone on the stream: the energy proxy cannot tell a chirp
    from music in the same sub-bands, and with 4 sub-bands band 0 (which also carries bass) is
    part of the proxy. The lowest sub-band is left out when the others still cover at least half
    of the sweep (8 sub-bands at 44.1 kHz: bands 1–2 cover 2.76–6 kHz; at 48 kHz band 1 alone
    covers 3–6 kHz). Leaving it out drops the sweep's first part and moves the energy centre later
    (about +0.4 ms with 8 sub-bands at 44.1 kHz); the other approximations (Hann edges under a
    peak-amplitude proxy, the half-maximum centre) pull the other way, and on libsbc-encoded click
    tracks the net error stays within ±0.35 ms with a mean near 0
    (``tests/fixtures/a2dp_air/libsbc``). With 4 sub-bands at 44.1 kHz band 1 (5.5–11 kHz) covers
    only the sweep's end, so band 0 is kept; leaving it out would bias the phase by about +1.1 ms.
    """
    width = frame.sampling_rate / 2 / frame.subbands
    lo = int(ct.CHIRP_F0 // width)
    hi = min(frame.subbands - 1, math.ceil(ct.CHIRP_F1 / width) - 1)
    if lo == 0 and hi >= 1:
        covered_above = min(ct.CHIRP_F1, (hi + 1) * width) - width
        if covered_above >= (ct.CHIRP_F1 - ct.CHIRP_F0) / 2:
            lo = 1
    return range(lo, hi + 1)


def frame_energy(frame: SbcFrame) -> float:
    """Energy proxy: sum of 2**scale_factor over channels in the chirp sub-bands."""
    return float(sum(2 ** row[sb] for row in frame.scale_factors for sb in chirp_subbands(frame)))


def _chirp_bands(layout: SbcLayout) -> tuple[int, ...]:
    return tuple(chirp_subbands(layout))


def sbc_chirp_energies(frames: SbcFrames) -> list[float]:
    """:func:`frame_energy` of every undecoded frame, read in place from the scale factors (no decode)."""
    return [float(total) for total in sbc_band_totals(frames, _chirp_bands)]


def unwrap_rtp(timestamps: Sequence[int]) -> list[int]:
    out: list[int] = []
    base = 0
    for i, ts in enumerate(timestamps):
        if i:
            step = (ts - timestamps[i - 1]) & 0xFFFFFFFF
            base += step - 0x100000000 if step >= 0x80000000 else step
        else:
            base = ts
        out.append(base)
    return out


def sbc_encoder_delay_samples(subbands: int) -> float:
    """Samples by which a PCM sample's energy lags it on the frames' media timeline.

    The SBC analysis filter (A2DP v1.3 §12.5) runs a 10·M-tap window, symmetric about its middle,
    over the newest 10·M input samples for each block of M new samples, so an input sample peaks
    in the block whose newest sample is (10M−1)/2 later. That block's own samples sit on the RTP
    timeline (the frame's timestamp plus j·M) centred (M−1)/2 before its newest sample, so the
    energy lags the PCM by (10M−1)/2 − (M−1)/2 = 4.5·M samples: 36 for 8 sub-bands (0.82 ms at
    44.1 kHz), 18 for 4. Measured with libsbc 1.3.1: an impulse shows up in frame k for input
    samples 128k − 72 … 128k + 126, centred 36.5 samples before the frame's centre.
    For scale: the whole codec (encoder + decoder) delays SBC by 73 ≈ 80 − 8 + 1 samples for
    M = 8; the encoder's share is 32.5–36.5 samples depending on where a block is anchored, and
    this tool anchors frames at their sample centres. Host buffering before the encoder (samples
    are assumed to get RTP timestamps as they enter it) is not modelled.
    """
    return 4.5 * subbands


MIN_COVERED_CHIRPS = 4  # of the 10 chirps in a cycle (5 clicks, a pair each): a position scored on fewer is not trusted
FOUND_PEAK_RATIO = 3.0  # "clicks present": pattern-score peak at least this many times the median score


def _fold_score(rows: Sequence[tuple[float, float]], frame_s: float, epoch_s: float) -> list[float] | None:
    """Pattern score per 1 ms position on the click cycle, ``None`` without any energy above the floor.

    Each frame spreads its energy above the median floor over the milliseconds it covers (the
    offsets of the click pattern are whole ms, so no rounding is needed). The score of a position
    is the mean profile over the pattern chirps that fall on covered bins; a span shorter than the
    cycle covers only part of the chirps for any position, and a plain sum would favour positions
    whose chirps happen to fall inside the span. Positions with fewer than ``MIN_COVERED_CHIRPS``
    covered chirps score 0.
    """
    if not rows or frame_s <= 0:
        return None
    bins = round(ct.CYCLE_S * 1000)
    frame_ms = frame_s * 1000.0
    acc, cnt = [0.0] * bins, [0.0] * bins
    floor = statistics.median(e for _, e in rows)
    for w, e in rows:
        lo = ((w - epoch_s) % ct.CYCLE_S) * 1000.0
        hi = lo + frame_ms
        b = int(lo)
        while b < hi:
            overlap = min(hi, b + 1) - max(lo, b)
            acc[b % bins] += max(0.0, e - floor) * overlap
            cnt[b % bins] += overlap
            b += 1
    prof = [acc[i] / cnt[i] if cnt[i] else 0.0 for i in range(bins)]
    if not any(prof):
        return None
    period_ms = round(ct.PERIOD_S * 1000)
    offs = [k * period_ms + g for k in range(ct.CYCLE_CLICKS) for g in (0, ct.gap_ms(k))]
    # a chirp is covered when its first and last ms were both seen
    seen = [1 if cnt[i] else 0 for i in range(bins)]
    last = ct.CHIRP_MS - 1
    score = []
    for p in range(bins):
        total, n = 0.0, 0
        for o in offs:
            if seen[(p + o) % bins] and seen[(p + o + last) % bins]:
                total += prof[(p + o) % bins]
                n += 1
        score.append(total / n if n >= MIN_COVERED_CHIRPS else 0.0)
    return score


def click_fold_quality(rows: Sequence[tuple[float, float]], frame_s: float, epoch_s: float = 0.0) -> float | None:
    """The pattern score's peak over its median (``inf`` with a zero median), ``None`` without a
    peak: how clearly the clicks stand out (:func:`click_fold_found` needs ``FOUND_PEAK_RATIO``)."""
    score = _fold_score(rows, frame_s, epoch_s)
    if score is None:
        return None
    peak, med = max(score), statistics.median(score)
    if peak <= 0:
        return None
    return math.inf if med <= 0 else peak / med


def click_fold_found(
    rows: Sequence[tuple[float, float]], frame_s: float, epoch_s: float = 0.0, encoder_delay_s: float = 0.0
) -> bool:
    """Whether the click pattern is present in ``rows``: the score peak is at least 3x the median score.

    ``encoder_delay_s`` is accepted for symmetry with :func:`click_phase_ms`; the criterion does not
    depend on it.
    """
    score = _fold_score(rows, frame_s, epoch_s)
    if score is None:
        return False
    peak, med = max(score), statistics.median(score)
    return peak > 0 and (med <= 0 or peak >= FOUND_PEAK_RATIO * med)


def click_phase_ms(
    rows: Sequence[tuple[float, float]], frame_s: float, epoch_s: float = 0.0, encoder_delay_s: float = 0.0
) -> float | None:
    """Fold ``rows`` over the click cycle and return the click-0 phase in ms, ``None`` without clicks.

    The pattern score (see :func:`_fold_score`) peaks over the frames that hold the chirps; the
    centre of its half-maximum peak is the chirps' centre on the sub-band timeline. Subtracting
    half a chirp (``CHIRP_MS / 2``) and the encoder's analysis delay gives the chirp *start* in PCM
    time, the estimator's convention. Works on spans shorter than the cycle (5 s and up).
    """
    score = _fold_score(rows, frame_s, epoch_s)
    if score is None:
        return None
    bins = len(score)
    p0 = max(range(bins), key=score.__getitem__)
    half = 0.5 * (score[p0] + statistics.median(score))
    lo_i = p0
    while score[(lo_i - 1) % bins] >= half and p0 - lo_i < bins // 2:
        lo_i -= 1
    hi_i = p0
    while score[(hi_i + 1) % bins] >= half and hi_i - p0 < bins // 2:
        hi_i += 1
    weights = [(i, score[i % bins] - half) for i in range(lo_i, hi_i + 1)]
    total = sum(wt for _, wt in weights)
    centre = sum(i * wt for i, wt in weights) / total if total > 0 else float(p0)
    start_ms = centre - ct.CHIRP_MS / 2 - encoder_delay_s * 1000.0
    return (start_ms - ct.LEAD_IN_S * 1000.0) % (ct.CYCLE_S * 1000.0)
