"""B6a: closed-loop simulation of the air bias (Stage B), fast subset.

The real engine runs in ``loop`` against :class:`SimulatedPort`; :class:`SimAir` produces the
air windows from the simulation's *truth* (``true_port_time_us`` plus a hidden per-start offset
``h0``) and feeds them through a real :class:`AirFeed`. The tests judge the TRUE heard error
(``SimAir.heard_error_us``: sound minus client time), so a sign or domain slip in the bias,
the seed, the delay trim or the baseline fails here even when every unit test passes.

Fast subset: ``noise_us = 4000``, sigma(H) = 500 us, ``L_true = L_as = 228.7`` ms,
``D_out = D_ref = 184`` ms, ``step_ms = 2.0`` (coarser steps are not needed; the whole file
runs in well under 60 s). The heard error is the median over the audio written 20-30 s after
the first audio. The slow cases (learned, PipeWire, lock lost, ...) belong to B6b.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field

from sendspin_bridge.services.audio.playout.air_bias import AirFeed
from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimAir, SimulatedPort, drive

FS = 48000
TONE = b"\x10\x00\x10\x00"  # one non-zero S16LE stereo frame
L_TRUE_MS = 228.7
L_AS_MS = 228.7
D_REF_MS = 184.0
D_OUT_US = 184_000.0
NOISE_US = 4000.0
STEP_MS = 2.0
H0_MS = (9, 16, 23, 30, 38, 45, 52, 60)
KEY = ("sbc", 44100, "C0:FB:F9:62:D7:D6")
ENGINE_LOGGER = "sendspin_bridge.services.audio.playout.engine"
_JUMP = re.compile(r"playout jump: (\w+), (-?[\d.]+) ms")


@dataclass
class Jump:
    t_s: float  # seconds after the first audio left the engine
    reason: str
    size_ms: float


class _JumpLog(logging.Handler):
    def __init__(self, raw: FakeRaw) -> None:
        super().__init__(logging.INFO)
        self._raw = raw
        self.rows: list[tuple[float, str, float]] = []

    def emit(self, record: logging.LogRecord) -> None:
        m = _JUMP.search(record.getMessage())
        if m:
            self.rows.append((self._raw.t_us, m.group(1), float(m.group(2))))


@dataclass
class Start:
    eng: PlayoutEngine
    port: SimulatedPort
    raw: FakeRaw
    sim: SimAir
    feed: AirFeed
    jumps: _JumpLog
    t0_us: float
    _logger: logging.Logger = field(init=False)
    _old_level: int = field(init=False)

    def run(self, seconds: float, *, on_tick=None) -> None:
        def sense() -> None:
            self.sim.tick(self.raw.t_us)
            if on_tick is not None:
                on_tick()

        drive(
            self.eng,
            self.port,
            self.raw,
            seconds,
            step_ms=STEP_MS,
            on_sense=sense,
            on_block=self.sim.on_block,
        )

    def reasons(self, *, after_s: float = 0.0, before_s: float = 1e9) -> list[Jump]:
        return [
            Jump((t - self.t0_us) / 1e6, r, size)
            for t, r, size in self.jumps.rows
            if after_s <= (t - self.t0_us) / 1e6 < before_s
        ]

    def heard_median_ms(self, lo_s: float, hi_s: float, *, l_true_ms: float = L_TRUE_MS) -> float:
        """Median true heard error over the audio written ``lo_s``-``hi_s`` after the first audio."""
        assert self.sim.first_audio_x_ns is not None
        x0 = self.sim.first_audio_x_ns
        errs = [
            self.sim.heard_error_us(x0 + s * 1e9, l_true_ms)
            for s in (lo_s + i * 0.1 for i in range(int((hi_s - lo_s) / 0.1)))
        ]
        return statistics.median(errs) / 1000.0

    def close(self) -> None:
        logging.getLogger(ENGINE_LOGGER).removeHandler(self.jumps)


def start(
    h0_ms: float,
    *,
    mode: str = "loop",
    buffer_ms: float = 800.0,
    d_out_us: float = D_OUT_US,
    seed: int = 1,
    tap_gen: int = 1,
    feed: AirFeed | None = None,
    tone_s: float = 40.0,
) -> Start:
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, buffer_ms=buffer_ms, noise_us=NOISE_US, seed=seed)
    feed = feed if feed is not None else AirFeed()
    if tap_gen > 1:
        feed.reopen(tap_gen)
    feed.set_key(*KEY)
    feed.set_target(latency_ms=L_AS_MS, d_ref_ms=D_REF_MS)
    sim = SimAir(feed, port, None, h0_us=h0_ms * 1000.0, seed=seed + 100, d_out_us=d_out_us, tap_gen=tap_gen)  # type: ignore[arg-type]
    eng = PlayoutEngine(
        settings=PlayoutSettings(mode=mode),  # type: ignore[arg-type]
        port=port,
        air_feed=feed if mode == "loop" else None,
    )
    sim._engine = eng
    eng.set_format(InputFormat(sample_rate=FS))
    eng.submit(int(raw.t_us + 1e6), TONE * int(FS * tone_s))
    log = _JumpLog(raw)
    lg = logging.getLogger(ENGINE_LOGGER)
    lg.addHandler(log)
    if lg.getEffectiveLevel() > logging.INFO:
        lg.setLevel(logging.INFO)
    return Start(eng, port, raw, sim, feed, log, raw.t_us)


def first_audio_us(st: Start) -> float:
    assert st.sim.first_audio_x_ns is not None
    t = st.port.true_port_time_us(st.sim.first_audio_x_ns)
    assert t is not None
    return t


def run_to_measure(st: Start, seconds: float = 32.0) -> None:
    st.run(seconds)
    # Re-base the jump log's clock to the first audible audio of the tone.
    st.t0_us = first_audio_us(st)


def test_calibrated_cold_starts():
    errs = []
    for i, h0 in enumerate(H0_MS):
        st = start(h0, seed=i + 1)
        try:
            run_to_measure(st)
            err = st.heard_median_ms(20.0, 30.0)
            seeds = [j for j in st.reasons() if j.reason == "air_seed"]
            snap = st.eng.snapshot()
            assert abs(err) <= 1.0, f"h0={h0} ms heard error {err:+.3f} ms; snapshot {snap}"
            assert len(seeds) == 1, f"h0={h0}: {st.reasons()}"
            assert seeds[0].t_s <= 10.0, f"h0={h0}: seed at {seeds[0].t_s:.1f} s"
            assert snap["air_bias_state"] == "active"
            errs.append(err)
        finally:
            st.close()
    sd = statistics.stdev(errs)
    print("calibrated cold starts heard errors (ms):", [round(e, 3) for e in errs], "sd", round(sd, 3))
    assert sd <= 0.5


def test_measure_mode_keeps_the_spread_as_a_control():
    errs = []
    for i, h0 in enumerate(H0_MS):
        st = start(h0, mode="measure", seed=i + 1)
        try:
            run_to_measure(st)
            errs.append(st.heard_median_ms(20.0, 30.0))
            assert st.eng.snapshot()["air_bias_state"] is None
        finally:
            st.close()
    sd = statistics.stdev(errs)
    print("measure heard errors (ms):", [round(e, 3) for e in errs], "sd", round(sd, 3))
    assert sd >= 15.0


def test_delay_change_moves_the_sound_in_active():
    st = start(30.0, seed=3)
    try:
        st.run(14.0)  # past the seed (acquisition + host latency + the triple)
        assert st.eng.snapshot()["air_bias_state"] == "active"
        assert [j.reason for j in st.reasons()].count("air_seed") == 1
        before = st.eng.snapshot()
        t_change = st.raw.t_us
        x_change = st.sim._x_w  # the write head at the change
        st.eng.resync(-30_000)
        st.sim.set_d_out(D_OUT_US + 30_000.0)
        st.run(3.0)
        soon = st.eng.snapshot()  # b is the same quantity before and right after the change (D never enters b)
        st.run(17.0)
        after = st.eng.snapshot()
        jumps = [(j.reason, j.size_ms) for j in st.reasons() if (j.t_s * 1e6 + st.t0_us) >= t_change]
        assert [r for r, _ in jumps] == ["delay"], jumps  # one delay jump, no air jump in the next 20 s
        assert abs(soon["air_bias_ms"] - before["air_bias_ms"]) <= 0.2
        assert abs(after["air_bias_ms"] - before["air_bias_ms"]) <= 1.0  # plus tracking noise over 20 s
        assert after["air_trim_ms"] == 30.0
        # Truth: the heard error of audio written well after the change, against audio well before it.
        st.t0_us = first_audio_us(st)
        x_end = st.sim._x_w
        sim = st.sim
        pre = statistics.median(sim.heard_error_us(x_change - (2.0 + i * 0.1) * 1e9, L_TRUE_MS) for i in range(20))
        post = statistics.median(sim.heard_error_us(x_end - (2.0 + i * 0.1) * 1e9, L_TRUE_MS) for i in range(20))
        shift_ms = (post - pre) / 1000.0
        print(
            "delay change: heard error moved by",
            round(shift_ms, 3),
            "ms; bias",
            before["air_bias_ms"],
            after["air_bias_ms"],
        )
        assert abs(shift_ms - (-30.0)) <= 0.5
    finally:
        st.close()

    # A new start at D_out = 214 lands at -30 ms.
    st2 = start(30.0, seed=4, d_out_us=214_000.0)
    try:
        run_to_measure(st2)
        err = st2.heard_median_ms(20.0, 30.0)
        print("new start at D_out=214: heard error", round(err, 3), "ms")
        assert abs(err - (-30.0)) <= 1.0
        assert st2.eng.snapshot()["air_trim_ms"] == 30.0
    finally:
        st2.close()


def test_reading_slew_after_an_underrun_before_the_seed():
    """C1: a reading slew after an underrun before the seed is folded into the bias, not into beta."""
    st = start(30.0, seed=5, buffer_ms=600.0)  # a 600 ms ring: the 800 ms starve runs it dry
    try:
        st.run(1.5)
        st.port.starve(800.0)
        for _ in range(100):  # until the underrun placement, then the reading falls behind
            st.run(0.1)
            if st.eng.snapshot()["last_jump_reason"] == "underrun":
                break
        else:
            raise AssertionError("no underrun placement")
        st.port.slew(-30.0, 300.0)
        st.run(34.0)
        st.t0_us = first_audio_us(st)
        err = st.heard_median_ms(20.0, 30.0)
        snap = st.eng.snapshot()
        assert snap["underruns"] >= 1
        print("C1 slew: heard error", round(err, 3), "ms; beta", st.eng._baseline_us, "jumps", st.reasons())
        assert snap["air_bias_state"] == "active"
        assert abs(err) <= 1.0
        assert st.eng._baseline_us == 0.0
        assert not [j for j in st.reasons() if j.reason == "steady" and j.t_s > 10.0]
    finally:
        st.close()
