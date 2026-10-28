"""Air sensor end to end on recorded captures (Task A10).

The whole chain runs with the real code. Parent side: the btsnoop records, shifted to RAW by one
constant, go through ``A2dpAirParser(check_crc="first")`` → ``AirLinkTracker`` →
``air_envelope_from_batch`` → ``encode_command`` → JSON → ``decode_command``. Daemon side: the
decoded commands go to a real ``AirSensor``, whose written side is a real ``WrittenEnvelope`` fed
10 ms blocks of PCM, and whose windows run through the real correlator. Only the clock and the
event loop are simulated.

**Truth at the PCM instant.** ``air_music.btsnoop.gz`` (``build_air_music_fixture.py``) was
encoded with libsbc from ``music_pcm()``; its truth table gives, for every SBC frame, the first
PCM sample it consumed, its packet's send time and its position in the packet. ``t_air(p)`` of a
PCM sample follows from that table and the nominal sample rate alone; no ``d_enc``, comb or anchor
code is used. The written schedule is chosen by the test, ``s(p) = t_air(p) − H_truth(t_air(p))``,
so the expected ``H`` is exact by construction. Samples that never went out (the PA skip, the
AVDTP gap) get ``t_air`` extrapolated at the nominal rate from the last sample that did.

The synthetic music's PSR values are **not** used to set the correlator gate (R-9): they say
nothing about real music. The confirmed-window PSR median and the noise control's maximum are
recorded with ``record_property`` for information only; the gate is set in A17 on real music.

Measured: the bias and p95 run over the windows the sensor accepted (``AirSensor.accepted_windows``,
so a candidate refuted later never counts) whose 3 s span holds a single ``H_truth`` value.

Time axis: seconds after the capture's first record (the music capture's Connection Complete).
State and reported-``H`` samples are taken on that axis at every 50 ms tick of the simulated
clock. The truth of a reported ``H`` is ``H_truth`` at the air time of the newest window behind
it; ``time_to_reflect_s`` is measured in air time too (the newest accepted window's middle when the
reported value first matches), and its wall-clock latency is recorded alongside.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import math
import statistics
from bisect import bisect_right
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pytest

from sendspin_bridge.services.audio.playout.air_sensor import AirSensor
from sendspin_bridge.services.audio.playout.air_tap import WrittenEnvelope
from sendspin_bridge.services.audio.playout.scheduler import Block, InputFormat
from sendspin_bridge.services.bluetooth.a2dp_air import A2dpAirParser, MonitorRecord, read_btsnoop
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirLinkTracker
from sendspin_bridge.services.ipc.commands import (
    AirEnvelope,
    AirSource,
    air_envelope_from_batch,
    decode_command,
    encode_command,
)
from tests.fixtures.a2dp_air.build_air_music_fixture import SEED, music_pcm

pytestmark = pytest.mark.slow

FIX = Path(__file__).resolve().parents[4] / "fixtures" / "a2dp_air"
RATE = 44100
RAW0_US = 5_000_000_000.0  # RAW time of each capture's first record
TICK_US = 50_000.0
BATCH_US = 250_000.0  # the parent's batch cadence
WRITE_AHEAD_US = 800_000.0  # the engine writes this far ahead of the schedule
BLOCK_FRAMES = 441  # 10 ms blocks
TAIL_S = 3.0  # the simulation runs this long after the last record
H_CLICK_MS = 23.0  # the host placement the click simulation's schedule is built for
CLICK_CYCLE_MS = 10_000.0


# ── the simulation ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tick:
    t: float  # s on the capture axis
    state: str
    h_ms: float | None
    epochs: int
    newest_at: float | None  # air time (capture axis, s) of the newest accepted window


@dataclass(frozen=True)
class Window:
    t: float  # when it was accepted (capture axis, s)
    at: float  # its middle's air time (capture axis, s)
    h_ms: float
    psr: float


@dataclass
class SimResult:
    ticks: list[Tick]
    windows: list[Window]  # every window the sensor accepted, in acceptance order
    results: list[tuple[str, float | None]]  # every correlator result: (status, psr)
    anchor_resets: list[float]  # delivery times of envelopes flagged anchor_reset
    h_truth: list[tuple[float, float]] = field(default_factory=list)
    first_packet: float = 0.0
    final: dict = field(default_factory=dict)

    def truth_ms(self, t: float) -> float:
        return next(h for t0, h in reversed(self.h_truth) if t0 <= t) if t >= self.h_truth[0][0] else self.h_truth[0][1]

    def _clean(self, w: Window) -> bool:
        """The window's 3 s span holds one H_truth value."""
        return all(not (w.at - 1.5 < t0 < w.at + 1.5) for t0, _ in self.h_truth[1:])

    def errors_ms(self, windows: list[Window] | None = None) -> list[float]:
        return [w.h_ms - self.truth_ms(w.at) for w in (self.windows if windows is None else windows) if self._clean(w)]

    @property
    def lock_time_s(self) -> float:
        return next((k.t for k in self.ticks if k.state == "locked"), math.inf) - self.first_packet

    @property
    def h_bias_ms_locked(self) -> float:
        errs = self.errors_ms()
        return statistics.fmean(errs) if errs else math.nan

    @property
    def h_err_ms_locked_p95(self) -> float:
        errs = sorted(abs(e) for e in self.errors_ms())
        return errs[min(len(errs) - 1, math.ceil(0.95 * len(errs)) - 1)] if errs else math.nan

    @property
    def epochs(self) -> int:
        return int(self.final["epochs"])

    @property
    def windows_confirmed(self) -> int:
        return int(self.final["windows_confirmed"])

    def reflect(self, t_change: float, h_new: float, tol_ms: float) -> Tick | None:
        return next(
            (k for k in self.ticks if k.t >= t_change and k.h_ms is not None and abs(k.h_ms - h_new) <= tol_ms), None
        )

    def time_to_reflect_s(self, t_change: float, h_new: float, tol_ms: float) -> float:
        k = self.reflect(t_change, h_new, tol_ms)
        return math.inf if k is None or k.newest_at is None else k.newest_at - t_change

    def epochs_between(self, t0: float, t1: float) -> int:
        return len({k.epochs for k in self.ticks if t0 <= k.t <= t1})

    def anchor_resets_between(self, t0: float, t1: float) -> int:
        return sum(1 for t in self.anchor_resets if t0 <= t <= t1)

    def states_between(self, t0: float, t1: float) -> list[str]:
        return [k.state for k in self.ticks if t0 <= k.t <= t1]

    def max_abs_err_ms_between(self, t0: float, t1: float, states: tuple[str, ...]) -> float:
        """Largest |reported H − truth| while in *states*; NaN (fails any bound) without a sample."""
        errs = [
            abs(k.h_ms - self.truth_ms(k.newest_at))
            for k in self.ticks
            if t0 <= k.t <= t1 and k.state in states and k.h_ms is not None and k.newest_at is not None
        ]
        return max(errs) if errs else math.nan

    def lock_time_after_s(self, t0: float) -> float:
        start = next((k.epochs for k in self.ticks if k.t >= t0), None)
        k = next((k for k in self.ticks if k.t >= t0 and k.state == "locked" and k.epochs != start), None)
        return math.inf if k is None else k.t - t0

    @property
    def locked_windows(self) -> int:
        return len(self.windows)


