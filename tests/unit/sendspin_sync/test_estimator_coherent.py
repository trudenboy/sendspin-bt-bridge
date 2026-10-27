"""The coherent estimator: every click of the window accumulated on the known schedule (issue #553).

A headset or laptop microphone in an ordinary room hears each 8 ms chirp only a few dB above the room's noise,
too weak to detect one by one; added up over the whole window on the click track's schedule they stand out."""

import functools
import io
import math
import random
import struct
import time

import pytest

from sendspin_sync import click_track as ct
from sendspin_sync import estimator as est
from sendspin_sync.models import Recording

SR = 16000


@functools.cache
def _track(sample_rate: int) -> tuple[float, ...]:
    """The click track's left channel, as floats, long enough for every window below (``write_wav`` writes
    the bytes of ``render_wav`` without per-sample work for the silence)."""
    buf = io.BytesIO()
    ct.write_wav(buf, duration_s=50.0, sample_rate=sample_rate)
    pcm = buf.getvalue()[len(ct.wav_header(duration_s=50.0, sample_rate=sample_rate)) :]
    return tuple(v / 32768.0 for v in struct.unpack(f"<{len(pcm) // 2}h", pcm)[::2])


def _band_noise(n: int, seed: int) -> list[float]:
    """Seeded noise limited to the chirp band (white noise through ``g[i] - g[i-2]``: a lobe on 4 kHz at 16 kHz)."""
    rnd = random.Random(seed)
    g = [rnd.gauss(0.0, 1.0) for _ in range(n + 2)]
    return [g[i + 2] - g[i] for i in range(n)]


def _window(
    *,
    lag_s: float,
    start_time_s: float = 23.0,
    duration_s: float = 18.0,
    epoch_s: float = 0.0,
    snr_db: float | None = None,
    reflection: tuple[float, float] | None = None,
    seed: int = 1,
) -> Recording:
    """A mono window recorded from capture time ``start_time_s`` of a track started at ``epoch_s`` that this
    speaker plays ``lag_s`` late. ``snr_db``: band noise, chirp band-energy peak over the noise's median band
    energy. ``reflection``: (delay_s, amplitude relative to the direct sound)."""
    track = _track(SR)
    n = round(duration_s * SR)
    lo = round((start_time_s - epoch_s - lag_s) * SR)
    assert lo >= 0 and lo + n <= len(track)
    x = list(track[lo : lo + n])
    if reflection:
        d, a = round(reflection[0] * SR), reflection[1]
        x = [v + (a * x[i - d] if i >= d else 0.0) for i, v in enumerate(x)]
    if snr_db is not None:
        noise = _band_noise(n, seed)
        env_x, env_n = est.band_envelope(x, SR), est.band_envelope(noise, SR)
        gain = math.sqrt(max(env_x) / (sorted(env_n)[len(env_n) // 2] * 10 ** (snr_db / 10)))
        x = [v + gain * w for v, w in zip(x, noise, strict=True)]
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, round(v * 32767)))) for v in x))
    return Recording(member="a", start_time_s=start_time_s, sample_rate=SR, pcm=pcm)


def _err_ms(phase_ms: float | None, truth_ms: float) -> float:
    assert phase_ms is not None
    cycle = ct.CYCLE_S * 1000.0
    return abs((phase_ms - truth_ms + cycle / 2) % cycle - cycle / 2)


def test_clean_window_keeps_the_per_click_precision():
    m = est.estimate(_window(lag_s=0.137, start_time_s=31.3, duration_s=12.0))
    assert m.status == "ok", m
    assert _err_ms(m.phase_ms, 137.0) <= 0.5


def test_clicks_too_weak_to_hear_one_by_one_are_measured_together():
    rec = _window(lag_s=0.215, snr_db=3.0, seed=4)
    assert est.estimate_per_click(rec).status != "ok"  # each chirp alone is lost in the room
    m = est.estimate(rec)
    assert m.status == "ok", m
    assert _err_ms(m.phase_ms, 215.0) <= 1.0
    assert m.clicks >= 16 and m.spread_ms is not None and 0.0 < m.confidence <= 1.0


def test_room_noise_alone_is_low_snr():
    n = round(18.0 * SR)
    noise = [0.02 * v for v in _band_noise(n, 9)]
    pcm = struct.pack(f"<{n}h", *(round(v * 32767) for v in noise))
    rec = Recording(member="a", start_time_s=23.0, sample_rate=SR, pcm=pcm)
    m = est.estimate_coherent(rec)
    assert (m.status, m.reason, m.phase_ms) == ("not_heard", "low_snr", None)
    assert est.estimate(rec).status == "not_heard"


def test_coherent_estimate_at_10_db():
    m = est.estimate_coherent(_window(lag_s=0.4, snr_db=10.0, seed=2))
    assert m.status == "ok", m
    assert _err_ms(m.phase_ms, 400.0) <= 1.0
    assert m.clicks >= 16 and m.spread_ms is not None and m.spread_ms <= 1.0


