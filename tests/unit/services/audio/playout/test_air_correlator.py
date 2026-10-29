"""Air sensor correlator: NCC, PSR gate, sub-frame refinement (Task A8)."""

from __future__ import annotations

import math
import random
import struct
import time

import pytest

from sendspin_bridge.services.audio.playout.air_correlator import (
    PSR_GATE,
    RHO_MIN,
    SIDE_MAX,
    bandpass_peak_log2,
    correlate_window,
    ncc_scan,
    refine,
    side_lobe,
)
from sendspin_bridge.services.audio.playout.air_tap import comb_lag, comb_peak_log2


def music(n, seed=1):
    rnd = random.Random(seed)
    v, out = 6.0, []
    for _ in range(n):
        v = max(0.0, min(14.0, v + rnd.gauss(0, 0.6) + (3.0 if rnd.random() < 0.04 else 0.0) - 0.05 * (v - 6)))
        out.append(v)
    return out


def make(lag_true=37.0, n=1034, lo=-86, hi=86, noise=0.3, seed=1):
    w = music(n + hi - lo + 4, seed)
    rnd = random.Random(seed + 100)

    def at(x):  # linear interpolation for fractional lags
        i = int(math.floor(x))
        f = x - i
        return w[i] * (1 - f) + w[i + 1] * f

    air = [round(8 * (at(j + lag_true - lo) + rnd.gauss(0, noise))) for j in range(n)]
    return [float(a) for a in air], w[: n + hi - lo], b"\x01" * (n + hi - lo)


def test_refine_is_exact_on_a_parabola():
    def f(x):
        return 1 - (x - 0.3) ** 2

    assert refine(f(-1), f(0), f(1)) == pytest.approx(0.3)
    assert refine(0.0, 1.0, 5.0) == 0.5  # clamped


def test_refine_is_zero_on_a_flat_top():
    assert refine(1.0, 1.0, 1.0) == 0.0


