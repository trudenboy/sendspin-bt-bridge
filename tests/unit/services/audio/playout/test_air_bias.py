import logging
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
    assert (s.codec, s.rate, s.adapter) == KEY and s.x_ms == pytest.approx((D_OUT - h + 5_000.0) / 1000, abs=0.01)
    assert s.d_out_ms == pytest.approx(D_OUT / 1000)
    run(b, feed, FakeHistory(), [win(i + 6, h) for i in range(6)], t0=26.0)
    assert b.learned.seq == 1  # still the one sample


def test_learned_sample_is_the_placement_the_loop_holds():
    # The speaker still plays e = 50 ms late at lock (start transient): learn d - h + e - beta, not the raw d - h.
    b, feed = bias(latency=None)
    h = -35_000.0
    run(b, feed, FakeHistory(50_000.0), [win(i, h) for i in range(3)], beta_us=4_000.0)
    assert b.learned is not None
    assert b.learned.x_ms == pytest.approx((D_OUT - h + 50_000.0 - 4_000.0) / 1000, abs=0.01)


def test_learned_sample_retries_until_the_key_is_complete():
    feed = AirFeed()
    feed.set_lock("locked", 1.0)
    feed.set_output_delay(D_OUT / 1000)
    b = AirBias(feed)
    run(b, feed, FakeHistory(), [win(i, -35_000.0) for i in range(3)])
    assert b.learned is None
    feed.set_key(*KEY)
    run(b, feed, FakeHistory(), [win(i + 3, -35_000.0) for i in range(3)], t0=23.0)
    assert b.learned is not None and b.learned.seq == 1


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


# ---- part 2: events, targets, held and reseed -------------------------------

H0 = (D_REF - L_AS) * 1000  # E = 0 for the calibrated target
INFO = logging.INFO


def seeded(**kw):
    b, feed = bias(**kw)
    run(b, feed, FakeHistory(5_000.0), [win(i, H0) for i in range(3)])
    assert b.state == "active" and b.value_us == pytest.approx(5_000.0)
    return b, feed


def more(b, feed, i0, n, h=H0, e=5_000.0, **kw):
    return run(b, feed, FakeHistory(e), [win(i0 + k, h) for k in range(n)], t0=20.0 + i0, **kw)


def test_a_delay_keeps_b_trims_and_skips_the_spanning_window(caplog):
    b, feed = seeded()
    with caplog.at_level(INFO):
        b.on_delay(30_000.0, 23.5e9)
    assert any(
        "Output delay changed 184\u2192214" in r.getMessage() and "= -30.0 ms" in r.getMessage() for r in caplog.records
    )
    snap = b.snapshot()
    assert b.value_us == pytest.approx(5_000.0) and snap["air_trim_ms"] == pytest.approx(30.0)
    acts = run(b, feed, FakeHistory(5_000.0), [win(i, H0, d_out=D_OUT + 30_000.0) for i in range(3, 9)], t0=23.0)
    assert acts == [] and b.snapshot()["air_bias_skipped"] == 1  # only the window across x = 23.5 s
    assert abs(b.value_us - 5_000.0) < 100.0


def test_format_change_reseeds_provisionally_at_the_same_error_without_a_jump():
    b, feed = seeded()
    b.on_format(22.5e9)
    assert b.state == "seeding" and b.value_us == pytest.approx(5_000.0)
    assert more(b, feed, 3, 2) == [] and b.state == "seeding"
    assert more(b, feed, 5, 1) == [] and b.state == "active"
    assert b.learned is not None and b.learned.seq == 1  # a reseed never learns again


def test_format_change_with_a_new_error_seeds_one_jump():
    b, feed = seeded()
    b.on_format(22.5e9)
    acts = more(b, feed, 3, 3, h=H0 + 5_000.0)
    assert [a.reason for a in acts] == ["air_seed"] and acts[0].jump_us == pytest.approx(5_000.0, abs=1.0)
    assert b.value_us == pytest.approx(0.0, abs=1.0)  # the reseed replaces, no filtering across


def test_underrun_freezes_until_the_reseed():
    b, feed = seeded()
    assert b.handles_breaks  # b has a value outside held
    b.on_reading_break("underrun", 22.5e9, 23.0)
    assert b.frozen and b.handles_breaks and b.state == "seeding"
    more(b, feed, 3, 3)
    assert b.state == "active" and not b.frozen


