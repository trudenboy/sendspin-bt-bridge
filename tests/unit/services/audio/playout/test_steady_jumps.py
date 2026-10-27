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
    for _ in range(100):
        drive(eng, port, raw, 0.1)
        snap = eng.snapshot()
        if snap["underruns"] >= n and snap["last_jump_reason"] == "underrun":
            if then is not None:
                then()
            return
    raise AssertionError("underrun was not placed")


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


# --- 2. a steady correction has an upper bound ----------------------------------------------


def test_steady_correction_over_the_bound_is_refused_and_logged(caplog):
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)
    before = eng.snapshot()
    port.skip(-100.0)  # the port falls 100 ms behind: far beyond a residual
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 20.0)
    snap = eng.snapshot()
    assert steady_jumps(caplog) == []
    assert snap["jumps"] == before["jumps"]
    refused = [r for r in caplog.records if r.levelno == logging.WARNING and "refused" in r.getMessage()]
    assert len(refused) == 1 and "100." in refused[0].getMessage()
    assert snap["state"] == "degraded"


# --- 3. after an underrun the error restarts from a fresh baseline --------------------------


def test_two_underruns_then_a_clock_settling_back_give_no_459_ms_jump(caplog):
    # Live: underrun 201 ms, underrun 62 ms, then "steady, 459.0 ms". The reported clock ran
    # ahead while the port was starved and converges back once data flows again.
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
    # Not merely refused by the bound: the offset is the post-underrun baseline.
    assert not [r for r in caplog.records if "refused" in r.getMessage()]


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
