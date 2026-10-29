"""B5a: the engine measures its loop error against the air bias (residual domains, C1, the guard)."""

import logging

import pytest

from sendspin_bridge.services.audio.playout.air_bias import AirFeed, AirSample
from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

FS = 48000
ZERO = b"\x00\x00" * 2
ENGINE_LOGGER = "sendspin_bridge.services.audio.playout.engine"
LATENCY_MS = 184.0
D_REF_MS = 184.0  # equal, so E_equiv = H
KEY = ("sbc", 44100, "C0:FB:F9:62:D7:D6")
WIDTH_NS = 2e9


def make_feed(*, locked=True):
    feed = AirFeed()
    feed.set_key(*KEY)
    feed.set_lock("locked" if locked else "no_signal", 1.0 if locked else None)
    feed.set_output_delay(D_REF_MS)
    feed.set_target(latency_ms=LATENCY_MS, d_ref_ms=D_REF_MS)
    return feed


def engine(mode="loop", *, feed=None, **port_kw):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, **port_kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode=mode), port=port, air_feed=feed)
    eng.set_format(InputFormat(sample_rate=FS))
    eng.submit(int(raw.t_us + 1e6), ZERO * FS * 120)
    return eng, port, raw


def windows(feed, eng, raw, h_us, *, n=3):
    """Push ``n`` windows whose span is written audio the port has played, starting at the port now.

    The caller drives ``n * 2 s`` of readings first, so the history covers the spans.
    """
    x0 = eng.port_x_ns
    assert x0 is not None
    return x0, lambda: [
        feed.push(AirSample(1, x0 + i * WIDTH_NS, x0 + (i + 1) * WIDTH_NS, raw.t_us, h_us, D_REF_MS * 1000.0, 1.0))
        for i in range(n)
    ]


def seed(eng, port, raw, feed, h_us=1_000.0, *, n=3):
    _, push = windows(feed, eng, raw, h_us, n=n)
    drive(eng, port, raw, n * 2.0 + 0.5)
    push()
    drive(eng, port, raw, 1.0)


def underrun(eng, port, raw, slew_ms=0.0):
    port.starve(800.0)
    for _ in range(100):
        drive(eng, port, raw, 0.1)
        s = eng.snapshot()
        if s["underruns"] >= 1 and s["last_jump_reason"] == "underrun":
            break
    else:
        raise AssertionError("no underrun placement")
    if slew_ms:
        port.slew(slew_ms, 300.0)


def messages(caplog, level=logging.INFO):
    return [r.getMessage() for r in caplog.records if r.levelno >= level]


def test_measure_with_a_feed_has_no_bias():
    eng, port, raw = engine("measure", feed=make_feed())
    drive(eng, port, raw, 5.0)
    assert eng.snapshot()["air_bias_state"] is None
    assert eng._bias is None


def test_loop_without_a_feed_has_no_bias():
    eng, port, raw = engine("loop")
    drive(eng, port, raw, 5.0)
    snap = eng.snapshot()
    assert snap["air_bias_state"] is None and snap["air_bias_ms"] is None and snap["air_corrections"] == 0


