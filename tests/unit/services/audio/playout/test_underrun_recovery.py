"""After an underrun or a stall the audio is heard on its schedule again (R3).

The port plays silence from the moment the ring runs dry until our data arrives, and the data
lands where the port is then. The engine must see all of that silence, also the part played
after its last underrun reading (or an underrun that begins and ends between two readings),
else the recovery leaves the audio late by it while the error reads 0.
"""

import pytest

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

FS = 48000
ZERO = b"\x00\x00" * 2  # program audio the simulated port does not count as heard
TONE = b"\x10\x00" * 2


def engine(mode="measure"):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode=mode), port=port)
    eng.set_format(InputFormat(sample_rate=FS))
    return eng, port, raw


def zeros_then_marker(eng, raw, *, zero_s):
    start = raw.t_us + 1e6
    eng.submit(int(start), ZERO * int(FS * zero_s))
    due = start + zero_s * 1e6
    eng.submit(int(due), TONE * FS)
    return due


# 600 ms ring + 40 ms appsrc: 800 ms starves the port ~160 ms (past several readings); 650 ms
# ~10 ms, between two readings.
@pytest.mark.parametrize("mode", ["measure", "loop"])
@pytest.mark.parametrize("starve_ms", [800.0, 650.0, 730.0])
def test_after_an_underrun_the_audio_is_heard_on_schedule(mode, starve_ms):
    eng, port, raw = engine(mode)
    due = zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 12.0)
    port.starve(starve_ms)
    drive(eng, port, raw, 8.0)
    assert port.underrun_ns > 5e6
    snap = eng.snapshot()
    assert snap["underruns"] == 1 and snap["last_jump_reason"] == "underrun"
    assert abs(snap["error_ms"]) < 1.0
    drive(eng, port, raw, 12.0)
    heard = port.heard_at_us(first_nonzero=True)
    assert heard is not None
    assert heard - due == pytest.approx(0.0, abs=1_000)  # today: late by the unseen silence


@pytest.mark.parametrize("stall_ms", [500.0, 1_250.0])
def test_after_a_stall_the_audio_is_heard_on_schedule(stall_ms):
    eng, port, raw = engine()
    due = zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 12.0)
    port.stall(stall_ms)
    drive(eng, port, raw, 8.0)
    snap = eng.snapshot()
    assert snap["last_jump_reason"] == "stall"
    assert abs(snap["error_ms"]) < 1.0
    drive(eng, port, raw, 12.0)
    heard = port.heard_at_us(first_nonzero=True)
    assert heard is not None
    assert heard - due == pytest.approx(0.0, abs=1_000)
