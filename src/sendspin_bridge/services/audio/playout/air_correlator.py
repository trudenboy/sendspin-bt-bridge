"""Air sensor correlator: normalised cross-correlation of the air envelope against the written one.

Pure functions, no I/O and no notion of time: the caller (the daemon's air sensor, and the
offline repo tool) cuts a window of air cells and the written cells that cover it shifted by
every candidate lag, and gets back a lag in cells (or why there is none). The daemon runs this
off its event loop (executor); the offline tool shares the same code, so the PSR the gate is
calibrated on is the PSR that runs.

Per window (spec "Correlation", steps 1, 2, 4–6):

1. **Validity** — at least :data:`MIN_PRESENT` of the air cells present and at least
   :data:`MIN_PROGRAM` of the written cells program over the span the centre lag of the range
   (the prior) aligns with the air; else ``invalid``.
2. **NCC** — air z-scored once over its present cells; per lag the written mean and σ over the
   same (present) cells from prefix sums of ``w`` and ``w²`` (minus the few missing positions);
   ``ρ(ℓ) = Σ â_j·w_{j+ℓ} / (N·σ_w(ℓ))`` (``Σ â = 0`` removes the written mean). Flatness:
   ``σ_a ≥`` :data:`FLAT_AIR` and ``σ_w ≥`` :data:`FLAT_WRITTEN` (lags below it score 0; a
   window with no such lag is ``flat``).
4. **Peak and side lobe** — ``ℓ*`` = argmax over the search range; a peak on its first or
   last lag is ``edge`` (the true lag may lie outside). The side lobe is measured over a fixed
   scan of ``±``:data:`SIDE_SCAN` lags around ``ℓ*`` (lags beyond the search range come from
   the caller's ``margin`` and are computed for the side lobe only), so the PSR does not depend
   on the search range: max ρ outside the main lobe, the peak's monotone descent capped at
   ``±``:data:`SIDE_MAX` but at least ``±``:data:`SIDE_EXCLUDE` (:func:`side_lobe`);
   ``PSR = ρ(ℓ*)/max(ρ_s, 0.01)``. Data too short for the scan → ``no_side_lobe``. Search
   lags beyond the scan (acquisition) also count as side lobe, so a loop's neighbour period
   is seen: a wider range can only lower the PSR, a range inside the scan never changes it.
5. **Gate** — ``confirmed`` when ``PSR ≥`` :data:`PSR_GATE` and ``ρ ≥`` :data:`RHO_MIN`, else
   ``rejected``.
6. **Sub-frame** — parabolic ``λ`` on ``ρ(ℓ*−1), ρ(ℓ*), ρ(ℓ*+1)`` clamped to ±0.5;
   ``lag = ℓ* + λ``.

The gate constants are provisional (R-9) and live here only; Task A17 recalibrates them on
real music.

:func:`bandpass_peak_log2` is a reference written envelope (a biquad band-pass 2.7–8.3 kHz)
for tests and the offline tool, to compare with the comb (R-7); it never runs in the daemon.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import accumulate
from operator import mul
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

# Gate (R-9: provisional until Task A17; keep them here, nowhere else).
PSR_GATE = 1.17
RHO_MIN = 0.25
SIDE_EXCLUDE = 2  # cells excluded around the peak for the side lobe
SIDE_MAX = 8  # the main lobe ends at the first rise or this many cells from the peak
SIDE_SCAN = SIDE_MAX + 24  # the side lobe is always measured over ±this many lags around the peak
FLAT_AIR = 2.0  # energy units (1/8 bit), sigma_a minimum
FLAT_WRITTEN = 0.25  # bits, sigma_w minimum
MIN_PRESENT = 0.90  # share of air cells present
MIN_PROGRAM = 0.80  # share of written cells program over the zero-lag-centred span
SIDE_FLOOR = 0.01  # a side lobe at or below this counts as this

Status = Literal["confirmed", "rejected", "flat", "invalid", "edge", "no_side_lobe"]


@dataclass(frozen=True, slots=True)
class WindowResult:
    status: Status
    lag: float | None  # L = l* + lambda (cells), None unless confirmed
    rho: float | None
    psr: float | None


def refine(rho_m: float, rho_0: float, rho_p: float) -> float:
    """Parabolic sub-frame offset of the peak at 0 from its neighbours, clamped to ±0.5.

    A neighbourhood that is not a maximum (flat or convex) returns ±0.5 towards the larger
    neighbour (0 when they are equal).
    """
    denom = rho_m - 2.0 * rho_0 + rho_p
    if denom >= 0.0:
        if rho_p > rho_m:
            return 0.5
        if rho_m > rho_p:
            return -0.5
        return 0.0
    lam = (rho_m - rho_p) / (2.0 * denom)
    return max(-0.5, min(0.5, lam))


def _scan(
    air: Sequence[float | None], written: Sequence[float], lag_lo: int, lag_hi: int, min_sigma_w: float
) -> tuple[list[float], float, float]:
    """``(ρ per lag, σ_a, max σ_w)``; lags with ``σ_w < min_sigma_w`` score 0."""
    n = len(air)
    n_lags = lag_hi - lag_lo + 1
    if n_lags < 1 or len(written) != n + n_lags - 1:
        raise ValueError(f"written must hold len(air) + lag_hi - lag_lo = {n + n_lags - 1} cells, got {len(written)}")
    present = [a for a in air if a is not None]
    missing = [j for j, a in enumerate(air) if a is None]
    count = len(present)
    if count < 2:
        return [0.0] * n_lags, 0.0, 0.0
    mean_a = math.fsum(present) / count
    sigma_a = math.sqrt(max(0.0, math.fsum((a - mean_a) ** 2 for a in present) / count))
    if sigma_a <= 0.0:
        return [0.0] * n_lags, 0.0, 0.0
    a_hat = [0.0 if a is None else (a - mean_a) / sigma_a for a in air]  # missing → 0: out of the dot

    w_mean = math.fsum(written) / len(written)  # centring keeps the prefix-sum variance exact enough
    w = [x - w_mean for x in written]
    s1 = [0.0, *accumulate(w)]
    s2 = [0.0, *accumulate(x * x for x in w)]
    floor = max(min_sigma_w, 1e-9)
    rho: list[float] = []
    max_sigma_w = 0.0
    for off in range(n_lags):
        sum_w = s1[off + n] - s1[off]
        sum_w2 = s2[off + n] - s2[off]
        for j in missing:
            x = w[j + off]
            sum_w -= x
            sum_w2 -= x * x
        mean_w = sum_w / count
        sigma_w = math.sqrt(max(0.0, sum_w2 / count - mean_w * mean_w))
        max_sigma_w = max(max_sigma_w, sigma_w)
        if sigma_w < floor:
            rho.append(0.0)
            continue
        rho.append(sum(map(mul, a_hat, w[off : off + n])) / (count * sigma_w))
    return rho, sigma_a, max_sigma_w


def ncc_scan(air: Sequence[float | None], written: Sequence[float], lag_lo: int, lag_hi: int) -> list[float]:
    """ρ(ℓ) for ℓ in [lag_lo, lag_hi]: air[j] against written[j + ℓ - lag_lo] (missing air cells excluded)."""
    return _scan(air, written, lag_lo, lag_hi, 0.0)[0]


def side_lobe(rho: Sequence[float], best: int) -> float | None:
    """Highest ρ outside the main lobe of the peak at ``best`` (None when no lag lies outside).

    The main lobe is the peak's monotone descent on each side, ending at the first rise or
    :data:`SIDE_MAX` cells from the peak, and never less than ±:data:`SIDE_EXCLUDE` cells: a
    lag on the peak's own shoulder is not a competing alignment, a neighbour period of a loop is.
    """
    lo = hi = best
    while lo > 0 and best - lo < SIDE_MAX and rho[lo - 1] <= rho[lo]:
        lo -= 1
    while hi < len(rho) - 1 and hi - best < SIDE_MAX and rho[hi + 1] <= rho[hi]:
        hi += 1
    lo = min(lo, best - SIDE_EXCLUDE)
    hi = max(hi, best + SIDE_EXCLUDE)
    return max((r for i, r in enumerate(rho) if i < lo or i > hi), default=None)


def _side_scan(
    air: Sequence[float | None],
    written: Sequence[float],
    rho: list[float],
    lag_lo: int,
    lag_hi: int,
    margin: int,
    peak_lag: int,
) -> tuple[int, list[float]] | None:
    """``(first lag, ρ)`` over the ``2·SIDE_SCAN + 1`` lags around the peak, or None.

    The scan is shifted to fit the available lags; lags inside the search range reuse ``rho``,
    the others are computed for the side lobe only.
    """
    avail_lo, avail_hi = lag_lo - margin, lag_hi + margin
    if avail_hi - avail_lo < 2 * SIDE_SCAN:
        return None
    a = max(avail_lo, min(peak_lag - SIDE_SCAN, avail_hi - 2 * SIDE_SCAN))
    b = a + 2 * SIDE_SCAN
    n = len(air)

    def extra(x: int, y: int) -> list[float]:
        k = x - avail_lo
        return _scan(air, written[k : k + n + y - x], x, y, FLAT_WRITTEN)[0]

    left = extra(a, lag_lo - 1) if a < lag_lo else []
    right = extra(lag_hi + 1, b) if b > lag_hi else []
    return a, left + rho[max(a, lag_lo) - lag_lo : min(b, lag_hi) - lag_lo + 1] + right


def correlate_window(
    air: Sequence[float | None],
    written: Sequence[float],
    program: bytes,
    lag_lo: int,
    lag_hi: int,
    *,
    margin: int = 0,
) -> WindowResult:
    """One window: validity, NCC over the lag range, peak/side-lobe gate, sub-frame lag.

    The caller centres ``[lag_lo, lag_hi]`` on its prior: the program share is judged on the
    written span the midpoint lag aligns with. ``written``/``program`` cover the lags
    ``[lag_lo − margin, lag_hi + margin]`` (``len(air) + lag_hi − lag_lo + 2·margin`` cells);
    the margin lags never hold the peak, they only let the side lobe be measured over the same
    ``±SIDE_SCAN`` lags around it whatever the search range. A window whose lags cannot hold
    that scan is ``no_side_lobe`` (PSR unmeasurable, never confirmed). Non-finite values make
    a window ``invalid``.
    """
    n = len(air)
    if n == 0 or not all(map(math.isfinite, written)) or not all(math.isfinite(a) for a in air if a is not None):
        return WindowResult("invalid", None, None, None)
    present = n - sum(1 for a in air if a is None)
    centre = (lag_lo + lag_hi) // 2 - lag_lo + margin
    span = program[centre : centre + n]
    if present < MIN_PRESENT * n or sum(1 for p in span if p) < MIN_PROGRAM * n:
        return WindowResult("invalid", None, None, None)
    search = written[margin : margin + n + lag_hi - lag_lo]
    rho, sigma_a, max_sigma_w = _scan(air, search, lag_lo, lag_hi, FLAT_WRITTEN)
    if sigma_a < FLAT_AIR or max_sigma_w < FLAT_WRITTEN:
        return WindowResult("flat", None, None, None)
    best = max(range(len(rho)), key=rho.__getitem__)
    peak = rho[best]
    if best == 0 or best == len(rho) - 1:
        return WindowResult("edge", None, peak, None)
    scanned = _side_scan(air, written, rho, lag_lo, lag_hi, margin, lag_lo + best)
    if scanned is None:
        return WindowResult("no_side_lobe", None, peak, None)
    a, scan = scanned
    side = side_lobe(scan, lag_lo + best - a)
    if side is None:
        return WindowResult("no_side_lobe", None, peak, None)
    # A search range wider than the scan also competes (a loop's neighbour period lies there).
    beyond = [r for i, r in enumerate(rho) if not a <= lag_lo + i <= a + 2 * SIDE_SCAN]
    side = max(side, *beyond) if beyond else side
    psr = peak / max(side, SIDE_FLOOR)
    if psr < PSR_GATE or peak < RHO_MIN:
        return WindowResult("rejected", None, peak, psr)
    lag = lag_lo + best + refine(rho[best - 1], peak, rho[best + 1])
    return WindowResult("confirmed", lag, peak, psr)


# -- reference written envelope (tests and the offline tool only) ------------------------------

BANDPASS_LO_HZ = 2700.0
BANDPASS_HI_HZ = 8300.0


def _bandpass_coefficients(rate: int) -> tuple[float, float, float, float, float]:
    """RBJ biquad band-pass (0 dB peak) centred at √(lo·hi), Q = f0 / (hi − lo)."""
    f0 = math.sqrt(BANDPASS_LO_HZ * BANDPASS_HI_HZ)
    q = f0 / (BANDPASS_HI_HZ - BANDPASS_LO_HZ)
    omega = 2.0 * math.pi * f0 / rate
    alpha = math.sin(omega) / (2.0 * q)
    a0 = 1.0 + alpha
    return alpha / a0, 0.0, -alpha / a0, -2.0 * math.cos(omega) / a0, (1.0 - alpha) / a0


def bandpass_peak_log2(s16: Sequence[int], channels: int, rate: int, start: int, stop: int) -> float:
    """``log2(1 + max|y|)`` over frames ``start:stop`` of a biquad band-pass (2.7–8.3 kHz).

    ``s16`` is interleaved 16-bit samples; the filter runs per channel from rest at frame 0, so
    ``start`` should leave it time to settle. Reference only (R-7): pure Python per sample.
    """
    b0, b1, b2, a1, a2 = _bandpass_coefficients(rate)
    peak = 0.0
    for ch in range(channels):
        x1 = x2 = y1 = y2 = 0.0
        for k, x in enumerate(s16[ch : stop * channels : channels]):
            y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, float(x), y1, y
            if k >= start and abs(y) > peak:
                peak = abs(y)
    return math.log2(1.0 + peak)
