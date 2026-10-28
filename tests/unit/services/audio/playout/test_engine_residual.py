"""Q6 and I1: placements keep the residual; a delay change is a frame-exact jump of its delta."""

import logging
import re

import pytest

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

FS = 48000
ZERO = b"\x00\x00" * 2  # program audio the simulated port does not count as heard
TONE = b"\x10\x00" * 2
ENGINE_LOGGER = "sendspin_bridge.services.audio.playout.engine"
_JUMP = re.compile(r"playout jump: (\w+), (-?[\d.]+) ms")


def engine(mode="measure", **port_kw):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, **port_kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode=mode), port=port)
    eng.set_format(InputFormat(sample_rate=FS))
    return eng, port, raw


def zeros_then_marker(eng, raw, *, zero_s, gap_us=0.0):
    """Program silence for ``zero_s`` from 1 s from now, then a tone (``gap_us`` after it)."""
    start = raw.t_us + 1e6
    eng.submit(int(start), ZERO * int(FS * zero_s))
    due = start + zero_s * 1e6 + gap_us
    eng.submit(int(due), TONE * FS)
    return due


def drive_until(eng, port, raw, done):
    for _ in range(100):
        drive(eng, port, raw, 0.1)
        if done(eng.snapshot()):
            return
    raise AssertionError("the event did not happen")


def jumps(caplog):
    return [(m.group(1), float(m.group(2))) for r in caplog.records if (m := _JUMP.search(r.getMessage()))]


# The stand: +30 ms moved ENEBY20 by -43.6 ms because e_w = +13.4 ms was zeroed. In loop the
# residual must stay inside J = 10 ms, else a steady jump removes it before the change.
@pytest.mark.parametrize(("mode", "residual_ms"), [("measure", 13.4), ("loop", 7.0)])
@pytest.mark.parametrize("delta_ms", [30.0, -30.0])
def test_delay_change_moves_the_audio_by_exactly_its_delta(caplog, mode, residual_ms, delta_ms):
    eng, port, raw = engine(mode)
    due = zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 5.0)
    port.skip(-residual_ms)  # the port falls behind: heard late by the residual, inside the dead band
    drive(eng, port, raw, 8.0)
    assert eng.snapshot()["error_ms"] == pytest.approx(residual_ms, abs=1.5)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        assert eng.resync(int(-delta_ms * 1000)) is True  # output delay +ΔD: the queued audio plays ΔD earlier
        drive(eng, port, raw, 2.0)
    assert ("delay", pytest.approx(delta_ms, abs=0.05)) in jumps(caplog)  # a jump of ΔD, not a placement
    assert eng.snapshot()["error_ms"] == pytest.approx(residual_ms, abs=1.5)  # kept, not zeroed
    drive(eng, port, raw, 23.0)
    heard = port.heard_at_us(first_nonzero=True)
    assert heard is not None
    assert heard - (due - delta_ms * 1000) == pytest.approx(residual_ms * 1000, abs=500)  # today: ≈ 0


def test_the_delay_jump_does_not_read_the_noisy_port():
    def heard_late_us(resync_us):
        eng, port, raw = engine("measure", noise_us=4000.0)  # sigma 4 ms per reading, PulseAudio 17
        due = zeros_then_marker(eng, raw, zero_s=30.0)
        drive(eng, port, raw, 12.0)
        if resync_us:
            eng.resync(resync_us)
        drive(eng, port, raw, 25.0)
        heard = port.heard_at_us(first_nonzero=True)
        assert heard is not None
        return heard - due

    # The start placement already read the noisy port (the same seed lands it the same way in
    # both runs); the delay jump adds exactly -30 ms to that, where a placement would land on the
    # 9-reading median (±1.3 ms).
    assert heard_late_us(-30_000) == pytest.approx(heard_late_us(0) - 30_000, abs=200)


def gap_after_an_underrun(caplog, *, slew_ms):
    """Heard-late of a marker after a 50 ms gap that follows an underrun, and the engine."""
    eng, port, raw = engine("measure")
    due = zeros_then_marker(eng, raw, zero_s=40.0, gap_us=50_000)  # a 50 ms gap: a "gap" placement
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        port.starve(800.0)
        drive_until(eng, port, raw, lambda s: s["underruns"] >= 1 and s["last_jump_reason"] == "underrun")
        if slew_ms:
            port.slew(slew_ms, 300.0)
        drive(eng, port, raw, 40.0)
    heard = port.heard_at_us(first_nonzero=True)
    assert heard is not None
    return heard - due, eng


def test_a_gap_after_an_underrun_baseline_does_not_move_the_audio(caplog):
    # Reference: the same run without the reading artefact. Where the underrun placement left
    # the audio is the observer's business (the silence played between the last underrun reading
    # and the data's arrival is not seen); the gap must not move it from there.
    reference_us, _ = gap_after_an_underrun(caplog, slew_ms=0.0)
    caplog.clear()
    # The reading settles 30 ms behind the port: an artefact, the audio does not move.
    heard_us, eng = gap_after_an_underrun(caplog, slew_ms=-30.0)
    reasons = [r for r, _ in jumps(caplog)]
    assert "gap" in reasons and "steady" not in reasons
    assert eng.snapshot()["error_ms"] == pytest.approx(30.0, abs=3.0)  # the baseline is still reported
    assert heard_us == pytest.approx(reference_us, abs=1_000)  # today: ≈ 30 ms earlier


def test_a_clear_keeps_the_baseline():
    eng, port, raw = engine("measure")
    zeros_then_marker(eng, raw, zero_s=60.0)
    drive(eng, port, raw, 12.0)
    port.starve(800.0)
    drive_until(eng, port, raw, lambda s: s["underruns"] >= 1 and s["last_jump_reason"] == "underrun")
    port.slew(-30.0, 300.0)
    drive(eng, port, raw, 12.0)
    eng.clear()
    eng.submit(int(raw.t_us + 1e6), ZERO * FS * 20)
    drive(eng, port, raw, 15.0)
    snap = eng.snapshot()
    assert snap["last_jump_reason"] in ("clear", "starved")
    assert snap["error_ms"] == pytest.approx(30.0, abs=3.0)  # today: the clear zeroes it (and moves the audio 30 ms)


def test_resync_zero_is_still_a_placement():
    eng, port, raw = engine("measure")
    zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 5.0)
    eng.resync(0)
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["last_jump_reason"] == "delay"


def test_two_delay_changes_before_a_render_add_up(caplog):
    eng, port, raw = engine("measure")
    due = zeros_then_marker(eng, raw, zero_s=30.0)
    drive(eng, port, raw, 12.0)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        eng.resync(-20_000)
        eng.resync(-10_000)
        drive(eng, port, raw, 2.0)
    assert [j for j in jumps(caplog) if j[0] == "delay"] == [("delay", pytest.approx(30.0, abs=0.05))]
    drive(eng, port, raw, 23.0)
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due - 30_000, abs=200)
