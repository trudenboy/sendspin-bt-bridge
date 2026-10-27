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