class AirSim:
    """One capture driven through the parent and daemon paths against a written schedule ``s(p)``."""

    def __init__(self, records: list[MonitorRecord], *, index: int, handle: int) -> None:
        self.shift_s = records[0].t - RAW0_US / 1e6  # unix → RAW, one constant
        self.records = records
        self.index, self.handle = index, handle

    def raw_us(self, t: float) -> float:
        return RAW0_US + t * 1e6

    def rel(self, raw_us: float) -> float:
        return (raw_us - RAW0_US) / 1e6

    def simulate(self, pcm: bytes, schedule_us, start_p: int = 0) -> SimResult:
        return asyncio.run(self._simulate(pcm, schedule_us, start_p))

    async def _simulate(self, pcm: bytes, schedule_us, start_p: int) -> SimResult:
        now = self.raw_us(-1.0)
        clock = {"now": now}
        tap = WrittenEnvelope()
        results: list[tuple[str, float | None]] = []

        async def runner(fn):
            res = fn()
            results.append((res.status, res.psr))
            return res

        sensor = AirSensor(
            tap=tap,
            now_us=lambda: clock["now"],
            output_delay_ms=lambda: 0.0,
            run_window=runner,
            wall_iso=lambda: "2026-10-28T12:00:00+00:00",
        )
        parser = A2dpAirParser(check_crc="first")
        tracker = AirLinkTracker()
        fmt = InputFormat(RATE, 2, 2)
        frames_total = len(pcm) // 4
        p_next = start_p
        rec_i = 0
        next_batch = now + BATCH_US
        end = (self.records[-1].t - self.shift_s) * 1e6 + TAIL_S * 1e6
        ticks: list[Tick] = []
        windows: list[Window] = []
        seen: dict[int, object] = {}  # id -> window, kept alive so an id is never reused
        resets: list[float] = []
        first_packet: float | None = None

        def wire(cmd):
            line = json.dumps(encode_command(cmd))
            return decode_command(json.loads(line), now_raw_us=lambda: clock["now"])

        def schedule_at(x_ns: int) -> float:
            return schedule_us(round(x_ns * RATE / 1e9))

        while now < end:
            now += TICK_US
            clock["now"] = now
            # Engine: write what is due WRITE_AHEAD_US before its schedule.
            while p_next < frames_total and schedule_us(p_next) - WRITE_AHEAD_US <= now:
                n = min(BLOCK_FRAMES, frames_total - p_next)
                blk = Block(
                    pcm=pcm[4 * p_next : 4 * (p_next + n)],
                    x0_ns=round(p_next * 1e9 / RATE),
                    duration_ns=round(n * 1e9 / RATE),
                    program=True,
                    dropped=0,
                    inserted=0,
                    late_drop_us=0.0,
                )
                tap.add_block(blk, fmt, schedule_at, lambda _x: "program", 0.0)
                p_next += n
            # Parent: the hub feeds the parser RAW seconds; packets go to the link's tracker.
            while rec_i < len(self.records) and (self.records[rec_i].t - self.shift_s) * 1e6 <= now:
                rec = self.records[rec_i]
                rec_i += 1
                packets = parser.feed(rec.t - self.shift_s, rec.index, rec.opcode, rec.payload, rec.drops)
                states = parser.link_states()
                for pk in packets:
                    if (pk.index, pk.handle) != (self.index, self.handle):
                        continue
                    if first_packet is None:
                        first_packet = self.rel(pk.t_send * 1e6)
                    st = states.get((pk.index, pk.handle))
                    tracker.on_packet(pk, timestamp="kernel", in_step=bool(st and st.in_step))
            if now >= next_batch:
                next_batch += BATCH_US
                for batch in tracker.take_batches(now):
                    cmd = wire(air_envelope_from_batch(batch))
                    assert isinstance(cmd, AirEnvelope)
                    if cmd.anchor_reset:
                        resets.append(self.rel(now))
                    sensor.submit(cmd)
                ts = tracker.state(now)
                src = AirSource(
                    ts.state, ts.reason, ts.codec, "C0:FB:F9:62:D7:D6", self.index, self.handle, ts.in_step, 0, 0
                )
                sensor.submit(wire(src))
            await sensor.step()
            # Measurement: the sensor's accepted windows and its snapshot.
            for w in sensor.accepted_windows():
                if id(w) not in seen:
                    seen[id(w)] = w
                    windows.append(Window(self.rel(now), self.rel(w.at_us), w.h_us / 1000.0, w.psr))
            snap = sensor.snapshot()
            ticks.append(
                Tick(
                    self.rel(now),
                    str(snap["state"]),
                    snap["host_placement_ms"],  # type: ignore[arg-type]
                    int(snap["epochs"]),  # type: ignore[call-overload]
                    max((w.at for w in windows), default=None),
                )
            )
        return SimResult(ticks, windows, results, resets, first_packet=first_packet or 0.0, final=sensor.snapshot())


