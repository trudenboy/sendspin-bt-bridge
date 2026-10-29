"""B6a: closed-loop simulation of the air bias (Stage B), fast subset.

The real engine runs in ``loop`` against :class:`SimulatedPort`; :class:`SimAir` produces the
air windows from the simulation's *truth* (``true_port_time_us`` plus a hidden per-start offset
``h0``) and feeds them through a real :class:`AirFeed`. The tests judge the TRUE heard error
(``SimAir.heard_error_us``: sound minus client time), so a sign or domain slip in the bias,
the seed, the delay trim or the baseline fails here even when every unit test passes.

Fast subset: ``noise_us = 4000``, sigma(H) = 500 us, ``L_true = L_as = 228.7`` ms,
``D_out = D_ref = 184`` ms, ``step_ms = 2.0`` (coarser steps are not needed; the whole file
runs in well under 60 s). The heard error is the median over the audio written 20-30 s after
the first audio. The slow cases (B6b, ``-m slow``) follow below.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field

import pytest

from sendspin_bridge.services.audio.playout.air_bias import AirFeed
from sendspin_bridge.services.audio.playout.air_learned import learned_reference
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
    noise_us: float = NOISE_US,
    target: dict[str, float | int | None] | None = None,
) -> Start:
    """``target`` replaces the calibration (``AirFeed.set_target`` kwargs, e.g. a learned one)."""
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, buffer_ms=buffer_ms, noise_us=noise_us, seed=seed)
    feed = feed if feed is not None else AirFeed()
    if tap_gen > 1:
        feed.reopen(tap_gen)
    feed.set_key(*KEY)
    feed.set_target(**(target if target is not None else {"latency_ms": L_AS_MS, "d_ref_ms": D_REF_MS}))
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


# -- B6b: the slow cases (``-m slow``) -----------------------------------------------------------

STAGE_A_H_MS = (-45.2, -32.4, -31.4, -44.5, -44.9, -36.3, -44.7, -36.4)
H_OFFSET_MS = 5.146  # t_air - s of the harness at h0 = 0 (measured in a `measure` start)


def _new_format(st: Start, seconds: float = 40.0) -> None:
    """A new stream format with audio in it: the queue of the old format is dropped, a new tone is queued."""
    st.eng.set_format(InputFormat(44100))
    st.eng.submit(int(st.raw.t_us + 1e6), TONE * int(44100 * seconds))


def _seeds(st: Start) -> list[Jump]:
    return [j for j in st.reasons() if j.reason == "air_seed"]


@pytest.mark.slow
def test_calibrated_cold_starts_low_latency():
    """As the fast case with a 200 ms ring (host latency 200 ms instead of 800 ms)."""
    errs = []
    for i, h0 in enumerate(H0_MS):
        st = start(h0, seed=i + 1, buffer_ms=200.0)
        try:
            run_to_measure(st)
            err = st.heard_median_ms(20.0, 30.0)
            seeds = _seeds(st)
            snap = st.eng.snapshot()
            assert abs(err) <= 1.0, f"h0={h0} ms heard error {err:+.3f} ms; snapshot {snap}"
            assert len(seeds) == 1, f"h0={h0}: {st.reasons()}"
            assert seeds[0].t_s <= 10.0, f"h0={h0}: seed at {seeds[0].t_s:.1f} s"
            assert snap["air_bias_state"] == "active"
            errs.append(err)
        finally:
            st.close()
    sd = statistics.stdev(errs)
    print("low-latency cold starts heard errors (ms):", [round(e, 3) for e in errs], "sd", round(sd, 3))
    assert sd <= 0.5


def _h_median_ms(st: Start, lo_s: float, hi_s: float) -> float:
    """Median of the true host placement H = t_air - s over the audio written lo_s-hi_s after the first audio."""
    assert st.sim.first_audio_x_ns is not None
    x0 = st.sim.first_audio_x_ns
    vals = []
    for i in range(int((hi_s - lo_s) / 0.1)):
        x = x0 + (lo_s + i * 0.1) * 1e9
        t, s_ = st.sim.t_air(x), st.sim.s_at(x)
        assert t is not None and s_ is not None
        vals.append(t - s_)
    return statistics.median(vals) / 1000.0


def _h0_for_raw_h(raw_h_ms: float, seed: int, d_out_us: float) -> float:
    """The hidden ``h0`` that makes a start's RAW placement ``H`` equal ``raw_h_ms``.

    The start placement reads a noisy clock, so a start's raw H is ``h0`` + the harness offset + that
    start's placement error (up to ~10 ms for ``noise_us = 4000``). A probe start with the same port
    seed and no target (nothing corrects before the first sample) reads the raw H the air sees.
    """
    probe = start(raw_h_ms - H_OFFSET_MS, seed=seed, d_out_us=d_out_us, target={"latency_ms": None}, feed=AirFeed())
    try:
        probe.run(14.0)
        sample = probe.eng.snapshot()["air_learned"]
        assert sample is not None
        raw = sample["d_out_ms"] - sample["x_ms"]
    finally:
        probe.close()
    return raw_h_ms - H_OFFSET_MS - (raw - raw_h_ms)


@pytest.mark.slow
def test_learned_target_from_the_third_start():
    feed = AirFeed()
    xs: list[float] = []
    ds: list[float] = []
    plain_refs: list[float] = []  # H_ref the plain median of Q7 would have used
    robust_refs: list[float] = []
    placed: list[float] = []  # heard error median per start
    heard_h: list[float] = []
    for n in range(1, 9):
        d_out_us = 214_000.0 if n == 5 else D_OUT_US  # +30 ms delay after start 4
        # stage A's raw H of this start, shifted by +60 ms so the raw placement is late of any learned target
        h0 = _h0_for_raw_h(STAGE_A_H_MS[n - 1] + 60.0, n, d_out_us)
        if n >= 3:
            lat, dref = learned_reference(xs, ds)
            target: dict[str, float | int | None] = {
                "latency_ms": None,
                "learned_ms": lat,
                "learned_d_ref_ms": dref,
                "learned_starts": n - 1,
            }
            h_ref = -(lat - dref)
            offs = [x - d for x, d in zip(xs, ds, strict=True)]
            plain_refs.append(-statistics.median(offs))
            robust_refs.append(h_ref)
        else:
            target = {"latency_ms": None}
            h_ref = None
        st = start(h0, seed=n, feed=feed, tap_gen=n, d_out_us=d_out_us, target=target)
        try:
            st.run(26.0)
            st.t0_us = first_audio_us(st)
            snap = st.eng.snapshot()
            corrections = snap["air_corrections"]
            learned = snap["air_learned"]
            assert learned is not None, f"start {n}: no learned sample; {snap}"
            xs.append(learned["x_ms"])
            ds.append(learned["d_out_ms"])
            h_after = _h_median_ms(st, 16.0, 24.0)
            heard_h.append(h_after)
            placed.append(st.heard_median_ms(16.0, 24.0))
            if n <= 2:
                assert corrections == 0, f"start {n}: {st.reasons()}"
            else:
                assert corrections == 1, f"start {n}: corrections {corrections} {st.reasons()}"
                assert h_ref is not None
                assert abs(h_after - h_ref) <= 1.0, f"start {n}: H {h_after:.2f} vs H_ref {h_ref:.2f}"
        finally:
            st.close()
    sd_robust = statistics.stdev(robust_refs[1:])  # starts 4-8
    sd_plain = statistics.stdev(plain_refs[1:])
    print(
        "learned: H_ref at starts 3-8 robust",
        [round(v, 2) for v in robust_refs],
        "plain median",
        [round(v, 2) for v in plain_refs],
    )
    print("learned: sd of starts 4-8 robust mean", round(sd_robust, 3), "plain median", round(sd_plain, 3))
    print("learned: heard error per start (ms)", [round(v, 2) for v in placed])
    assert sd_robust <= 2.0
    # start 5 ran with D_out = 214: the sound sits 30 ms earlier than start 4's (H_ref differs by the estimator's move)
    move = (placed[4] - placed[3]) - (robust_refs[2] - robust_refs[1])
    print("learned: start 5 vs 4 delay move", round(move, 2), "ms")
    assert abs(move - (-30.0)) <= 1.0


@pytest.mark.slow
@pytest.mark.parametrize(("amplitude_ms", "period_s"), [(30.0, 5.0), (27.0, 15.0)])
def test_pipewire_sawtooth(amplitude_ms, period_s):
    st = start(30.0, seed=7, noise_us=1000.0, tone_s=100.0)
    try:
        st.port.sawtooth(amplitude_ms, period_s)
        st.run(30.0)
        st.t0_us = first_audio_us(st)
        seeds = _seeds(st)
        assert len(seeds) == 1, st.reasons()
        t_seed = st.t0_us + seeds[0].t_s * 1e6
        t_after_seed = st.raw.t_us
        st.run(60.0)
        err = st.heard_median_ms(seeds[0].t_s + 15.0, 30.0 + 60.0 - 10.0)
        steady = [j for j in st.reasons(after_s=seeds[0].t_s + 0.5) if j.reason == "steady"]
        print(
            f"sawtooth {amplitude_ms}/{period_s}: heard error {err:+.3f} ms, seed at {seeds[0].t_s:.1f} s,"
            f" steady jumps {[(round(j.t_s, 1), j.size_ms) for j in steady]}"
        )
        assert t_seed <= t_after_seed
        assert abs(err) <= 2.0, st.eng.snapshot()
        assert not steady
    finally:
        st.close()


@pytest.mark.slow
def test_lock_lost_holds_the_bias():
    st = start(30.0, seed=8, tone_s=150.0)
    try:
        st.run(16.0)
        before = st.eng.snapshot()
        assert before["air_bias_state"] == "active"
        n_jumps = len(st.jumps.rows)
        st.sim.stop()
        st.run(60.0)
        held = st.eng.snapshot()
        assert held["air_bias_state"] == "held", held
        assert held["air_bias_ms"] == before["air_bias_ms"]
        assert len(st.jumps.rows) == n_jumps, st.jumps.rows[n_jumps:]
        st.sim.resume()
        st.run(20.0)
        after = st.eng.snapshot()
        print(
            "lock lost: bias", before["air_bias_ms"], held["air_bias_ms"], after["air_bias_ms"], after["air_bias_state"]
        )
        assert after["air_bias_state"] == "active", after
        assert len(st.jumps.rows) == n_jumps, st.jumps.rows[n_jumps:]
        assert abs(after["air_bias_ms"] - before["air_bias_ms"]) <= 1.0
    finally:
        st.close()


@pytest.mark.slow
def test_format_change_reseeds():
    st = start(30.0, seed=9, tone_s=60.0)
    try:
        st.run(16.0)
        assert st.eng.snapshot()["air_bias_state"] == "active"
        n = len(st.jumps.rows)
        _new_format(st)
        st.sim.h0_us += 5_000.0
        st.run(24.0)
        rows = st.jumps.rows[n:]
        seeds = [r for r in rows if r[1] == "air_seed"]
        print("format change +5 ms:", rows, st.eng.snapshot()["air_bias_state"])
        assert len(seeds) == 1, rows
        assert abs(abs(seeds[0][2]) - 5.0) <= 0.5
    finally:
        st.close()

    st = start(30.0, seed=10, tone_s=60.0)
    try:
        st.run(16.0)
        n = len(st.jumps.rows)
        _new_format(st)
        st.run(1.0)
        assert st.eng.snapshot()["air_bias_state"] == "seeding"
        st.run(23.0)
        assert st.eng.snapshot()["air_bias_state"] == "active"
        assert [r for r in st.jumps.rows[n:] if r[1] in ("air_seed", "steady", "air_target")] == []
    finally:
        st.close()


@pytest.mark.slow
def test_underrun_with_a_hidden_change_reseeds(caplog):
    st = start(30.0, seed=11, buffer_ms=600.0, tone_s=80.0)
    try:
        with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
            st.run(16.0)
            assert st.eng.snapshot()["air_bias_state"] == "active"
            n = len(st.jumps.rows)
            st.port.starve(800.0)  # the 600 ms ring runs dry
            st.sim.h0_us += 12_000.0
            st.run(40.0)
        assert st.eng.snapshot()["underruns"] >= 1
        rows = st.jumps.rows[n:]
        t_under = next(t for t, r, _ in rows if r == "underrun")
        seeds = [(t, size) for t, r, size in rows if r == "air_seed"]
        assert len(seeds) == 1, rows
        assert (seeds[0][0] - t_under) / 1e6 <= 15.0, rows
        # Truth: the audio written in the last 10 s (well past the reseed).
        x_end = st.sim._x_w
        errs = [st.sim.heard_error_us(x_end - (2.0 + i * 0.1) * 1e9, L_TRUE_MS) for i in range(100)]
        err = statistics.median(errs) / 1000.0
        print(
            "underrun + 12 ms hidden: heard error",
            round(err, 3),
            "ms; reseed",
            round((seeds[0][0] - t_under) / 1e6, 1),
            "s after the underrun;",
            [(r, size) for _, r, size in rows],
        )
        assert abs(err) <= 1.0
        assert not [r for r in caplog.records if "error baseline" in r.getMessage()]
    finally:
        st.close()


@pytest.mark.slow
def test_calibration_set_mid_stream():
    lat, dref = 228.7, 184.0
    st = start(
        30.0,
        seed=12,
        tone_s=60.0,
        target={"latency_ms": None, "learned_ms": lat, "learned_d_ref_ms": dref, "learned_starts": 3},
    )
    try:
        st.run(16.0)
        assert st.eng.snapshot()["air_bias_state"] == "active"
        n = len(st.jumps.rows)
        st.feed.set_target(latency_ms=lat + 20.0, d_ref_ms=dref)
        st.run(10.0)
        rows = [r for r in st.jumps.rows[n:]]
        print("calibration +20 ms:", rows)
        assert [r[1] for r in rows] == ["air_target"], rows
        assert abs(rows[0][2] - 20.0) <= 0.5
    finally:
        st.close()


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 13))
def test_hidden_step_is_absorbed_then_corrected(seed):
    st = start(30.0, seed=seed, tone_s=100.0)
    try:
        st.run(16.0)
        b0 = st.eng.snapshot()["air_bias_ms"]
        n = len(st.jumps.rows)
        t_step = st.raw.t_us
        w0 = st.sim.windows_sent
        st.sim.h0_us += 25_000.0
        t_absorbed = None
        for _ in range(350):
            st.run(0.1)
            b1 = st.eng.snapshot()["air_bias_ms"]
            if b1 - b0 < -12.0:
                t_absorbed = (st.raw.t_us - t_step) / 1e6
                break
        assert t_absorbed is not None, "the step was never absorbed"
        assert st.sim.windows_sent - w0 >= 15  # not before 15 windows
        st.run(10.0)
        rows = st.jumps.rows[n:]
        steady = [(round((t - t_step) / 1e6, 1), size) for t, r, size in rows if r == "steady"]
        print("hidden step +25 ms: bias", b0, "->", b1, "after", t_absorbed, "s; steady jumps", steady)
        # b0 is the median of only three windows (sigma ~0.9 ms of its own), so the change is that noise plus b1's
        assert abs((b1 - b0) - (-25.0)) <= 2.5
        assert [r for _, r, _ in rows] == ["steady"], rows
        assert abs(rows[0][2] - 25.0) <= 5.0  # R14 acts on the noisy pulsesink readings
        assert (rows[0][0] - t_step) / 1e6 - t_absorbed <= 10.0
    finally:
        st.close()