def test_coherent_estimate_honours_the_capture_clock_and_the_epoch():
    base = est.estimate_coherent(_window(lag_s=0.3, start_time_s=23.0, snr_db=10.0, duration_s=12.0))
    # the same audio, captured on a clock 7.25 s ahead and with the track started 7.25 s later on it
    shifted = est.estimate_coherent(
        _window(lag_s=0.3, start_time_s=30.25, epoch_s=7.25, snr_db=10.0, duration_s=12.0), epoch_s=7.25
    )
    assert _err_ms(shifted.phase_ms, 300.0) <= 1.0
    assert _err_ms(base.phase_ms, 300.0) <= 1.0
    # the same recording against an epoch 1.5 s later: every click is 1.5 s earlier in the track
    later = est.estimate_coherent(_window(lag_s=0.3, start_time_s=23.0, snr_db=10.0, duration_s=12.0), epoch_s=1.5)
    assert _err_ms(later.phase_ms, 300.0 - 1500.0) <= 1.0


@pytest.mark.parametrize("amplitude", [0.71, 1.1], ids=["minus_3_db", "louder_than_direct"])
def test_a_reflection_3_ms_later_is_not_the_direct_sound(amplitude):
    rec = _window(lag_s=0.25, snr_db=15.0, reflection=(0.003, amplitude), seed=3)
    m = est.estimate_coherent(rec)
    assert m.status == "ok", m
    assert _err_ms(m.phase_ms, 250.0) <= 0.5


def test_coherent_estimate_validates_like_the_per_click_one():
    n = 6 * SR
    assert est.estimate_coherent(Recording("a", 0.0, 7000, bytes(n * 2))).reason == "invalid_recording"
    assert est.estimate_coherent(Recording("a", 0.0, SR, bytes(n * 2 + 1))).reason == "invalid_recording"
    assert est.estimate_coherent(Recording("a", 0.0, SR, bytes(SR))).reason == "invalid_recording"
    assert est.estimate_coherent(Recording("a", float("nan"), SR, bytes(n * 2))).reason == "invalid_recording"
    assert est.estimate_coherent(Recording("a", 0.0, SR, bytes(n * 2)), epoch_s=math.inf).reason == (
        "invalid_recording"
    )


def test_silence_is_not_heard_by_the_coherent_estimate():
    m = est.estimate_coherent(Recording("a", 0.0, SR, bytes(6 * SR * 2)))
    assert (m.status, m.phase_ms) == ("not_heard", None)


def test_a_full_window_at_48_khz_stays_within_the_time_budget():
    track = _track(48000)
    n = round(20.0 * 48000)
    rnd = random.Random(5)
    x = [v + rnd.gauss(0.0, 0.01) for v in track[round(3.2 * 48000) : round(3.2 * 48000) + n]]
    pcm = struct.pack(f"<{n}h", *(int(max(-32767, min(32767, round(v * 32767)))) for v in x))
    rec = Recording(member="a", start_time_s=3.2, sample_rate=48000, pcm=pcm)
    t = time.perf_counter()
    m = est.estimate_coherent(rec)
    assert time.perf_counter() - t < 2.0
    assert m.status == "ok" and _err_ms(m.phase_ms, 0.0) <= 0.5


def test_a_speaker_whose_timing_wanders_during_the_window_is_unstable():
    # run3's web player: every click crisp and loud, but each one 2 ms later than the one before (a clock being
    # corrected); summed, they still give one confident phase, which is none of them
    n = round(18.0 * SR)
    x = [0.0] * n
    template = [v * ct.PEAK for v in ct.chirp(SR)]
    start_time_s = 23.0
    for i, (t, k) in enumerate(c for c in ct.click_times(60.0) if start_time_s < c[0] < start_time_s + 17.5):
        lag_s = 0.2 + 0.002 * i
        for chirp_t in (t, t + ct.gap_ms(k) / 1000):
            s = round((chirp_t + lag_s - start_time_s) * SR)
            for j, v in enumerate(template):
                x[s + j] += v
    noise = _band_noise(n, 6)
    x = [v + 0.003 * w for v, w in zip(x, noise, strict=True)]
    pcm = struct.pack(f"<{n}h", *(round(v * 32767) for v in x))
    m = est.estimate_coherent(Recording(member="a", start_time_s=start_time_s, sample_rate=SR, pcm=pcm))
    assert m.status == "unstable" and m.reason == "spread", m
    assert m.spread_ms is not None and m.spread_ms > est.MAX_SPREAD_MS


@pytest.mark.parametrize("clock", [3e17, 1e18, -1e18, 1e300])
def test_an_absurd_capture_clock_is_invalid_and_returns_at_once(clock):
    # the clock comes from the upload; at 1e18 adding a 10 s cycle no longer changes a float, so a loop stepping
    # through the window's cycles on that clock would never end (security review)
    rec = _window(lag_s=0.2, duration_s=20.0)
    rec.start_time_s = clock
    t = time.perf_counter()
    for estimate in (est.estimate, est.estimate_coherent, est.estimate_per_click):
        assert estimate(rec).reason == "invalid_recording"
        assert estimate(_window(lag_s=0.2, duration_s=6.0), epoch_s=clock).reason == "invalid_recording"
    assert time.perf_counter() - t < 2.0


def test_a_wall_clock_capture_clock_still_measures():
    # a capture clock in Unix seconds (~1.8e9) is large but sane: the phase is the same as on a small clock
    rec = _window(lag_s=0.2, start_time_s=23.0, snr_db=10.0, duration_s=12.0)
    rec.start_time_s += 1_793_110_560.0  # a multiple of the 10 s cycle
    m = est.estimate_coherent(rec, epoch_s=0.0)
    assert m.status == "ok" and _err_ms(m.phase_ms, 200.0) <= 1.0