class MusicSim:
    """``air_music.btsnoop.gz`` against ``music_pcm()`` with ``s(p) = t_air(p) − H_truth``."""

    def __init__(self) -> None:
        with gzip.open(FIX / "air_music_truth.json.gz") as f:
            self.truth = json.load(f)
        records = list(read_btsnoop(FIX / "air_music.btsnoop.gz"))
        self.sim = AirSim(records, index=self.truth["index"], handle=self.truth["handle"])
        frames = self.truth["frames"]
        self.p0 = [f[0] for f in frames]
        self.t0 = [f[1] + f[2] * self.truth["frame_samples"] * 1e6 / RATE for f in frames]  # µs, frame's first sample
        self._cache: dict[tuple, SimResult] = {}

    def t_air_us(self, p: int) -> float:
        """Air time of PCM sample ``p`` (µs after the first record), from the truth table only."""
        n = self.truth["frame_samples"]
        j = bisect_right(self.p0, p) - 1
        if j < 0:
            return self.t0[0] - (self.p0[0] - p) * 1e6 / RATE
        if p < self.p0[j] + n:
            return self.t0[j] + (p - self.p0[j]) * 1e6 / RATE
        q = self.p0[j] + n - 1  # never sent: extrapolate from the last sample that was
        return self.t0[j] + (q - self.p0[j]) * 1e6 / RATE + (p - q) * 1e6 / RATE

    def run(self, H_truth: list[tuple[float, float]], written_seed: int | None = None) -> SimResult:
        key = (tuple(H_truth), written_seed)
        if key not in self._cache:
            pcm = _music(SEED if written_seed is None else written_seed)

            def h_ms(t_s: float) -> float:
                return next(h for t0, h in reversed(H_truth) if t0 <= t_s) if t_s >= H_truth[0][0] else H_truth[0][1]

            def schedule_us(p: int) -> float:
                t = self.t_air_us(p)
                return RAW0_US + t - h_ms(t / 1e6) * 1000.0

            res = self.sim.simulate(pcm, schedule_us)
            res.h_truth = list(H_truth)
            self._cache[key] = res
        return self._cache[key]


