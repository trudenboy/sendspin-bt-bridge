"""Steady jumps against sensor artefacts seen live on a desktop PipeWire sink (#556)."""

import logging
import re

import pytest

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

ENGINE_LOGGER = "sendspin_bridge.services.audio.playout.engine"
TONE_FRAME = b"\x10\x00" * 2
_STEADY = re.compile(r"playout jump: steady, (-?[\d.]+) ms")


def playing(seconds, **port_kw):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, **port_kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=port)
    eng.set_format(InputFormat(sample_rate=48000))
    eng.submit(int(raw.t_us + 1e6), TONE_FRAME * int(48000 * seconds))
    return eng, port, raw


def steady_jumps(caplog):
    return [float(m.group(1)) for r in caplog.records if (m := _STEADY.search(r.getMessage()))]


def drive_until_underruns(eng, port, raw, n, *, then=None):
    """Drive until the engine has placed ``n`` underruns in total; call ``then`` right after."""
    drive_until(eng, port, raw, lambda s: s["underruns"] >= n and s["last_jump_reason"] == "underrun", then)


def drive_until(eng, port, raw, done, then=None):
    for _ in range(100):
        drive(eng, port, raw, 0.1)
        if done(eng.snapshot()):
            if then is not None:
                then()
            return
    raise AssertionError("the placement did not happen")


# --- 1. a periodic sawtooth on the port clock is not an error to correct --------------------


@pytest.mark.parametrize(("amplitude_ms", "period_s"), [(25.0, 3.0), (27.0, 6.0), (27.0, 15.0)])
def test_clock_sawtooth_without_drift_gives_no_steady_jump(caplog, amplitude_ms, period_s):
    eng, port, raw = playing(80.0)
    drive(eng, port, raw, 2.0)
    port.sawtooth(amplitude_ms, period_s)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 60.0)
    assert steady_jumps(caplog) == []
    assert eng.snapshot()["underruns"] == 0


@pytest.mark.parametrize("delta_ppm", [-2000.0, -3500.0])  # 2 and 3.5 ms/s late, as in #546
def test_real_drift_is_still_corrected_by_steady_jumps(caplog, delta_ppm):
    eng, port, raw = playing(70.0, delta_ppm=delta_ppm)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 60.0)
    jumps = steady_jumps(caplog)
    assert len(jumps) >= 3
    assert all(20.0 < j <= 50.0 for j in jumps)
    assert abs(eng.snapshot()["error_ms"]) < 50.0


# --- 2. a steady correction is clamped to a bound -------------------------------------------


def errors_while(eng, port, raw, seconds):
    seen = []
    drive(eng, port, raw, seconds, on_sense=lambda: seen.append(eng.snapshot()["error_ms"]))
    return [e for e in seen if e is not None]


def test_steady_correction_over_the_bound_is_clamped_and_logged(caplog):
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)
    port.skip(-100.0)  # the port falls 100 ms behind: corrected in two clamped steps
    states = []
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 25.0, on_sense=lambda: states.append(eng.snapshot()["state"]))
    snap = eng.snapshot()
    # The second step waiting for the 10 s interval is expected: no WARNING, never degraded.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert set(states) == {"playing"}
    assert steady_jumps(caplog) == [pytest.approx(50.0), pytest.approx(50.0, abs=1.0)]
    clamped = [r for r in caplog.records if r.levelno == logging.INFO and "clamped" in r.getMessage()]
    assert clamped and "100." in clamped[0].getMessage() and "50." in clamped[0].getMessage()
    assert abs(snap["error_ms"]) < 1.0
    assert snap["state"] == "playing"


def test_persistent_100_ms_error_after_start_is_corrected_within_25_s(caplog):
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 1.5)  # the start placement is done
    port.skip(-100.0)
    states = []
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 23.5, on_sense=lambda: states.append(eng.snapshot()["state"]))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert set(states) == {"playing"}
    assert abs(eng.snapshot()["error_ms"]) < 20.0
    assert eng.snapshot()["state"] == "playing"
    assert sum(steady_jumps(caplog)) == pytest.approx(100.0, abs=1.0)


def test_60_ms_pa_skip_is_corrected(caplog):
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)
    port.skip(60.0)  # PA dropped 60 ms: heard 60 ms early
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 25.0)
    assert steady_jumps(caplog)[0] == pytest.approx(-50.0)
    assert abs(eng.snapshot()["error_ms"]) < 20.0


def test_drift_with_a_clock_sawtooth_does_not_run_away():
    eng, port, raw = playing(130.0, delta_ppm=-3500.0)
    drive(eng, port, raw, 2.0)
    port.sawtooth(30.0, 15.0)
    errors = errors_while(eng, port, raw, 120.0)
    assert max(abs(e) for e in errors) < 60.0
    assert eng.snapshot()["state"] == "playing"


# --- 3. after an underrun the error restarts from a fresh baseline --------------------------


