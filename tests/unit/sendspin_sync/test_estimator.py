import math
import struct
import time

import pytest

from sendspin_sync import click_track as ct
from sendspin_sync import estimator as est
from sendspin_sync.models import Recording
from tests.support.sync_synth import band_chatter, bursty_noise, sparse_lsb_noise, synth


def _phase(m):
    assert m.status == "ok", m
    return m.phase_ms


def test_clean_recording_gives_phase_equal_to_lag():
    m = est.estimate(synth("a", lag_s=0.123))
    assert m.clicks == 3 and m.confidence > 0.5
    assert abs(_phase(m) - 123.0) <= 1.0 and m.spread_ms <= 1.0


def test_phase_is_relative_to_the_cycle_and_independent_of_start_time():
    a = est.estimate(synth("a", lag_s=0.250, start_time_s=20.0))
    b = est.estimate(synth("a", lag_s=0.250, start_time_s=33.7))
    assert abs(_phase(a) - _phase(b)) <= 1.0


def test_lag_near_cycle_edge_is_measured_modulo_ten_seconds():
    m = est.estimate(synth("a", lag_s=-0.2))
    assert abs(_phase(m) - 9800.0) <= 1.0  # -200 ms ≡ 9800 ms in the 10 s cycle


def test_noise_echo_band_limit_and_agc_are_tolerated():
    m = est.estimate(synth("a", lag_s=0.05, noise=0.05, echo=(0.012, 0.15), agc=True, sample_rate=22050))
    assert abs(_phase(m) - 50.0) <= 2.0


def test_dropped_clicks_lower_the_count_and_one_click_is_not_enough():
    # clicks at 21, 23, 25 s → indexes 10, 11, 12; two dropped
    m = est.estimate(synth("a", lag_s=0.0, start_time_s=20.0, drop={10, 11}))
    assert m.status == "not_heard" and m.clicks == 1 and m.reason == "too_few_clicks"