def test_a_seed_inside_the_dead_band_moves_nothing_and_the_error_is_in_the_c_domain(caplog):
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    port.skip(-6.0)  # the pulsesink reads 6 ms late; the air says the sound is 1 ms late
    drive(eng, port, raw, 3.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        seed(eng, port, raw, feed, 1_000.0)
        drive(eng, port, raw, 3.0)
    snap = eng.snapshot()
    assert snap["air_bias_state"] == "active"
    assert not [m for m in messages(caplog) if "steady" in m or "air_" in m]  # no jump
    assert snap["error_ms"] == pytest.approx(1.0, abs=1.5)
    assert snap["air_bias_ms"] == pytest.approx(5.0, abs=1.5)
    assert snap["error_ms"] + snap["air_bias_ms"] == pytest.approx(6.0, abs=1.5)  # the raw pulsesink error


def test_a_clear_keeps_the_c_domain_and_the_bias():
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    port.skip(-6.0)
    drive(eng, port, raw, 3.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 3.0)
    before = eng.snapshot()["air_bias_ms"]
    eng.clear()
    eng.submit(int(raw.t_us + 1e6), ZERO * FS * 60)
    drive(eng, port, raw, 6.0)
    snap = eng.snapshot()
    assert snap["last_jump_reason"] in ("clear", "starved")
    assert snap["error_ms"] == pytest.approx(0.0, abs=1.5)
    assert snap["air_bias_ms"] == before


def test_an_underrun_before_the_seed_collects_a_baseline_and_the_seed_folds_it(caplog):
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        underrun(eng, port, raw, slew_ms=-30.0)
        drive(eng, port, raw, 12.0)
    assert any("error baseline" in m for m in messages(caplog))
    assert eng._baseline_us == pytest.approx(30_000.0, abs=3_000.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 2.0)
    assert eng._baseline_us == 0.0
    assert eng.snapshot()["air_bias_state"] == "active"
    assert eng.snapshot()["error_ms"] == pytest.approx(1.0, abs=1.5)


def test_an_underrun_in_active_collects_no_baseline_and_freezes_until_three_new_samples(caplog):
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 3.0)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        underrun(eng, port, raw, slew_ms=-30.0)
        drive(eng, port, raw, 1.5)
    assert not any("error baseline" in m for m in messages(caplog))
    assert eng._baseline_us == 0.0
    assert eng._bias is not None and eng._bias.frozen
    frozen_error = eng.snapshot()["error_ms"]
    drive(eng, port, raw, 1.5)  # the reseed must come inside RESEED_TIMEOUT_S (15 s) of the break
    assert eng.snapshot()["error_ms"] == frozen_error  # the controller did not move
    seed(eng, port, raw, feed, 1_000.0)
    assert not eng._bias.frozen
    assert eng.snapshot()["air_bias_state"] == "active"
    drive(eng, port, raw, 5.0)  # the controller's EMA settles on the new domain
    assert eng.snapshot()["error_ms"] == pytest.approx(1.0, abs=1.5)
    assert eng.snapshot()["air_bias_ms"] == pytest.approx(28.0, abs=3.0)


def test_a_held_underrun_baseline_is_folded_into_the_bias_on_resume(caplog):
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 2.0)
    feed.set_lock("no_signal", None)
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["air_bias_state"] == "held"
    b_before = eng.snapshot()["air_bias_ms"]
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        underrun(eng, port, raw, slew_ms=-12.0)
        drive(eng, port, raw, 12.0)
    assert any("error baseline" in m for m in messages(caplog))  # today's rules on c while held
    assert eng._baseline_us == pytest.approx(12_000.0, abs=2_000.0)
    feed.set_lock("locked", 1.0)
    _, push = windows(feed, eng, raw, 1_000.0, n=1)
    drive(eng, port, raw, 3.0)
    push()
    drive(eng, port, raw, 1.0)
    assert eng._baseline_us == 0.0
    assert eng.snapshot()["air_bias_ms"] == pytest.approx(b_before + 12.0, abs=1.6)


def _failing_engine(caplog):
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 3.0)
    assert eng.snapshot()["air_bias_state"] == "active"
    return eng, port, raw


def _boom(*_a, **_k):
    raise RuntimeError("boom")


@pytest.mark.parametrize("target", ["bias_update", "history_add"])
def test_an_internal_error_disables_the_bias_without_moving_the_audio(caplog, monkeypatch, target):
    eng, port, raw = _failing_engine(caplog)
    before = eng.snapshot()["error_ms"]
    if target == "bias_update":
        monkeypatch.setattr(eng._bias, "update", _boom)
    else:
        monkeypatch.setattr(eng._history, "add", _boom)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 3.0)
        snap = eng.snapshot()
        assert snap["air_bias_state"] == "none"
        assert snap["air_bias_reason"] == "internal_error"
        assert snap["state"] == "playing"
        assert snap["error_ms"] == pytest.approx(before, abs=1.5)
        assert eng._bias is None and eng._baseline_us == 0.0
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and warnings[0].exc_info is not None
        eng._disable_air("internal_error")  # a second failure within 10 min logs nothing new
        drive(eng, port, raw, 3.0)
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert not [m for m in messages(caplog) if "steady" in m]


def test_disabling_the_bias_drops_a_pending_air_jump(caplog):
    from sendspin_bridge.services.audio.playout.air_bias import AirAction

    eng, _port, _raw = _failing_engine(caplog)
    eng._pending_air = AirAction(jump_us=37_000.0, reason="air_seed")
    eng._disable_air("internal_error")
    assert eng._pending_air is None