def test_reseed_timeout_with_the_lock_kept_goes_active_with_the_provisional_value():
    b, _feed = seeded()
    b.on_reading_break("stall", 22.5e9, 23.0)
    assert b.update(37.9, FakeHistory()) is None and b.frozen
    assert b.update(38.0, FakeHistory()) is None
    assert b.state == "active" and not b.frozen and b.value_us == pytest.approx(5_000.0)


def test_underrun_then_lock_lost_holds_the_reseed_and_runs_it_when_the_lock_returns():
    b, feed = seeded()
    b.on_reading_break("underrun", 22.5e9, 23.0)
    feed.set_lock("no_lock", None)
    b.update(24.0, FakeHistory())
    assert b.state == "held" and b.reseed_pending and not b.frozen
    assert b.snapshot()["air_bias_reason"] == "not_locked" and not b.handles_breaks
    feed.set_lock("locked", 1.0)
    assert more(b, feed, 3, 1, h=H0 + 1_000.0) == [] and b.state == "seeding" and not b.reseed_pending
    assert more(b, feed, 4, 1, h=H0 + 1_000.0) == [] and b.state == "seeding"
    assert more(b, feed, 5, 1, h=H0 + 1_000.0) == [] and b.state == "active"  # 1 ms: inside the band (I2)


def test_a_reseed_after_held_jumps_only_beyond_the_dead_band():
    b, feed = seeded()
    b.on_format(22.5e9)
    feed.set_lock("no_lock", None)
    b.update(24.0, FakeHistory())
    assert b.state == "held" and b.reseed_pending
    feed.set_lock("locked", 1.0)
    acts = more(b, feed, 3, 3, h=H0 + 4_000.0)
    assert [a.reason for a in acts] == ["air_seed"] and b.state == "active"


def test_held_in_a_break_or_format_only_marks_the_reseed():
    b, feed = seeded()
    feed.set_lock("no_lock", None)
    b.update(24.0, FakeHistory())
    b.on_format(22.5e9)
    assert b.state == "held" and b.reseed_pending
    b2, feed2 = seeded()
    feed2.set_lock("no_lock", None)
    b2.update(24.0, FakeHistory())
    b2.on_reading_break("pa_step", 22.5e9, 24.0)
    assert b2.state == "held" and b2.reseed_pending and not b2.frozen


def test_lock_lost_holds_b_and_resume_folds_beta(caplog):
    b, feed = seeded()
    with caplog.at_level(INFO):
        feed.set_lock("no_lock", None)
        b.update(24.0, FakeHistory())
    assert any("air bias held (sensor no_lock)" in r.getMessage() for r in caplog.records)
    run(b, feed, FakeHistory(), [win(3 + i, H0, conf=0.0) for i in range(60)], t0=25.0)
    assert b.state == "held" and b.value_us == pytest.approx(5_000.0) and not b.reseed_pending
    assert b.snapshot()["air_bias_skipped"] == 60
    feed.set_lock("locked", 1.0)
    acts = run(b, feed, FakeHistory(5_000.0), [win(63, H0)], t0=90.0, beta_us=12_000.0)
    assert acts == [] and b.state == "active"
    assert b.value_us == pytest.approx(5_000.0 + 12_000.0) and b.resume_beta() == 12_000.0


def test_holding_counts_as_locked():
    b, feed = seeded()
    feed.set_lock("holding", 0.8)
    more(b, feed, 3, 30, e=8_000.0)
    assert b.state == "active" and b.value_us > 5_500.0  # tracked, not held


def test_calibration_change_moves_b_and_jumps():
    b, feed = seeded()
    assert b.learned is not None  # own sample published: a calibration is still adopted
    feed.set_target(latency_ms=L_AS + 20.0, d_ref_ms=D_REF)
    a = b.update(24.0, FakeHistory())
    assert a is not None and a.reason == "air_target" and a.jump_us == pytest.approx(20_000.0)
    assert b.value_us == pytest.approx(5_000.0 - 20_000.0)


def test_d_ref_change_moves_the_jump_the_other_way():
    b, feed = seeded()
    feed.set_target(latency_ms=L_AS, d_ref_ms=D_REF + 20.0)
    a = b.update(24.0, FakeHistory())
    assert a is not None and a.reason == "air_target" and a.jump_us == pytest.approx(-20_000.0)
    assert b.value_us == pytest.approx(5_000.0 + 20_000.0)