def test_silence_is_not_heard_and_garbage_is_invalid():
    n = 6 * 16000
    silent = Recording(member="a", start_time_s=0.0, sample_rate=16000, pcm=bytes(n * 2))
    assert est.estimate(silent).status == "not_heard"
    bad_rate = Recording(member="a", start_time_s=0, sample_rate=7000, pcm=bytes(n * 2))
    assert est.estimate(bad_rate).reason == "invalid_recording"
    odd_bytes = Recording(member="a", start_time_s=0, sample_rate=16000, pcm=bytes(n * 2 + 1))
    assert est.estimate(odd_bytes).reason == "invalid_recording"
    too_short = Recording(member="a", start_time_s=0, sample_rate=16000, pcm=bytes(2 * 16000 // 4))
    assert est.estimate(too_short).reason == "invalid_recording"


def test_unstable_when_clicks_disagree():
    # two clicks 10 ms apart in phase: build by concatenating halves with different lags
    a = synth("a", lag_s=0.100, duration_s=3.0)
    b = synth("a", lag_s=0.110, duration_s=3.0, start_time_s=23.0)
    rec = Recording(member="a", start_time_s=20.0, sample_rate=16000, pcm=a.pcm + b.pcm)
    m = est.estimate(rec)
    assert m.status == "unstable" and m.spread_ms is not None and m.spread_ms > 3.0


def test_pure_python_cost_is_bounded():
    import time

    rec = synth("a", lag_s=0.3, duration_s=12.0, noise=0.03)
    t = time.perf_counter()
    est.estimate(rec)
    assert time.perf_counter() - t < 2.0


@pytest.mark.parametrize("delay_s", [0.025, 0.060])
def test_room_echo_after_each_chirp_is_not_a_click(delay_s):
    # a reflection 25/60 ms later at -3.5 dB must not pair up as an extra click (60 ms is also a valid gap)
    m = est.estimate(synth("a", lag_s=0.1, echo=(delay_s, 0.2)))
    assert m.clicks == 3
    assert abs(_phase(m) - 100.0) <= 1.0


@pytest.mark.parametrize(
    "rec",
    [sparse_lsb_noise("a"), bursty_noise("a"), bursty_noise("a", rate_hz=20.0, seed=9)],
    ids=["sparse_lsb", "bursty", "bursty_dense"],
)
def test_garbage_without_a_click_track_is_not_heard(rec):
    assert est.estimate(rec).status == "not_heard"


@pytest.mark.parametrize(
    "make",
    [
        lambda: sparse_lsb_noise("a", duration_s=20.0, sample_rate=48000),
        lambda: bursty_noise("a", duration_s=20.0, sample_rate=48000, rate_hz=20.0),
    ],
    ids=["sparse_lsb", "bursty"],
)
def test_garbage_at_the_largest_accepted_geometry_is_cheap(make):
    rec = make()
    t = time.perf_counter()
    m = est.estimate(rec)
    assert time.perf_counter() - t < 2.0
    assert m.status == "not_heard"


def test_silence_reason_is_no_clicks():
    silent = Recording(member="a", start_time_s=0.0, sample_rate=16000, pcm=bytes(6 * 16000 * 2))
    m = est.estimate(silent)
    assert (m.status, m.reason, m.clicks) == ("not_heard", "no_clicks", 0)


def test_room_noise_without_clicks_is_no_clicks():
    m = est.estimate(synth("a", lag_s=0.0, noise=0.02, gain=0.0))
    assert (m.status, m.reason) == ("not_heard", "no_clicks")


def test_clicks_barely_above_the_noise_are_low_snr():
    m = est.estimate(synth("a", lag_s=0.1, noise=0.05, gain=0.1))
    assert (m.status, m.reason) == ("not_heard", "low_snr")
    assert m.clicks >= 2 and m.confidence == 0.0


def _mix(a: Recording, b: Recording) -> Recording:
    sa = struct.unpack(f"<{len(a.pcm) // 2}h", a.pcm)
    sb = struct.unpack(f"<{len(b.pcm) // 2}h", b.pcm)
    mixed = [max(-32767, min(32767, p + q)) for p, q in zip(sa, sb, strict=True)]
    return Recording(
        member=a.member,
        start_time_s=a.start_time_s,
        sample_rate=a.sample_rate,
        pcm=struct.pack(f"<{len(mixed)}h", *mixed),
    )


def test_clicks_survive_moderate_clatter():
    # ~10 noise bursts a second: more events than a click track can make, the strongest are still the chirps
    clicks = synth("a", lag_s=0.2, duration_s=12.0, noise=0.0)
    clatter = bursty_noise("a", duration_s=12.0, rate_hz=10.0, level=0.03, seed=5)
    m = est.estimate(_mix(clicks, clatter))
    assert m.clicks == 6
    assert abs(_phase(m) - 200.0) <= 1.0


def test_loud_clicks_are_found_among_dense_weak_band_chatter():
    # a real phone recording (AAC, desk, 1-2 m away): the band envelope crackles with >100 weak events per 6 s at
    # 3x the floor, while the chirps stand 20+ dB above it; the crackle must not hide the clicks
    clicks = synth("a", lag_s=0.2, noise=0.003, gain=0.1, sample_rate=48000)
    rec = _mix(clicks, band_chatter("a", sample_rate=48000))
    m = est.estimate(rec)
    x = est.pcm_to_floats(rec.pcm)
    env = est.band_envelope(x, 48000)
    floor = sorted(env)[len(env) // 2]
    plausible = est._EVENTS_PER_PERIOD * (math.ceil(len(x) / 48000 / ct.PERIOD_S) + 1)
    assert len(est._events(env, 3.0 * floor)) > 4 * plausible  # the density that used to bail out
    assert 10 * math.log10(max(env) / floor) >= 20.0
    assert m.clicks == 3
    assert abs(_phase(m) - 200.0) <= 1.0


def test_a_chirp_the_speaker_or_codec_roughened_still_counts():
    # E5 run3, a web kiosk: clearly audible clicks (28 dB band peak) whose per-chirp correlation with the template
    # is only ~0.5 (AAC, the kiosk's own processing); here in-band noise riding on each chirp gives ~0.45. The pair
    # gap and the click sequence reject garbage, the per-chirp gate only has to drop what is plainly no chirp.
    m = est.estimate(synth("a", lag_s=0.1, masked=1.7, sample_rate=48000, noise=0.003))
    assert m.clicks == 3
    assert abs(_phase(m) - 100.0) <= 1.0


def test_a_room_full_of_clatter_is_too_noisy_to_try():
    m = est.estimate(bursty_noise("a", rate_hz=50.0, level=0.1))
    assert (m.status, m.reason) == ("not_heard", "low_snr")


@pytest.mark.parametrize(
    ("lag_s", "delay_s", "seed"),
    [(0.1, 0.002, 1), (0.37, 0.020, 2), (0.1, 0.040, 1), (0.1, 0.060, 1)],
    ids=["2ms_overlaps_the_chirp", "20ms_shifts_gaps_down", "40ms_aliases_two_gap_steps", "60ms_lands_on_partner"],
)
def test_a_reflection_as_loud_as_the_direct_sound_is_still_not_the_click(lag_s, delay_s, seed):
    # a hard wall next to the phone: the reflection matches the direct chirp, so only arrival order tells them apart
    m = est.estimate(synth("a", lag_s=lag_s, echo=(delay_s, 0.3), seed=seed))
    assert m.clicks == 3
    assert abs(_phase(m) - lag_s * 1000) <= 1.0


def test_matched_filter_work_is_bounded_by_the_click_track_not_by_the_noise(monkeypatch):
    calls = 0
    real = est.matched_peak

    def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(est, "matched_peak", counting)
    rec = bursty_noise("a", duration_s=12.0, rate_hz=10.0, level=0.1)  # ~100 events in 12 s
    est.estimate(rec)
    # a 12 s click track has 7 periods x (2 chirps + reflections) = 42 plausible events, two searches each
    assert calls <= 2 * 42


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_clock_is_an_invalid_recording(bad):
    rec = synth("a", lag_s=0.0)
    rec.start_time_s = bad
    m = est.estimate(rec)
    assert m.reason == "invalid_recording" and m.phase_ms is None
    assert est.estimate(synth("a", lag_s=0.0), epoch_s=bad).reason == "invalid_recording"