# -- B5b: air jumps, events -------------------------------------------------------------------

TONE = b"\x10\x00" * 2


def test_three_samples_at_plus_37_ms_make_one_air_seed_jump(caplog):
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        seed(eng, port, raw, feed, 37_000.0)
        drive(eng, port, raw, 3.0)
    snap = eng.snapshot()
    assert snap["last_jump_reason"] == "air_seed"
    assert snap["air_corrections"] == 1
    assert snap["last_jump_ms"] == pytest.approx(37.0, abs=0.05)
    lines = [m for m in messages(caplog) if "air_seed" in m]
    assert len(lines) == 1 and "air_seed, 37.0 ms" in lines[0]
    assert not [m for m in messages(caplog) if "clamped" in m]
    assert snap["error_ms"] == pytest.approx(0.0, abs=1.5)
    assert snap["air_bias_state"] == "active"


def test_an_air_action_while_a_placement_is_pending_is_dropped(caplog):
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    _, push = windows(feed, eng, raw, 37_000.0)
    drive(eng, port, raw, 6.5)
    eng.clear()
    eng.submit(int(raw.t_us + 1e6), ZERO * FS * 60)
    push()
    eng.sense(raw.t_us, port.delivered_ns)
    assert eng._pending_jump_us is None
    snap = eng.snapshot()
    assert snap["air_corrections"] == 0  # only executed corrections count
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 15.0)
    snap = eng.snapshot()
    # The placement itself carries the correction: it aligns to audible - o with o = b, so the audio
    # moves by the 37 ms the air saw, and the c-domain error ends at 0.
    assert any("jump: clear, 37.0 ms" in m for m in messages(caplog))
    assert snap["error_ms"] == pytest.approx(0.0, abs=1.5)


def marker_run(change_us, caplog=None):
    feed = make_feed()
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="loop"), port=port, air_feed=feed)
    eng.set_format(InputFormat(sample_rate=FS))
    start = raw.t_us + 1e6
    eng.submit(int(start), ZERO * FS * 40)
    due = start + 40e6
    eng.submit(int(due), TONE * FS)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 3.0)
    bias_before = eng.snapshot()["air_bias_ms"]
    if change_us:
        eng.resync(change_us)
    drive(eng, port, raw, 40.0)
    return eng, port, due, bias_before


def test_a_delay_change_moves_the_marker_exactly_and_leaves_the_bias(caplog):
    _, base_port, _, _ = marker_run(0)
    with caplog.at_level(logging.INFO):
        eng, port, _, bias_before = marker_run(-30_000)
    assert base_port.heard_at_us(first_nonzero=True) - port.heard_at_us(first_nonzero=True) == pytest.approx(
        30_000, abs=500
    )
    snap = eng.snapshot()
    assert snap["air_bias_ms"] == bias_before
    assert snap["air_corrections"] == 0
    assert snap["last_jump_reason"] != "air_seed"
    assert [m for m in messages(caplog) if "Output delay changed" in m]


def test_a_format_change_reseeds_after_three_new_samples():
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 2.0)
    assert eng.snapshot()["air_bias_state"] == "active"
    eng.set_format(InputFormat(sample_rate=FS, channels=2, sample_bytes=3))
    assert eng.snapshot()["air_bias_state"] == "seeding"
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["air_bias_state"] == "seeding"
    seed(eng, port, raw, feed, 1_000.0)
    assert eng.snapshot()["air_bias_state"] == "active"


def composed_run(scenario):
    """A delay change of +30 ms and an air jump decided before one render, in either order.

    ``air_first``: the seed (+37 ms) is decided, then the delay change arrives. ``delay_first``: the
    delay change arrives, then a target change (+20 ms) is decided (a delay event excludes the windows
    around it, so a seed cannot follow it at once). ``none``: a dead-band seed only.
    """
    feed = make_feed()
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="loop"), port=port, air_feed=feed)
    eng.set_format(InputFormat(sample_rate=FS))
    start = raw.t_us + 1e6
    eng.submit(int(start), ZERO * FS * 40)
    eng.submit(int(start + 40e6), TONE * FS)
    drive(eng, port, raw, 4.0)
    _, push = windows(feed, eng, raw, 37_000.0 if scenario == "air_first" else 1_000.0)
    drive(eng, port, raw, 6.5)
    push()
    eng.sense(raw.t_us, port.delivered_ns)
    if scenario == "air_first":
        eng.resync(-30_000)
    elif scenario == "delay_first":
        drive(eng, port, raw, 1.0)
        eng.resync(-30_000)
        feed.set_target(latency_ms=LATENCY_MS + 20.0, d_ref_ms=D_REF_MS)
        eng.sense(raw.t_us, port.delivered_ns)
    if scenario != "none":
        assert eng._pending_air is not None and eng._pending_jump_us is not None
    drive(eng, port, raw, 40.0)
    return eng, port