def test_the_peaks_own_shoulder_is_not_a_side_lobe():
    rho = [0.1, 0.5, 0.8, 0.9, 1.0, 0.9, 0.8, 0.5, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert side_lobe(rho, 4) == 0.0


def test_the_main_lobe_is_capped():
    rho = [1.0 - 0.01 * abs(i - 20) for i in range(41)]  # monotone over the whole range
    assert side_lobe(rho, 20) == pytest.approx(1.0 - 0.01 * (SIDE_MAX + 1))


def test_no_lag_outside_the_main_lobe_has_no_side_lobe():
    assert side_lobe([0.5, 1.0, 0.5], 1) is None


def test_a_secondary_peak_beyond_the_main_lobe_is_the_side_lobe():
    rho = [0.2, 0.6, 0.3, 0.5, 1.0, 0.5, 0.3, 0.7, 0.2]
    assert side_lobe(rho, 4) == 0.7


def test_the_main_lobe_is_at_least_two_cells_wide():
    rho = [0.0, 0.0, 0.9, 0.95, 1.0, 0.95, 0.9, 0.0, 0.0, 0.3]
    rho[3], rho[5] = 0.2, 0.2  # a dip right next to the peak, then a rise inside ±2
    assert side_lobe(rho, 4) == 0.3


def test_finds_an_integer_lag():
    air, w, prog = make(lag_true=37.0)
    r = correlate_window(air, w, prog, -86, 86)
    assert r.status == "confirmed"
    assert r.lag == pytest.approx(37.0, abs=0.15)
    assert r.psr >= PSR_GATE
    assert r.rho >= RHO_MIN


def test_finds_a_fractional_lag_within_a_fifth_of_a_cell():
    air, w, prog = make(lag_true=-12.4)
    assert correlate_window(air, w, prog, -86, 86).lag == pytest.approx(-12.4, abs=0.2)


def test_unrelated_envelopes_are_rejected():
    air, _, _ = make(seed=1)
    _, w, prog = make(seed=7)
    r = correlate_window(air, w, prog, -86, 86)
    assert r.status == "rejected"
    assert r.lag is None


def test_flat_windows_are_flat_not_rejected():
    _, w, prog = make()
    assert correlate_window([40.0] * 1034, w, prog, -86, 86).status == "flat"


def test_a_flat_written_side_is_flat():
    air, w, prog = make()
    assert correlate_window(air, [5.0] * len(w), prog, -86, 86).status == "flat"


def test_missing_and_silence_make_a_window_invalid():
    air, w, prog = make()
    holes = [None if i % 8 == 0 else a for i, a in enumerate(air)]  # 12.5 % missing
    assert correlate_window(holes, w, prog, -86, 86).status == "invalid"
    assert correlate_window(air, w, b"\x00" * len(prog), -86, 86).status == "invalid"


def test_a_few_missing_cells_still_confirm():
    air, w, prog = make(lag_true=21.0)
    holes = [None if i % 20 == 0 else a for i, a in enumerate(air)]  # 5 % missing
    r = correlate_window(holes, w, prog, -86, 86)
    assert r.status == "confirmed"
    assert r.lag == pytest.approx(21.0, abs=0.15)


def test_program_share_is_judged_on_the_zero_lag_span():
    air, w, prog = make()
    n, lo = len(air), -86
    # silence only outside the zero-lag-centred span: still valid
    outside = bytearray(prog)
    outside[:-lo] = b"\x00" * (-lo)
    outside[-lo + n :] = b"\x00" * (len(prog) - (-lo + n))
    assert correlate_window(air, w, bytes(outside), lo, 86).status == "confirmed"
    # 25 % silence inside that span: invalid
    inside = bytearray(prog)
    inside[-lo : -lo + n // 4] = b"\x00" * (n // 4)
    assert correlate_window(air, w, bytes(inside), lo, 86).status == "invalid"


def test_missing_air_cells_are_excluded_from_the_statistics():
    rnd = random.Random(3)
    w = [rnd.uniform(0, 12) for _ in range(300)]
    air: list[float | None] = [3.0 * w[j + 10 + 5] + 7.0 for j in range(280)]  # lag 10 with lo = -5
    for j in range(0, 280, 9):
        air[j] = None
    rho = ncc_scan(air, w, -5, 15)
    assert len(rho) == 21
    assert rho[10 + 5] == pytest.approx(1.0, abs=1e-9)
    assert max(rho[:15] + rho[16:]) < 0.5


def test_scan_rejects_a_written_span_of_the_wrong_length():
    with pytest.raises(ValueError):
        ncc_scan([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], -1, 1)


def test_a_peak_on_the_range_edge_is_not_trusted():
    air, w, prog = make(lag_true=86.0)  # true lag = the last candidate
    assert correlate_window(air, w, prog, -86, 86).status in ("edge", "rejected")


def test_periodic_content_does_not_confirm_a_neighbour_period():
    base = [6 + 4 * math.sin(2 * math.pi * i / 60) for i in range(1300)]  # 174 ms loop, very periodic
    air = [float(round(8 * base[j + 100])) for j in range(1034)]
    r = correlate_window(air, base[14 : 14 + 1034 + 172], b"\x01" * 1206, -86, 86)
    assert r.status != "confirmed" or abs(r.lag - 0.0) < 0.5  # true lag is 0 in this frame; never 60 away


def test_finds_a_lag_near_the_edge_of_the_500ms_acquisition_range():
    air, w, prog = make(lag_true=150.0, lo=-172, hi=172)
    assert correlate_window(air, w, prog, -172, 172).lag == pytest.approx(150.0, abs=0.2)


def test_scan_cost_is_bounded():
    air, w, _prog = make(lo=-172, hi=172)
    t0 = time.perf_counter()
    ncc_scan(air, w, -172, 172)
    dt = time.perf_counter() - t0
    assert dt < 0.5  # ±500 ms acquisition scan, generous for CI


def _tone(freq, rate=44100, frames=4096, amp=8000.0, channels=2):
    vals = []
    for n in range(frames):
        v = int(round(amp * math.sin(2 * math.pi * freq * n / rate)))
        vals.extend([v] * channels)
    return memoryview(struct.pack(f"<{len(vals)}h", *vals)).cast("h")


def test_bandpass_and_comb_both_rank_5khz_above_200hz_by_more_than_24db():
    rate, frames = 44100, 4096
    bits_24db = 24 / (20 * math.log10(2))  # 24 dB in log2 amplitude units
    hi, lo = _tone(5000, rate, frames), _tone(200, rate, frames)
    start = frames - 1024  # past the filters' settling
    bp = bandpass_peak_log2(hi, 2, rate, start, frames) - bandpass_peak_log2(lo, 2, rate, start, frames)
    lag = comb_lag(rate)
    prev_hi = tuple(hi[(start - lag) * 2 : start * 2])
    prev_lo = tuple(lo[(start - lag) * 2 : start * 2])
    comb = comb_peak_log2(hi, 2, start, frames, prev_hi, lag) - comb_peak_log2(lo, 2, start, frames, prev_lo, lag)
    assert bp > bits_24db
    assert comb > bits_24db


def _tracking(air, w, prog, lo_full, centre, half, margin):
    lo, hi = centre - half, centre + half
    start = lo - margin - lo_full
    length = len(air) + hi - lo + 2 * margin
    return correlate_window(air, w[start : start + length], prog[start : start + length], lo, hi, margin=margin)


@pytest.mark.parametrize("half", [20, 5])
def test_tracking_windows_measure_the_same_psr_as_acquisition(half):
    air, w, prog = make(lag_true=37.0, lo=-172, hi=172)
    acq = correlate_window(air, w, prog, -172, 172)
    trk = _tracking(air, w, prog, -172, 37, half, margin=SIDE_MAX + 24)
    assert acq.status == trk.status == "confirmed"
    assert math.isfinite(trk.psr)
    assert trk.psr == pytest.approx(acq.psr, rel=0.05)
    assert trk.lag == pytest.approx(acq.lag, abs=1e-9)


def test_psr_does_not_depend_on_the_acquisition_range():
    air, w, prog = make(lag_true=37.0, lo=-172, hi=172)
    wide = correlate_window(air, w, prog, -172, 172)
    narrow = correlate_window(air, w[86 : 86 + len(air) + 172], prog[86 : 86 + len(air) + 172], -86, 86)
    assert narrow.psr == pytest.approx(wide.psr, rel=1e-9)


def test_a_window_too_narrow_for_the_side_lobe_scan_is_not_confirmed():
    air, w, prog = make(lag_true=37.0, lo=-172, hi=172)
    r = _tracking(air, w, prog, -172, 37, 5, margin=0)
    assert r.status == "no_side_lobe"
    assert r.psr is None
    assert r.lag is None


def test_non_finite_input_makes_a_window_invalid():
    air, w, prog = make()
    air[10] = float("nan")
    assert correlate_window(air, w, prog, -86, 86).status == "invalid"
    air, w, prog = make()
    w[5] = float("inf")
    assert correlate_window(air, w, prog, -86, 86).status == "invalid"


@pytest.mark.parametrize("cut", ["written", "program"])
def test_written_and_program_must_match_the_lag_range_exactly(cut):
    air, w, prog = make()
    if cut == "written":
        w = w[:-1]
    else:
        prog = prog[:-1]
    with pytest.raises(ValueError):
        correlate_window(air, w, prog, -86, 86)
    air, w, prog = make()
    with pytest.raises(ValueError):
        correlate_window(air, w, prog, -86, 86, margin=1)  # margin cells missing


def test_a_negative_margin_is_rejected():
    air, w, prog = make()
    with pytest.raises(ValueError):
        correlate_window(air, w[1:-1], prog[1:-1], -86, 86, margin=-1)
