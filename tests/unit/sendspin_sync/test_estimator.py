from sendspin_sync import estimator as est
from sendspin_sync.models import Recording
from tests.support.sync_synth import synth


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
