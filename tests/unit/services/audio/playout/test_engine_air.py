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
