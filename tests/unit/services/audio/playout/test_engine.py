import pytest

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive


def engine_with_port(**port_kw):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, **port_kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=port)
    eng.set_format(InputFormat(sample_rate=48000))
    return eng, port, raw


def test_error_is_near_zero_with_an_ideal_port():
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 5.0)
    snap = eng.snapshot()
    assert abs(snap["error_ms"]) < 0.1 and snap["jumps"] >= 1  # the start placement


def test_measure_mode_reports_a_rate_error_as_a_growing_error():
    eng, port, raw = engine_with_port(delta_ppm=-200.0)  # port 200 ppm slow → later and later
    drive(eng, port, raw, 30.0)
    snap = eng.snapshot()
    assert snap["error_slope_ms_per_s"] == pytest.approx(0.2, rel=0.2)
    assert snap["correction_ppm"] == 0.0


def test_a_submitted_chunk_is_heard_at_its_play_time():
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 2.0)
    due = raw.t_us + 1_500_000
    eng.submit(int(due), b"\x10\x00" * 2 * 4800)
    drive(eng, port, raw, 2.0)
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due, abs=50)


# --- simulation: placements, PA steps, stalls and steady jumps --------------------------

TONE_FRAME = b"\x10\x00" * 2


def playing(seconds=30.0, *, after_s=1.0, **port_kw):
    """Engine and port with one long tone chunk due ``after_s`` from now."""
    eng, port, raw = engine_with_port(**port_kw)
    eng.submit(int(raw.t_us + after_s * 1e6), TONE_FRAME * int(48000 * seconds))
    return eng, port, raw


def test_a_chunk_queued_before_the_first_reading_is_placed_once_for_start():
    eng, port, raw = playing()
    drive(eng, port, raw, 3.0)
    snap = eng.snapshot()
    assert (snap["jumps"], snap["last_jump_reason"]) == (1, "start")
    assert abs(snap["error_ms"]) < 0.1 and snap["error_p95_ms"] < 0.1


def test_underrun_places_once_and_the_error_returns_to_zero():
    eng, port, raw = playing()
    drive(eng, port, raw, 3.0)
    before = eng.snapshot()
    port.starve(800.0)  # 600 ms ring + 40 ms appsrc → ~160 ms of underrun
    drive(eng, port, raw, 3.0)
    snap = eng.snapshot()
    assert port.underrun_ns > 100e6
    assert snap["underruns"] == 1
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == "underrun"
    assert snap["k_ns"] > before["k_ns"]
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0
    assert snap["warm_s"] < 3.0  # reset by the underrun
    assert snap["state"] == "playing"


def test_pa_skip_is_a_pa_step_without_a_k_change():
    eng, port, raw = playing()
    drive(eng, port, raw, 3.0)
    before = eng.snapshot()
    port.skip(18.0)  # PA dropped 18 ms of our frames: heard 18 ms early, below the 20 ms jump
    drive(eng, port, raw, 5.0)
    snap = eng.snapshot()
    assert snap["pa_steps"] == 1
    assert snap["k_ns"] == before["k_ns"] and snap["underruns"] == 0
    assert snap["jumps"] == before["jumps"]
    assert snap["error_ms"] == pytest.approx(-18.0, abs=1.0)


def test_small_steps_are_not_pa_steps():
    eng, port, raw = playing()
    drive(eng, port, raw, 3.0)
    port.skip(5.0)
    drive(eng, port, raw, 2.0)
    assert eng.snapshot()["pa_steps"] == 0


def test_stall_suspends_and_places_on_recovery():
    eng, port, raw = playing()
    drive(eng, port, raw, 3.0)
    before = eng.snapshot()
    port.stall(600.0)
    drive(eng, port, raw, 0.5)
    assert eng.snapshot()["state"] == "suspended"
    drive(eng, port, raw, 3.0)
    snap = eng.snapshot()
    assert snap["state"] == "playing"
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == "stall"
    assert snap["underruns"] == 0 and snap["pa_steps"] == 0
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0
    assert snap["warm_s"] < 3.0