def test_a_change_inside_the_dead_band_moves_b_without_a_jump():
    b, feed = seeded()
    feed.set_target(latency_ms=L_AS + 1.0, d_ref_ms=D_REF)
    assert b.update(24.0, FakeHistory()) is None and b.value_us == pytest.approx(4_000.0)


def test_forgotten_calibration_falls_back_to_the_learned_value():
    b, feed = seeded()
    feed.set_target(latency_ms=None, learned_ms=226.0, learned_d_ref_ms=184.0, learned_starts=2)
    a = b.update(24.0, FakeHistory())
    assert a is not None and a.reason == "air_target" and a.jump_us == pytest.approx((226.0 - L_AS) * 1000)
    assert b.snapshot()["air_target"] == "learned"


def test_forgotten_after_a_calibration_change_uses_the_changed_target():
    b, feed = seeded()
    feed.set_target(latency_ms=248.7, d_ref_ms=D_REF)
    b.update(24.0, FakeHistory())
    feed.set_target(latency_ms=None, learned_ms=226.0, learned_d_ref_ms=184.0, learned_starts=2)
    a = b.update(25.0, FakeHistory())
    assert a is not None and a.jump_us == pytest.approx((226.0 - 248.7) * 1000)


def test_forgotten_without_a_learned_value_drops_to_none():
    b, feed = seeded()
    feed.set_target(latency_ms=None)
    assert b.update(24.0, FakeHistory()) is None
    assert b.state == "none" and b.value_us is None and b.snapshot()["air_bias_reason"] == "no_target"
    assert not b.handles_breaks


def test_a_learned_target_before_the_own_sample_is_taken():
    b, _feed = bias(latency=None, learned=226.0, starts=2)
    b.update(20.0, FakeHistory())
    assert b.state == "seeding" and b.snapshot()["air_target"] == "learned"


def test_a_learned_target_after_the_own_sample_is_ignored():
    b, feed = bias(latency=None)
    run(b, feed, FakeHistory(), [win(i, -35_000.0) for i in range(3)])
    assert b.learned is not None and b.state == "none"
    feed.set_target(latency_ms=None, learned_ms=226.0, learned_d_ref_ms=184.0, learned_starts=2)
    b.update(24.0, FakeHistory())
    assert b.state == "none"
    feed.set_target(latency_ms=L_AS, d_ref_ms=D_REF)  # a calibration is still adopted
    b.update(25.0, FakeHistory())
    assert b.state == "seeding"


def test_a_changed_learned_value_after_the_own_sample_does_not_move_a_learned_target():
    b, feed = bias(latency=None, learned=226.0, starts=2)
    run(b, feed, FakeHistory(), [win(i, H0 + 2_700.0) for i in range(3)])
    assert b.learned is not None and b.state == "active"
    feed.set_target(latency_ms=None, learned_ms=250.0, learned_d_ref_ms=184.0, learned_starts=3)
    assert b.update(24.0, FakeHistory()) is None
    assert b.snapshot()["air_target_ms"] == 226.0


def test_the_learned_sample_is_emitted_while_seeding_with_a_target():
    b, feed = bias()
    assert b.learned is None
    run(b, feed, FakeHistory(), [win(i, H0) for i in range(3)])
    assert b.learned is not None and b.learned.seq == 1 and b.state == "active"


def test_fallback_seeds_b_from_the_median_of_the_last_five():
    b, feed = bias()
    hs = [(-6_000.0 if i % 2 else -10_000.0) for i in range(SEED_MAX_WINDOWS)]
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i, h in enumerate(hs)])
    assert b.value_us == pytest.approx(5_000.0 - (-6_000.0 + (L_AS - D_REF) * 1000))  # 3 of the last 5 at -6 ms


def test_fallback_needs_five_surviving_candidates_and_an_event_resets_the_count():
    b, feed = bias()
    hs = [(-6_000.0 if i % 2 else -10_000.0) for i in range(SEED_MAX_WINDOWS - 1)]
    run(b, feed, FakeHistory(), [win(i, h) for i, h in enumerate(hs)])
    b.on_event(20e9 + 9.5e9)  # drops the older candidates, restarts the count
    run(b, feed, FakeHistory(), [win(20 + i, hs[i]) for i in range(2)], t0=40.0)
    assert b.state == "seeding"


def test_entering_active_clears_the_candidates():
    b, feed = bias()
    run(b, feed, FakeHistory(), [win(i, H0) for i in range(3)])
    assert b.state == "active" and len(b._cands) == 0


