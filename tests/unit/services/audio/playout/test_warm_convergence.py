"""Warm-up ends when the sync has converged (warm_reason "converged"), not only on the clock."""

import pytest

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

FS = 48000
ZERO = b"\x00\x00" * 2


def engine(mode="measure", *, warmup_s=60.0):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode=mode, warmup_s=warmup_s), port=port)
    eng.set_format(InputFormat(sample_rate=FS))
    eng.submit(int(raw.t_us + 1e6), ZERO * FS * 120)
    return eng, port, raw


@pytest.mark.parametrize("mode", ["measure", "loop"])
def test_a_steady_stream_is_warm_long_before_the_timeout(mode):
    eng, port, raw = engine(mode)
    assert eng.snapshot()["warm_reason"] is None
    drive(eng, port, raw, 20.0)
    snap = eng.snapshot()
    assert snap["warm"] is True and snap["warm_reason"] == "converged"
    assert snap["warm_s"] < snap["warmup_s"]
    assert snap["settling"] is False


def test_not_warm_before_the_first_readings_have_held_for_three_seconds():
    eng, port, raw = engine()
    drive(eng, port, raw, 1.5)
    snap = eng.snapshot()
    assert snap["warm"] is False and snap["warm_reason"] is None


def test_the_clock_is_the_timeout_when_the_error_never_settles():
    eng, port, raw = engine(warmup_s=8.0)
    # keep the engine from ever calling the error in tolerance
    eng._error_ema_us = 0.0
    orig = eng._error_in_tolerance
    eng._error_in_tolerance = lambda: False  # type: ignore[method-assign]
    drive(eng, port, raw, 5.0)
    assert eng.snapshot()["warm"] is False
    drive(eng, port, raw, 6.0)
    snap = eng.snapshot()
    assert snap["warm"] is True and snap["warm_reason"] == "timeout"
    del orig


def test_a_short_underrun_keeps_the_stream_warm():
    eng, port, raw = engine()
    drive(eng, port, raw, 15.0)
    assert eng.snapshot()["warm"] is True
    warm_s = eng.snapshot()["warm_s"]
    port.starve(650.0)  # ~10 ms of silence, well under 1 s
    drive(eng, port, raw, 8.0)
    snap = eng.snapshot()
    assert snap["underruns"] == 1
    assert snap["warm"] is True and snap["warm_reason"] == "converged"
    assert snap["warm_s"] > warm_s  # the clock was not restarted


def test_a_long_stall_restarts_the_warm_clock():
    eng, port, raw = engine()
    drive(eng, port, raw, 15.0)
    assert eng.snapshot()["warm"] is True
    port.stall(2_500.0)
    drive(eng, port, raw, 4.0)
    snap = eng.snapshot()
    assert snap["warm"] is False and snap["warm_reason"] is None
    assert snap["warm_s"] < 8.0


def test_settling_is_out_of_tolerance_error_or_a_jump_in_this_period():
    eng, port, raw = engine()
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["settling"] is False  # the start placement is not a reason
    eng._error_ema_us = 50_000.0
    assert eng.snapshot()["settling"] is True
    eng._error_ema_us = 0.0
    eng._record_event(15_000.0, "steady")
    assert eng.snapshot()["settling"] is True


def test_loop_with_an_air_bias_waits_for_the_seed():
    from tests.unit.services.audio.playout.test_engine_air import engine as air_engine
    from tests.unit.services.audio.playout.test_engine_air import make_feed, windows

    feed = make_feed()
    eng, port, raw = air_engine(feed=feed)
    drive(eng, port, raw, 1.0)
    _, push = windows(feed, eng, raw, 1_000.0, n=3)
    drive(eng, port, raw, 5.5)
    snap = eng.snapshot()
    assert snap["air_bias_state"] != "active"
    assert snap["warm"] is False  # converged for >3 s, but the bias is not seeded
    push()
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["air_bias_state"] == "active"
    drive(eng, port, raw, 4.0)
    snap = eng.snapshot()
    assert snap["warm"] is True and snap["warm_reason"] == "converged"
    assert snap["warm_s"] < 20.0