@lru_cache(maxsize=2)
def _music(seed: int) -> bytes:
    return music_pcm(seed=seed)


@dataclass
class ClickResult:
    locked_windows: int
    h_ms: float | None
    expected_ms: float
    sim: SimResult

    @property
    def h_minus_fold_ms(self) -> float:
        if self.h_ms is None:
            return math.nan
        return (self.h_ms - self.expected_ms + CLICK_CYCLE_MS / 2) % CLICK_CYCLE_MS - CLICK_CYCLE_MS / 2


class ClickSim:
    """``eneby_clicks.btsnoop.gz`` (real, mid-stream) against the rendered click track.

    The schedule ``s(p) = S0 + p/rate`` puts the written click phase ``φ_s = S0 mod cycle`` at the
    phase tool's air phase minus ``H_CLICK_MS``; the expected ``H`` is ``φ_air − φ_s`` (wrapped).
    """

    def __init__(self) -> None:
        self.path = FIX / "eneby_clicks.btsnoop.gz"
        self.records = list(read_btsnoop(self.path))
        self._result: ClickResult | None = None

    def run(self) -> ClickResult:
        if self._result is not None:
            return self._result
        from scripts.a2dp_air_phase import analyze
        from sendspin_sync import click_track as ct

        sim = AirSim(self.records, index=1, handle=70)
        reports, _ = analyze(self.path, 1, 70, epoch_s=sim.shift_s)  # phase of RAW times
        phi_air_ms = reports[0].phase_send_ms
        assert phi_air_ms is not None
        cycle_us = CLICK_CYCLE_MS * 1000.0
        start_us = RAW0_US - 1.5e6
        s0 = (phi_air_ms - H_CLICK_MS) * 1000.0
        s0 += math.floor((start_us - s0) / cycle_us) * cycle_us  # the last such phase before the start
        phi_s_ms = (s0 / 1000.0) % CLICK_CYCLE_MS
        duration = (RAW0_US + (self.records[-1].t - self.records[0].t + TAIL_S + 1.0) * 1e6 - s0) / 1e6
        pcm = ct.render_pcm(duration_s=duration, sample_rate=RATE)
        assert ct.CYCLE_S * 1000.0 == CLICK_CYCLE_MS
        res = sim.simulate(pcm, lambda p: s0 + p * 1e6 / RATE)
        h_last = next((k.h_ms for k in reversed(res.ticks) if k.h_ms is not None), None)
        self._result = ClickResult(len(res.windows), h_last, phi_air_ms - phi_s_ms, res)
        return self._result


@pytest.fixture(scope="module")
def sim() -> MusicSim:
    return MusicSim()


@pytest.fixture(scope="module")
def sim_clicks() -> ClickSim:
    return ClickSim()