def test_persistent_late_offset_gives_one_steady_jump():
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)  # > 10 s after the start placement
    before = eng.snapshot()
    port.skip(-25.0)  # the port falls 25 ms behind: heard 25 ms late
    drive(eng, port, raw, 14.0)
    snap = eng.snapshot()
    assert snap["jumps"] == before["jumps"] + 1
    assert snap["last_jump_reason"] == "steady"
    assert snap["last_jump_ms"] == pytest.approx(25.0, abs=0.5)
    # R10: jump frames are not loop corrections
    assert (snap["dropped"], snap["inserted"], snap["corrections_per_s"]) == (0, 0, 0.0)
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0
    assert snap["pa_steps"] == 0


def test_persistent_early_offset_gives_one_steady_insert():
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)
    before = eng.snapshot()
    port.skip(25.0)  # PA dropped 25 ms: heard 25 ms early
    drive(eng, port, raw, 14.0)
    snap = eng.snapshot()
    assert snap["pa_steps"] == 1
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == "steady"
    assert snap["last_jump_ms"] == pytest.approx(-25.0, abs=0.5)
    assert (snap["dropped"], snap["inserted"], snap["corrections_per_s"]) == (0, 0, 0.0)
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0
    assert snap["rejected_samples"] <= 5  # the step itself, held back by the gate until the jump


def test_steady_jump_in_silence_re_anchors_the_silence():
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 11.0)
    before = eng.snapshot()
    port.skip(-25.0)
    drive(eng, port, raw, 13.0)
    snap = eng.snapshot()
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == "steady"
    assert snap["last_jump_ms"] == pytest.approx(25.0, abs=0.5)
    assert snap["dropped"] == 0
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0


@pytest.mark.parametrize("event", ["delay", "format", "delay in silence"])
def test_jump_is_never_passed_while_a_placement_is_pending(monkeypatch, event):
    eng, port, raw = engine_with_port() if event == "delay in silence" else playing(40.0)
    drive(eng, port, raw, 12.0)
    before = eng.snapshot()

    decisions = []
    real_update = eng._controller.update

    def update(t_s, e_w_us):
        d = real_update(t_s, e_w_us)
        decisions.append(d)
        return d

    monkeypatch.setattr(eng._controller, "update", update)
    renders = []
    real_render = eng._sched.render

    def render(**kw):
        renders.append((eng._sched.placement_pending, kw.get("jump_us")))
        return real_render(**kw)

    monkeypatch.setattr(eng._sched, "render", render)
    fired = []

    def on_sense():
        # A placement becomes due between the jump decision and the next block.
        if decisions and decisions[-1].jump_us is not None and not fired:
            fired.append(decisions[-1].jump_us)
            if event.startswith("delay"):
                eng.resync(0)  # engine-requested placement
            else:  # the scheduler's own placement: a new format, then audio in it
                eng.set_format(InputFormat(sample_rate=44100))
                eng.submit(int(raw.t_us + 1e6), TONE_FRAME * 44100 * 5)

    port.skip(-25.0)
    drive(eng, port, raw, 3.0, on_sense=on_sense)
    snap = eng.snapshot()
    assert fired and fired[0] == pytest.approx(25_000.0, abs=500.0)
    assert not [j for pending, j in renders if pending and j]
    assert not [j for _, j in renders if j]  # the placement re-aligned; the jump was dropped
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == event.split()[0]
    assert snap["dropped"] == 0 and snap["inserted"] == 0
    assert abs(snap["error_ms"]) < 1.0


