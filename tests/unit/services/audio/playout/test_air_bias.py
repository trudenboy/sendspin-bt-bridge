import math

import pytest

from sendspin_bridge.services.audio.playout.air_bias import (
    SEED_MAX_WINDOWS,
    STEP_WINDOWS,
    AirBias,
    AirFeed,
    AirSample,
)

L_AS = 228.7
D_OUT = 184_000.0  # the delay the windows' audio was written with
D_REF = 184.0  # the calibration's reference delay (ms); equal here, so E = H + L_as - D_out
KEY = ("sbc", 44100, "C0:FB:F9:62:D7:D6")


class FakeHistory:
    def __init__(self, e_us=5_000.0):
        self.e_us = e_us

    def median_over(self, lo, hi):
        return None if self.e_us is None else (self.e_us, 30)


def win(i, h_us, *, conf=1.0, d_out=D_OUT, gen=1):
    return AirSample(gen, 20e9 + i * 1e9, 23e9 + i * 1e9, 20e6 + i * 1e6, h_us, d_out, conf)


def bias(*, latency=L_AS, learned=None, starts=None):
    feed = AirFeed()
    feed.set_key(*KEY)
    feed.set_lock("locked", 1.0)
    feed.set_output_delay(D_OUT / 1000)
    feed.set_target(
        latency_ms=latency, d_ref_ms=D_REF, learned_ms=learned, learned_d_ref_ms=D_REF, learned_starts=starts
    )
    return AirBias(feed), feed


def run(b, feed, hist, samples, t0=20.0, beta_us=0.0):
    actions = []
    for k, s in enumerate(samples):
        feed.push(s)
        a = b.update(t0 + k, hist, beta_us=beta_us)
        if a is not None:
            actions.append(a)
    return actions


def test_three_agreeing_windows_seed_with_one_full_correction():
    b, feed = bias()
    # A late start: H = -6 ms gives E = H + L_as - D_ref = +38.7 ms (the three within 0.7 ms)
    acts = run(b, feed, FakeHistory(5_000.0), [win(i, -6_000.0 + d) for i, d in enumerate((0.0, 300.0, -400.0))])
    e_med = -6_000.0 + (L_AS - D_REF) * 1000
    assert b.state == "active"
    assert len(acts) == 1 and acts[0].reason == "air_seed"
    assert acts[0].jump_us == pytest.approx(e_med, abs=1.0)  # no 50 ms clamp, positive = late = drop
    assert b.value_us == pytest.approx(5_000.0 - e_med, abs=1.0)


def test_seeded_now_only_for_the_seeding_update():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000
    for i in range(3):
        feed.push(win(i, h))
        b.update(20.0 + i, FakeHistory())
        assert b.seeded_now is (i == 2)
    feed.push(win(3, h))
    b.update(23.0, FakeHistory())
    assert b.seeded_now is False


def test_the_seed_folds_the_baseline():
    # ê is raw: a 30 ms reading artefact measured as β before the seed ends up in b, not in c (C1)
    b, feed = bias()
    h = -6_000.0
    run(b, feed, FakeHistory(35_000.0), [win(i, h) for i in range(3)], beta_us=30_000.0)
    assert b.value_us == pytest.approx(35_000.0 - (h + (L_AS - D_REF) * 1000), abs=1.0)


def test_inside_the_dead_band_no_jump():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000 + 1_500.0  # E = +1.5 ms
    assert run(b, feed, FakeHistory(), [win(i, h) for i in range(3)]) == []
    assert b.state == "active"


def test_disagreeing_windows_slide_and_fall_back_after_12():
    b, feed = bias()
    hs = [(-6_000.0 if i % 2 else -10_000.0) for i in range(SEED_MAX_WINDOWS)]  # 4 ms apart: never agree
    acts = run(b, feed, FakeHistory(), [win(i, h) for i, h in enumerate(hs)])
    assert acts == [] and b.state == "active" and b.learned is None


def test_low_confidence_or_thin_coverage_is_not_eligible():
    b, feed = bias()
    run(b, feed, FakeHistory(), [win(i, -6_000.0, conf=0.4) for i in range(5)])
    run(b, feed, FakeHistory(e_us=None), [win(i + 5, -6_000.0) for i in range(5)])
    assert b.state == "seeding" and b.snapshot()["air_bias_skipped"] == 10


def test_tracking_follows_with_tau_30_s():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000  # E = 0
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    run(b, feed, FakeHistory(8_000.0), [win(i + 3, h) for i in range(30)], t0=23.0)  # b_i 3 ms higher for 30 s
    assert b.value_us == pytest.approx(5_000.0 + 3_000.0 * (1 - math.exp(-1.0)), abs=150.0)


def test_one_wrong_window_is_gated_and_does_not_widen_the_gate():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    before = b.value_us
    run(b, feed, FakeHistory(5_000.0), [win(3 + k, h - 50_000.0) for k in range(5)], t0=23.0)  # 5 wrong in a row
    assert abs(b.value_us - before) < 100.0 and b.snapshot()["air_bias_gated"] == 5  # MAD over accepted only (I6)


def test_a_persistent_hidden_step_is_absorbed_after_15_windows():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    run(b, feed, FakeHistory(5_000.0), [win(i + 3, h + 25_000.0) for i in range(STEP_WINDOWS)], t0=23.0)
    assert b.value_us == pytest.approx(5_000.0 - 25_000.0, abs=200.0)


def test_learned_sample_once_per_output_from_the_initial_triple():
    b, feed = bias(latency=None)  # no target: learning only
    h = -35_000.0
    run(b, feed, FakeHistory(), [win(i, h) for i in range(6)])
    s = b.learned
    assert b.state == "none" and s is not None and s.seq == 1
    assert b.snapshot()["air_bias_reason"] == "no_target"
    assert (s.codec, s.rate, s.adapter) == KEY and s.x_ms == pytest.approx((D_OUT - h) / 1000, abs=0.01)
    assert s.d_out_ms == pytest.approx(D_OUT / 1000)
    run(b, feed, FakeHistory(), [win(i + 6, h) for i in range(6)], t0=26.0)
    assert b.learned.seq == 1  # still the one sample


def test_no_key_no_learned_sample():
    feed = AirFeed()
    feed.set_lock("locked", 1.0)
    b = AirBias(feed)
    run(b, feed, FakeHistory(), [win(i, -35_000.0) for i in range(3)])
    assert b.learned is None


def test_the_target_does_not_contain_the_windows_delay():
    # Windows written at a delay 30 ms above D_ref, same H: the same E (the trim is the delay itself)
    b, feed = bias()
    run(b, feed, FakeHistory(), [win(i, -6_000.0, d_out=D_OUT + 30_000.0) for i in range(3)])
    assert b.snapshot()["air_last_correction_ms"] == pytest.approx(-6.0 + L_AS - D_REF, abs=0.01)


def test_events_inside_a_window_exclude_it():
    b, feed = bias()
    b.on_event(21.5e9)  # inside the first two windows' spans
    run(b, feed, FakeHistory(), [win(i, -6_000.0) for i in range(3)])
    assert b.state == "seeding"  # the triple must start after 21.5e9: two more windows needed