# ── the brief's scenarios ────────────────────────────────────────────────────────────────────


def test_cold_start_offset_without_bias(sim, record_property):
    r = sim.run(H_truth=[(0.0, 37.0)])
    record_property("lock_time_s", round(r.lock_time_s, 3))
    record_property("h_bias_ms", round(r.h_bias_ms_locked, 4))
    record_property("h_err_p95_ms", round(r.h_err_ms_locked_p95, 4))
    record_property("psr_median_confirmed", round(statistics.median(w.psr for w in r.windows), 3))
    assert r.lock_time_s < 5.0
    assert abs(r.h_bias_ms_locked) <= 0.5 and r.h_err_ms_locked_p95 <= 2.0


def test_steady_jump_is_seen_within_3s(sim, record_property):
    r = sim.run(H_truth=[(0.0, 37.0), (8.0, -4.5)])
    k = r.reflect(8.0, -4.5, tol_ms=2.0)
    record_property("reflect_wall_latency_s", None if k is None else round(k.t - 8.0, 3))
    assert r.time_to_reflect_s(8.0, -4.5, tol_ms=2.0) <= 3.0


def test_pa_skip_keeps_epoch_and_lag(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.epochs_between(8.5, 13.5) == 1 and r.anchor_resets_between(8.5, 9.5) == 1
    assert r.max_abs_err_ms_between(9.0, 13.0, states=("locked",)) <= 2.0


def test_quiet_passage_holds_not_lies(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert set(r.states_between(6.5, 8.0)) <= {"locked", "holding"}
    assert r.max_abs_err_ms_between(6.0, 8.0, states=("locked", "holding")) <= 2.0


def test_loop_section_never_confirms_a_wrong_lag(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.max_abs_err_ms_between(8.0, 12.0, states=("locked",)) <= 2.0


def test_avdtp_restart_is_a_new_epoch_and_relocks(sim):
    r = sim.run(H_truth=[(0.0, 10.0)])
    assert r.epochs >= 2 and r.lock_time_after_s(15.5) < 5.0


def test_noise_control_never_confirms(sim, record_property):
    r = sim.run(H_truth=[(0.0, 10.0)], written_seed=999)
    psrs = [p for _, p in r.results if p is not None]
    record_property("noise_psr_max", round(max(psrs), 3) if psrs else None)
    assert r.windows_confirmed == 0


def test_real_click_capture_agrees_with_the_phase_tool(sim_clicks):
    r = sim_clicks.run()  # eneby_clicks.btsnoop.gz + the rendered click track, schedule chosen by the test
    assert r.locked_windows >= 1 and abs(r.h_minus_fold_ms) <= 1.0


# ── measurements around the brief's scenarios ────────────────────────────────────────────────


def test_windows_straddling_a_quiet_to_program_step_stay_within_the_p95_bound(sim, record_property):
    """A9 saw about 0.46 ms on the first window across a silence → program step; measure it here.

    The windows whose 3 s span contains the quiet → loop step at 8 s (or the program → quiet step
    at 6 s) are reported with their errors; each must stay within the spec's 2 ms p95 bound.
    """
    r = sim.run(H_truth=[(0.0, 10.0)])
    across = [w for w in r.windows if w.at - 1.5 < 8.0 < w.at + 1.5 or w.at - 1.5 < 6.0 < w.at + 1.5]
    errs = r.errors_ms(across)
    record_property("straddling_errors_ms", [round(e, 3) for e in errs])
    assert errs and max(abs(e) for e in errs) <= 2.0


def test_the_fixture_matches_its_truth_table(sim):
    """PA's skip as PA does it: 882 samples (not whole frames) never sent, the send pacing goes on."""
    t = sim.truth
    skip = t["skip"]
    assert skip["samples"] == skip["rtp_ts_step"] == 882 and 882 % t["frame_samples"] != 0
    frames = t["frames"]
    j = next(k for k, f in enumerate(frames) if f[0] == skip["pcm_from"] + 882)
    assert frames[j][2] == 0 and frames[j - 1][0] + t["frame_samples"] == skip["pcm_from"]
    pkt_us = t["frames_per_packet"] * t["frame_samples"] * 1e6 / RATE
    assert frames[j][1] - frames[j - 1][1] == pytest.approx(pkt_us, abs=1000.0)