def test_controller_waits_while_a_long_insert_is_written():
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)
    port.skip(300.0)  # 300 ms early: a 300 ms insert takes 30 blocks, across three readings
    drive(eng, port, raw, 3.0)
    snap = eng.snapshot()
    assert snap["last_jump_reason"] == "steady"
    assert snap["last_jump_ms"] == pytest.approx(-300.0, abs=0.5)
    assert (snap["dropped"], snap["inserted"], snap["corrections_per_s"]) == (0, 0, 0.0)
    # Only the step itself is held back by the gate (4 readings, the 5th decides the jump);
    # readings during the insert are not judged against the old schedule.
    assert snap["rejected_samples"] == 4
    assert abs(snap["error_ms"]) < 1.0


def test_delay_change_places_exactly_and_does_not_degrade():
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)
    before = eng.snapshot()
    assert eng.resync(700_000) is True  # audio 700 ms later: the placement owes 700 ms of silence
    drive(eng, port, raw, 3.0)
    snap = eng.snapshot()
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == "delay"
    assert snap["state"] == "playing"  # the owed silence is scheduled with the placed frame
    assert snap["rejected_samples"] == 0
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0


def test_clear_in_silence_is_a_placement():
    eng, port, raw = playing(2.0)
    drive(eng, port, raw, 2.0)
    before = eng.snapshot()
    eng.clear()
    drive(eng, port, raw, 2.0)
    snap = eng.snapshot()
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == "clear"
    assert abs(snap["error_ms"]) < 1.0


def test_resync_with_nothing_queued_returns_false():
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 1.0)
    assert eng.resync(10_000) is False


def test_audio_written_before_the_first_reading_is_heard_at_its_play_time():
    # The output opens with an empty ring that fills at once: the first frames leave the
    # port right away, not a buffer-time later.
    eng, port, raw = engine_with_port()
    due = raw.t_us + 740_000  # PULSE_LATENCY 600 + LEAD 40 + 100 ms
    eng.submit(int(due), TONE_FRAME * 48000)
    drive(eng, port, raw, 2.0)
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due, abs=50)


class SpikyPort:
    """The simulated port with one reading off by ``spike_ms`` (a PA timing glitch)."""

    def __init__(self, port, spike_at, spike_ms):
        self._port = port
        self._n = 0
        self._spike_at = spike_at
        self._spike_ns = int(spike_ms * 1e6)
        self.spike_at_us = None

    def arm(self, spike_at):
        self._n = 0
        self._spike_at = spike_at

    def read(self):
        self._n += 1
        sample = self._port.read()
        if self._n != self._spike_at:
            return sample
        self.spike_at_us = sample.t_us
        return type(sample)(sample.t_us, sample.a_ns + self._spike_ns, sample.mono_us, sample.bracket_us)


def test_one_outlier_reading_does_not_move_a_placement():
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)
    spiky = SpikyPort(port, spike_at=-1, spike_ms=12.0)  # below a PA step (R5)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=spiky)
    eng.set_format(InputFormat(sample_rate=48000))
    eng.submit(int(raw.t_us + 1e6), TONE_FRAME * 48000 * 10)
    drive(eng, port, raw, 3.0)
    spiky.arm(1)  # the next reading is the glitch; a delay change lands right after it
    fired = []

    def on_sense():
        if not fired:
            fired.append(raw.t_us)
            eng.resync(0)

    drive(eng, port, raw, 2.0, on_sense=on_sense)
    snap = eng.snapshot()
    assert spiky.spike_at_us == fired[0]
    assert snap["last_jump_reason"] == "delay"
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0


def test_missing_readings_are_counted_as_rejected():
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)

    class Flaky:
        def __init__(self):
            self.n = 0

        def read(self):
            self.n += 1
            return None if self.n % 2 else port.read()

    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=Flaky())
    eng.set_format(InputFormat(sample_rate=48000))
    drive(eng, port, raw, 2.0)
    snap = eng.snapshot()
    assert snap["rejected_samples"] >= 9 and abs(snap["error_ms"]) < 0.1