def test_a_d_out_latched_reference_when_the_target_has_none():
    feed = AirFeed()
    feed.set_key(*KEY)
    feed.set_lock("locked", 1.0)
    feed.set_output_delay(200.0)
    feed.set_target(latency_ms=L_AS, d_ref_ms=None)
    b = AirBias(feed)
    run(b, feed, FakeHistory(5_000.0), [win(i, -6_000.0, d_out=200_000.0) for i in range(3)])
    assert b.value_us == pytest.approx(5_000.0 - (-6_000.0 + (L_AS - 200.0) * 1000))
    snap = b.snapshot()
    assert snap["air_d_ref_ms"] == 200.0 and snap["air_trim_ms"] == pytest.approx(0.0)


def test_dt_is_clamped_to_five_seconds():
    b, feed = seeded()
    s = AirSample(1, 24e9, 27e9, 20e6 + 500e6, H0, D_OUT, 1.0)  # 500 s after the seed
    feed.push(s)
    b.update(30.0, FakeHistory(8_000.0))
    assert b.value_us == pytest.approx(5_000.0 + 3_000.0 * (1 - math.exp(-5.0 / 30.0)), abs=1.0)


def test_the_mad_term_widens_the_gate():
    b, feed = seeded()
    run(b, feed, FakeHistory(0.0), [win(3, H0)], t0=23.0)
    for k in range(20):  # accepted b_i alternate 0 / 10 ms: MAD 5 ms, gate ~29.7 ms
        run(b, feed, FakeHistory(10_000.0 if k % 2 else 0.0), [win(4 + k, H0)], t0=24.0 + k)
    gated = b.snapshot()["air_bias_gated"]
    run(b, feed, FakeHistory(25_000.0), [win(30, H0)], t0=50.0)  # 20 ms off: beyond 10 ms, inside the widened gate
    assert b.snapshot()["air_bias_gated"] == gated
    c, cfeed = seeded()
    run(c, cfeed, FakeHistory(25_000.0), [win(3, H0)], t0=23.0)  # constant history: gate stays 10 ms
    assert c.snapshot()["air_bias_gated"] == 1


def test_a_pa_step_before_the_first_seed_does_not_restart_the_triple():
    # PipeWire's clock sawtooth steps every few seconds; the initial triple is air-only, so a step
    # must not keep resetting it (found by the closed-loop sawtooth case). Underruns still restart it.
    h = (D_REF - L_AS) * 1000 + 20_000.0
    b, feed = bias()
    hist = FakeHistory()
    run(b, feed, hist, [win(0, h), win(1, h)])
    b.on_reading_break("pa_step", 21.5e9, 22.0)
    acts = run(b, feed, hist, [win(2, h)], t0=22.0)
    assert b.state == "active"
    assert [a.reason for a in acts] == ["air_seed"]

    b2, feed2 = bias()
    run(b2, feed2, hist, [win(0, h), win(1, h)])
    b2.on_reading_break("underrun", 21.5e9, 22.0)
    assert run(b2, feed2, hist, [win(2, h)], t0=22.0) == []
    assert b2.state == "seeding"


def test_a_hidden_step_is_absorbed_despite_a_few_noisy_windows():
    # 12 of the 15 gated windows within 3 ms of their median are enough; the other three may stray
    b, feed = bias()
    h = (D_REF - L_AS) * 1000
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    offs = [25_000.0] * 15
    offs[2], offs[7], offs[11] = 29_500.0, 20_500.0, 30_000.0
    run(b, feed, FakeHistory(5_000.0), [win(i + 3, h + o) for i, o in enumerate(offs)], t0=23.0)
    assert b.value_us == pytest.approx(5_000.0 - 25_000.0, abs=200.0)


def test_a_scattered_run_is_not_a_step():
    b, feed = bias()
    h = (D_REF - L_AS) * 1000
    run(b, feed, FakeHistory(5_000.0), [win(i, h) for i in range(3)])
    offs = [25_000.0 + (8_000.0 if i % 2 else -8_000.0) for i in range(15)]  # 15 gated, none within 3 ms of the median
    run(b, feed, FakeHistory(5_000.0), [win(i + 3, h + o) for i, o in enumerate(offs)], t0=23.0)
    assert b.value_us == pytest.approx(5_000.0, abs=200.0)