@pytest.mark.parametrize(("scenario", "total_ms"), [("air_first", 67.0), ("delay_first", 50.0)])
def test_a_delay_change_and_an_air_jump_before_one_render_both_apply(scenario, total_ms):
    _, base = composed_run("none")
    eng, port = composed_run(scenario)
    heard = base.heard_at_us(first_nonzero=True) - port.heard_at_us(first_nonzero=True)
    assert heard == pytest.approx(total_ms * 1000.0, abs=700)  # delay and air, exactly
    assert eng.snapshot()["air_corrections"] == 1


def test_a_format_that_equals_the_previous_one_does_not_reseed():
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 2.0)
    eng.set_format(InputFormat(sample_rate=FS))
    assert eng.snapshot()["air_bias_state"] == "active"


def test_a_placement_after_the_air_action_was_decided_undoes_its_count():
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    _, push = windows(feed, eng, raw, 37_000.0)
    drive(eng, port, raw, 6.5)
    push()
    eng.sense(raw.t_us, port.delivered_ns)
    assert eng.snapshot()["air_corrections"] == 1 and eng._pending_air is not None
    eng.clear()  # the placement re-aligns; the decided air jump never happens
    eng.submit(int(raw.t_us + 1e6), ZERO * FS * 60)
    drive(eng, port, raw, 2.0)
    snap = eng.snapshot()
    assert snap["air_corrections"] == 0
    assert snap["last_jump_reason"] != "air_seed"


def test_the_air_trim_follows_a_delay_change():
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    assert eng.snapshot()["air_trim_ms"] == pytest.approx(0.0)
    eng.resync(-30_000)
    assert eng.snapshot()["air_trim_ms"] == pytest.approx(30.0)


def test_the_published_error_right_after_a_resume_drops_the_held_baseline():
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    seed(eng, port, raw, feed, 1_000.0)
    drive(eng, port, raw, 2.0)
    feed.set_lock("no_signal", None)
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["air_bias_state"] == "held"
    underrun(eng, port, raw, slew_ms=-30.0)
    drive(eng, port, raw, 12.0)
    assert eng._baseline_us == pytest.approx(30_000.0, abs=3_000.0)
    feed.set_lock("locked", 1.0)
    _, push = windows(feed, eng, raw, 1_000.0)
    drive(eng, port, raw, 6.5)
    push()
    eng.sense(raw.t_us, port.delivered_ns)
    snap = eng.snapshot()
    assert eng._baseline_us == 0.0
    assert snap["air_bias_state"] == "active"
    assert snap["error_ms"] == pytest.approx(1.0, abs=1.5)  # the 30 ms baseline moved into b
    assert snap["air_bias_ms"] == pytest.approx(29.0, abs=1.5)


def test_a_reseed_with_an_existing_bias_rebases_the_published_error():
    feed = make_feed()
    eng, port, raw = engine(feed=feed)
    drive(eng, port, raw, 4.0)
    port.skip(-6.0)
    drive(eng, port, raw, 3.0)
    seed(eng, port, raw, feed, 1_000.0)  # b = 5 ms
    drive(eng, port, raw, 3.0)
    eng.set_format(InputFormat(sample_rate=FS, channels=2, sample_bytes=3))
    drive(eng, port, raw, 1.5)  # the port passes the format change
    _, push = windows(feed, eng, raw, 2_000.0)
    drive(eng, port, raw, 6.5)
    push()
    eng.sense(raw.t_us, port.delivered_ns)
    snap = eng.snapshot()
    assert snap["air_bias_state"] == "active"
    assert snap["air_bias_ms"] == pytest.approx(4.0, abs=1.5)
    assert snap["error_ms"] == pytest.approx(2.0, abs=1.0)  # the air's new reading, not 2 - 5