def test_snapshot_carries_the_status_keys():
    eng, _, _ = engine_with_port()
    snap = eng.snapshot()
    assert snap["state"] == "idle" and snap["mode"] == "measure"
    keys = {
        "error_ms", "error_p95_ms", "error_slope_ms_per_s", "correction_ppm", "corrections_per_s",
        "inserted", "dropped", "jumps", "last_jump_ms", "last_jump_reason", "last_jump_at",
        "underruns", "pa_steps", "rejected_samples", "host_latency_ms", "pa_latency_ms",
        "sink_rate_ppm", "clock_slew_ppm", "warm", "warm_s", "warmup_s", "hold_reason",
        "hold_left_s", "late_start_ms", "trim_us", "fallback_reason", "k_ns",
    }  # fmt: skip
    assert keys <= snap.keys()
    assert snap["warm"] is False and snap["warm_s"] is None and snap["warmup_s"] == 60.0


def test_warm_after_warmup():
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure", warmup_s=2.0), port=port)
    eng.set_format(InputFormat(sample_rate=48000))
    drive(eng, port, raw, 1.0)
    assert eng.snapshot()["warm"] is False
    drive(eng, port, raw, 1.5)
    assert eng.snapshot()["warm"] is True


def test_44k1_chunk_is_heard_at_its_play_time():
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=port)
    eng.set_format(InputFormat(sample_rate=44100))
    drive(eng, port, raw, 2.0)
    due = raw.t_us + 1_234_567
    eng.submit(int(due), TONE_FRAME * 4410)
    drive(eng, port, raw, 2.0)
    assert port.heard_at_us(first_nonzero=True) == pytest.approx(due, abs=50)
    assert abs(eng.snapshot()["error_ms"]) < 0.1


def test_jump_frames_are_not_loop_corrections_even_mid_insert():
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 12.0)
    port.skip(300.0)
    seen = []
    drive(eng, port, raw, 3.0, on_sense=lambda: seen.append(eng.snapshot()["corrections_per_s"]))
    assert set(seen) == {0.0}


def test_program_after_a_silence_placement_is_not_recorded_with_the_stale_reason():
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 2.0)
    port.stall(600.0)
    drive(eng, port, raw, 2.0)
    before = eng.snapshot()
    assert before["last_jump_reason"] == "stall"
    eng.submit(int(raw.t_us + 1e6), TONE_FRAME * 4800)
    drive(eng, port, raw, 2.0)
    snap = eng.snapshot()
    assert snap["jumps"] == before["jumps"] + 1 and snap["last_jump_reason"] == "starved"


def test_jump_while_an_insert_is_owed_in_silence_is_dropped_and_logged(monkeypatch, caplog):
    eng, port, raw = engine_with_port()
    drive(eng, port, raw, 2.0)
    eng._sched._owed_insert = 2400  # an insert still being written, nothing queued
    eng._pending_jump_us = 25_000.0
    renders = []
    real_render = eng._sched.render

    def render(**kw):
        renders.append(kw.get("jump_us"))
        return real_render(**kw)

    monkeypatch.setattr(eng._sched, "render", render)
    before = eng.snapshot()["jumps"]
    with caplog.at_level("DEBUG", logger="sendspin_bridge.services.audio.playout.engine"):
        eng.next_block(raw.t_us)
    assert renders == [None]
    assert eng.snapshot()["jumps"] == before
    assert any("jump" in rec.getMessage() and rec.levelname == "DEBUG" for rec in caplog.records)


def test_placement_right_after_a_pa_step_uses_the_new_port_position():
    eng, port, raw = playing(40.0)
    drive(eng, port, raw, 3.0)
    port.skip(18.0)  # below the jump threshold: only a placement can remove it
    fired = []

    def on_sense():
        if not fired:
            fired.append(True)
            eng.resync(0)  # the very next reading is the first after the step

    drive(eng, port, raw, 3.0, on_sense=on_sense)
    snap = eng.snapshot()
    assert snap["pa_steps"] == 1 and snap["last_jump_reason"] == "delay"
    assert abs(snap["error_ms"]) < 1.0 and snap["error_p95_ms"] < 1.0