def test_two_underruns_then_a_clock_settling_back_give_no_459_ms_jump(caplog):
    """Live: underrun 201 ms, underrun 62 ms, then "steady, 459.0 ms", and the microphone found
    the player early afterwards. Modelled here as the reported clock, ahead while the port was
    starved, converging back once data flows again: a hypothesis consistent with the microphone
    finding (the jump moved audio that was on time), not a measured mechanism.
    """
    eng, port, raw = playing(60.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(840.0)
        drive_until_underruns(eng, port, raw, 1)
        drive(eng, port, raw, 0.5)
        port.starve(700.0)
        drive_until_underruns(eng, port, raw, 2, then=lambda: port.slew(-459.0, 600.0))
        drive(eng, port, raw, 20.0)
    snap = eng.snapshot()
    assert snap["underruns"] == 2
    assert steady_jumps(caplog) == []
    assert not [r for r in caplog.records if "clamped" in r.getMessage()]
    # A baseline over 50 ms is reported (WARNING, degraded), not corrected (R3).
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "baseline" in r.getMessage()]
    assert len(warned) == 1 and "459." in warned[0].getMessage()
    assert snap["state"] == "degraded"


def test_offset_left_by_an_underrun_is_the_new_baseline_and_later_steps_are_corrected(caplog):
    eng, port, raw = playing(60.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until_underruns(eng, port, raw, 1, then=lambda: port.slew(-30.0, 300.0))
        drive(eng, port, raw, 15.0)
        assert steady_jumps(caplog) == []  # within the bound, but left by the underrun (R3)
        assert eng.snapshot()["error_ms"] == pytest.approx(30.0, abs=3.0)  # still reported
        port.skip(-25.0)  # a real step after the baseline is corrected as before
        drive(eng, port, raw, 12.0)
    assert steady_jumps(caplog) == [pytest.approx(25.0, abs=1.0)]


def test_a_placement_while_the_baseline_is_measured_does_not_cancel_it(caplog):
    eng, port, raw = playing(60.0)
    drive(eng, port, raw, 12.0)

    def delay_then_settle():
        eng.resync(0)  # a delay change right after the underrun placement
        drive(eng, port, raw, 0.2)
        assert eng.snapshot()["last_jump_reason"] == "delay"
        port.slew(-30.0, 300.0)

    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until_underruns(eng, port, raw, 1, then=delay_then_settle)
        drive(eng, port, raw, 15.0)
    assert steady_jumps(caplog) == []


def test_slow_settle_after_an_underrun_gives_no_jump(caplog):
    # The reading settles by 60 ms over 4 s: a baseline taken before it is stable would be
    # ~22 ms and leave a false ~38 ms error to "correct".
    eng, port, raw = playing(60.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until_underruns(eng, port, raw, 1, then=lambda: port.slew(-60.0, 4000.0))
        drive(eng, port, raw, 25.0)
    assert steady_jumps(caplog) == []
    assert eng.snapshot()["error_ms"] == pytest.approx(60.0, abs=3.0)


def test_transient_offset_after_an_underrun_that_clears_later_gives_no_jump(caplog):
    eng, port, raw = playing(70.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until_underruns(eng, port, raw, 1, then=lambda: port.slew(-30.0, 300.0))
        drive(eng, port, raw, 10.0)  # baseline 30 ms
        port.slew(30.0, 1000.0)  # the artefact clears: the reading is back on the audio
        drive(eng, port, raw, 25.0)
    assert steady_jumps(caplog) == []
    assert abs(eng.snapshot()["error_ms"]) < 3.0


def test_offset_left_by_a_stall_is_a_baseline_too(caplog):
    eng, port, raw = playing(60.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.stall(600.0)
        drive_until(eng, port, raw, lambda s: s["last_jump_reason"] == "stall", lambda: port.slew(-30.0, 300.0))
        drive(eng, port, raw, 20.0)
    assert steady_jumps(caplog) == []
    assert eng.snapshot()["error_ms"] == pytest.approx(30.0, abs=3.0)


@pytest.mark.parametrize(("baseline_ms", "skip_ms", "expected"), [(30.0, 60.0, [-50.0]), (2.0, 40.0, [-40.0])])
def test_real_step_against_the_baseline_sign_is_corrected(caplog, baseline_ms, skip_ms, expected):
    # The raw error stays far from the reading's zero, so this is a real step, not the
    # baseline's offset clearing: it is corrected (clamped), not absorbed as a new baseline.
    eng, port, raw = playing(70.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until_underruns(eng, port, raw, 1, then=lambda: port.slew(-baseline_ms, 300.0))
        drive(eng, port, raw, 12.0)
        port.skip(skip_ms)  # PA dropped audio: heard early
        drive(eng, port, raw, 12.0)
    assert steady_jumps(caplog) == [pytest.approx(e, abs=1.0) for e in expected]
    assert not [r for r in caplog.records if "re-measur" in r.getMessage()]


def test_a_re_measured_small_baseline_clears_the_degraded_state(caplog):
    eng, port, raw = playing(70.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until_underruns(eng, port, raw, 1, then=lambda: port.slew(-80.0, 300.0))
        drive(eng, port, raw, 10.0)
        assert eng.snapshot()["state"] == "degraded"  # baseline 80 ms
        port.slew(80.0, 1000.0)  # the artefact clears; the baseline is re-measured near 0
        drive(eng, port, raw, 25.0)
    snap = eng.snapshot()
    assert steady_jumps(caplog) == []
    assert snap["state"] == "playing"
    assert any("re-measured" in r.getMessage() for r in caplog.records)
