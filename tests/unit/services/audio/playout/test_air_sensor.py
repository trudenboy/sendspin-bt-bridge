"""AirSensor: air stream, search policy, lock state machine, snapshot (Task A9).

The ``air_world`` fixture owns a real ``WrittenEnvelope``, a real ``AirSensor`` and a fake RAW
clock. Its air side is built at the PCM instant, independently of the sensor's formula:

* the engine writes PCM (a 7 kHz carrier under the A8 ``music()`` contour) block by block,
  ``PA_LATENCY_US`` ahead of the schedule ``s(x) = s0 + x/rate (+ engine shifts)``; the written
  envelope comes from the real tap on that PCM (so ``d_comb`` comes from the comb itself);
* PulseAudio plays the PCM sample ``x`` over the air at ``t_air(x) = s0 + x/rate + H_true``
  (engine shifts move ``s``, not the air), with RTP media position ``m = x + C``;
* the SBC energy of the air frame starting at media ``m`` describes the PCM it sees, its own
  128 samples and the ``SBC_LOOK_BACK`` before them (the 10·M-tap analysis window; libsbc 1.3.1
  responds to an impulse at ``m − 72 … m + 126``), so it is centred 4.5·M samples before the
  frame's centre; it carries that frame's air time ``t_air(m − C)`` and is a zero-phase measure
  (``|p[n+1] − p[n−1]|``) of that PCM.

Air batches arrive every ``BATCH_US`` with whatever went over the air until then.
"""

from __future__ import annotations

import math
import sys
from array import array
from functools import lru_cache

import pytest

from sendspin_bridge.services.audio.playout import air_sensor as air_sensor_module
from sendspin_bridge.services.audio.playout.air_correlator import WindowResult
from sendspin_bridge.services.audio.playout.air_sensor import (
    NO_LOCK_S,
    AirSensor,
    AirStream,
    comb_delay_us,
    enc_delay_us,
)
from sendspin_bridge.services.audio.playout.air_tap import WrittenEnvelope
from sendspin_bridge.services.audio.playout.scheduler import Block, InputFormat
from sendspin_bridge.services.ipc.commands import AirEnvelope, AirSource, SetAirCalibration
from tests.unit.services.audio.playout.test_air_correlator import music

pytestmark = pytest.mark.asyncio

AIR_RATE = 44100
SBC_SUBBANDS = 8
SBC_BLOCKS = 16
FRAME_SAMPLES = SBC_SUBBANDS * SBC_BLOCKS
FRAME_US = FRAME_SAMPLES * 1e6 / AIR_RATE
SBC_LOOK_BACK = 72  # an SBC frame's scale factors also see the 9·M samples before it (M = 8)
PA_LATENCY_US = 800_000.0
BATCH_US = 250_000.0
TICK_US = 50_000.0
START_US = 5_000_000_000.0
FIRST_MEDIA = 4 * FRAME_SAMPLES - 2  # media sample of the first output's x = 0 (whole-cell lag: the comb's 1.5)
CARRIER_HZ = 7000.0
PROGRAM_SAMPLES = 1 << 20


@lru_cache(maxsize=1)
def program_pcm() -> array:
    contour = music(PROGRAM_SAMPLES // 128 + 2, seed=7)
    two_pi_f = 2.0 * math.pi * CARRIER_HZ / AIR_RATE
    out = array("h", bytes(2 * PROGRAM_SAMPLES))
    for n in range(PROGRAM_SAMPLES):
        k, r = divmod(n, 128)
        v = contour[k] + (contour[k + 1] - contour[k]) * r / 128.0
        out[n] = int(min(2.0**v, 16000.0) * math.sin(two_pi_f * n))
    return out


class Output:
    """One opened output: what the engine wrote (x in samples) and when it plays."""

    def __init__(self, s0_us: float, h_true_us: float, content_off: int, rate: int) -> None:
        self.s0 = s0_us
        self.h = h_true_us
        self.off = content_off
        self.rate = rate
        self.pcm = array("h")
        self.shifts: list[tuple[int, float]] = [(0, 0.0)]
        self.silent_from: int | None = None
        self.silent_until: float = math.inf

    def s_orig(self, x: float) -> float:
        return self.s0 + x * 1e6 / self.rate

    def shift_at(self, x: float) -> float:
        return next(sh for x0, sh in reversed(self.shifts) if x0 <= x)

    def s(self, x: float) -> float:
        return self.s_orig(x) + self.shift_at(x)

    def t_air(self, x: float) -> float:
        return self.s_orig(x) + self.h

    def silent(self, x: float) -> bool:
        return self.silent_from is not None and self.silent_from <= x < self.silent_until


class World:
    def __init__(
        self, *, H_true_ms: float, output_delay_ms: float = 0.0, input_rate: int = 44100, on_window=None
    ) -> None:
        self.now = START_US
        self.tap = WrittenEnvelope()
        self.fmt = InputFormat(input_rate, 2, 2)
        self.block_frames = round(input_rate * 0.02)
        self.d_out_us = output_delay_ms * 1000.0
        self.runner_calls = 0
        self.windows_computed_on_loop = 0
        self.injected = 0
        self.in_runner = False
        self._inject: float | None = None
        self.flat_next = 0  # the next n window results are replaced by "flat"
        self.flat_after_inject = 0  # ... and this many after the injected one
        self.during_window = None  # called inside the runner, after the window was computed
        self.sensor = AirSensor(
            tap=self.tap,
            now_us=lambda: self.now,
            output_delay_ms=lambda: output_delay_ms,
            run_window=self._runner,
            wall_iso=lambda: "2026-10-28T12:00:00+00:00",
            on_window=on_window,
        )
        self.epoch = 0
        self.seq = 0
        self.m_next = 0  # media sample of the next air frame
        self.frames: list[tuple[int, float, int, bool]] = []  # (m, t, e, anchor_reset) not yet sent
        self.next_batch_at = self.now + BATCH_US
        self.anchor_reset_next = False
        self.segments: list[tuple[int, float, Output | None, int]] = []  # (m0, t0, output or gap, x0)
        self.mutes: list[float] = []
        self.content_off = 0
        self.out = self._open_output(H_true_ms * 1000.0, FIRST_MEDIA)
        self.source()

    # -- runner ---------------------------------------------------------------------------

    async def _runner(self, fn):
        self.runner_calls += 1
        self.in_runner = True
        try:
            result = fn()
        finally:
            self.in_runner = False
        if self.during_window is not None:
            self.during_window()
        if self.flat_next > 0:
            self.flat_next -= 1
            return WindowResult("flat", None, None, None)
        if self._inject is not None and result.status == "confirmed":
            result = WindowResult("confirmed", result.lag + self._inject, result.rho, result.psr)
            self._inject = None
            self.injected += 1
            self.flat_next = self.flat_after_inject
        return result

    # -- world ----------------------------------------------------------------------------

    def _open_output(self, h_true_us: float, m0: int) -> Output:
        out = Output(self.now + PA_LATENCY_US, h_true_us, self.content_off, self.fmt.sample_rate)
        out.x_next = 0  # type: ignore[attr-defined]
        self.segments.append((m0, out.t_air(0), out, 0))
        return out

    def _segment(self, m: int) -> tuple[int, float, Output | None, int]:
        for seg in reversed(self.segments):
            if seg[0] <= m:
                return seg
        return self.segments[0]

    def _air_time(self, m: int) -> float:
        m0, t0, _out, _x0 = self._segment(m)
        return t0 + (m - m0) * 1e6 / AIR_RATE

    def _muted(self, out: Output, x: int) -> bool:
        return bool(self.mutes) and out.s(x) >= self.mutes[0]

    def _air_pcm(self, m_lo: int, m_hi: int) -> list[int]:
        """The PCM PulseAudio played at media samples ``m_lo:m_hi`` (silence in gaps and before the start)."""
        out_samples = []
        for m in range(m_lo, m_hi):
            m0, _t0, out, x0 = self._segment(m)
            x = x0 + m - m0
            if out is None or m < m0 or not 0 <= x < len(out.pcm) or self._muted(out, x):
                out_samples.append(0)
            else:
                out_samples.append(out.pcm[x])
        return out_samples

    def _fast_air_pcm(self, m_lo: int, m_hi: int) -> list[int]:
        m0, _t0, out, x0 = self._segment(m_lo)
        if out is None or m_lo < m0 or self._segment(m_hi - 1)[0] != m0:
            return self._air_pcm(m_lo, m_hi)
        x_lo, x_hi = x0 + m_lo - m0, x0 + m_hi - m0
        if x_lo < 0 or x_hi > len(out.pcm) or self._muted(out, x_lo) or self._muted(out, x_hi - 1):
            return self._air_pcm(m_lo, m_hi)
        return list(out.pcm[x_lo:x_hi])

    def _energy(self, m: int) -> int:
        q = m - SBC_LOOK_BACK
        p = self._fast_air_pcm(q - 1, m + FRAME_SAMPLES + 1)
        peak = max(abs(p[k + 2] - p[k]) for k in range(SBC_LOOK_BACK + FRAME_SAMPLES))
        return min(254, round(8.0 * math.log2(1.0 + peak / 2.0)))

    def _write_due_blocks(self) -> None:
        out = self.out
        rate = self.fmt.sample_rate
        pcm = program_pcm()
        while out.s_orig(out.x_next) - PA_LATENCY_US <= self.now:  # type: ignore[attr-defined]
            x = out.x_next  # type: ignore[attr-defined]
            n = self.block_frames
            if out.silent(x):
                mono = array("h", bytes(2 * n))
            else:
                a = (x + out.off) % PROGRAM_SAMPLES
                mono = pcm[a : a + n] if a + n <= PROGRAM_SAMPLES else pcm[a:] + pcm[: a + n - PROGRAM_SAMPLES]
            out.pcm.extend(mono)
            stereo = array("h", bytes(4 * n))
            stereo[0::2] = mono
            stereo[1::2] = mono
            blk = Block(
                pcm=stereo.tobytes(),
                x0_ns=round(x * 1e9 / rate),
                duration_ns=round(n * 1e9 / rate),
                program=not out.silent(x),
                dropped=0,
                inserted=0,
                late_drop_us=0.0,
            )

            def sched(x_ns: int, out: Output = out) -> float:
                return out.s(round(x_ns * rate / 1e9))

            def kind(x_ns: int, out: Output = out) -> str:
                return "silence" if out.silent(round(x_ns * rate / 1e9)) else "program"

            self.tap.add_block(blk, self.fmt, sched, kind, self.d_out_us)
            out.x_next = x + n  # type: ignore[attr-defined]

    def _produce_air(self) -> None:
        while (t := self._air_time(self.m_next)) <= self.now:
            self.frames.append((self.m_next, t, self._energy(self.m_next), self.anchor_reset_next))
            self.anchor_reset_next = False
            self.m_next += FRAME_SAMPLES

    def _send_batches(self) -> None:
        if self.now < self.next_batch_at:
            return
        self.next_batch_at += BATCH_US
        runs: list[list[tuple[int, float, int, bool]]] = []
        for f in self.frames:
            last = runs[-1][-1] if runs else None
            contiguous = (
                last is not None
                and f[0] == last[0] + FRAME_SAMPLES
                and abs(f[1] - last[1] - FRAME_US) < 1e-3
                and not f[3]
                and len(runs[-1]) < 128
            )
            if contiguous:
                runs[-1].append(f)
            else:
                runs.append([f])
        self.frames = []
        for run in runs:
            self.sensor.on_envelope(
                AirEnvelope(
                    epoch=self.epoch,
                    seq=self.seq,
                    u0_us=run[0][0] * 1e6 / AIR_RATE,
                    t0_us=run[0][1],
                    frame_us=FRAME_US,
                    timestamp="kernel",
                    rate=AIR_RATE,
                    subbands=SBC_SUBBANDS,
                    blocks=SBC_BLOCKS,
                    bands=(1, 2),
                    complete_lag_us=8000.0,
                    anchor_reset=run[0][3],
                    e=tuple(f[2] for f in run),
                )
            )
            self.seq += len(run)

    async def run(self, seconds: float) -> None:
        for _ in range(round(seconds * 1e6 / TICK_US)):
            self.now += TICK_US
            self._write_due_blocks()
            self._produce_air()
            self._send_batches()
            await self.sensor.step()

    # -- events ---------------------------------------------------------------------------

    def source(self, state: str = "streaming", reason: str | None = None) -> None:
        self.sensor.on_source(
            AirSource(
                state=state,
                reason=reason,
                codec="sbc",
                adapter="C0:FB:F9:62:D7:D6",
                index=0,
                handle=70,
                in_step=False,
                monitor_drops=0,
                ipc_dropped=0,
            )
        )

    def shift_schedule_ms(self, ms: float) -> None:
        """An engine jump: what is written from now on is scheduled ``ms`` later; the air does not move."""
        self.out.shifts.append((self.out.x_next, self.out.shifts[-1][1] + ms * 1000.0))  # type: ignore[attr-defined]

    def pa_skip_ms(self, ms: float) -> None:
        """PulseAudio "Skipping": media time steps over PCM that never goes out; sequence contiguous."""
        self.m_next += round(ms * AIR_RATE / 1000.0)
        self.anchor_reset_next = True

    def pa_skip_count_axis_ms(self, ms: float) -> None:
        """PulseAudio "Skipping" with the air running on (R-18): media time steps over PCM that never
        goes out while the next frame airs one frame after the last; the content mapping is kept."""
        skip = round(ms * AIR_RATE / 1000.0)
        m_g = self.m_next
        m0, _t0, out, x0 = self._segment(m_g)
        t_g = self._air_time(m_g)  # when the next frame airs anyway
        self.segments.append((m_g + skip, t_g, out, x0 + m_g + skip - m0))
        self.m_next = m_g + skip
        self.anchor_reset_next = True

    def underrun_ms(self, ms: float) -> None:
        """The port ran through an underrun: PulseAudio played silence that is not ours (media advances)."""
        gap = round(ms * AIR_RATE / 1000.0)
        m_g = self.m_next
        m0, _t0, out, x0 = self._segment(m_g)
        t_g = self._air_time(m_g)
        self.segments.append((m_g, t_g, None, 0))
        self.segments.append((m_g + gap, t_g + gap * 1e6 / AIR_RATE, out, x0 + m_g - m0))
        self.sensor.on_engine_event("underrun")

    def reopen_output(self, H_true_ms: float) -> None:
        """A stream start or seek: the output reopens (x restarts at 0); the air link keeps its epoch."""
        self.frames = [f for f in self.frames if f[1] <= self.now]
        self.tap.reset()
        self.sensor.on_engine_event("reopen")
        self.content_off += 7 * AIR_RATE
        self.out = self._open_output(H_true_ms * 1000.0, self.m_next)

    def mute(self) -> None:
        self.tap.mute_transition(self.now, True)
        self.mutes.append(self.now)

    def silence(self) -> None:
        self.out.silent_from = self.out.x_next  # type: ignore[attr-defined]

    def resume(self) -> None:
        self.out.silent_until = self.out.x_next  # type: ignore[attr-defined]

    def inject_one_window_with_lag_offset(self, cells: float) -> None:
        self._inject = float(cells)


@pytest.fixture
def air_world(monkeypatch):
    worlds: list[World] = []
    real = air_sensor_module.correlate_window

    def counted(*args, **kwargs):
        if not any(w.in_runner for w in worlds):
            for w in worlds:
                w.windows_computed_on_loop += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(air_sensor_module, "correlate_window", counted)

    def make(**kwargs) -> World:
        w = World(**kwargs)
        worlds.append(w)
        return w

    return make


# -- the brief's scenarios ------------------------------------------------------------------------


async def test_locks_and_reports_host_placement_without_bias(air_world):
    w = air_world(H_true_ms=37.0)
    await w.run(seconds=10)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["host_placement_ms"] == pytest.approx(37.0, abs=0.5)
    assert s["air_error_ms"] is None and s["calibrated"] is False and 0.0 < s["confidence"] <= 1.0


async def test_sign_of_the_encoder_correction(air_world):
    """Air energy lags the PCM by d_enc: forgetting the correction (or flipping it) is off by ~0.8 or ~1.6 ms."""
    w = air_world(H_true_ms=0.0)
    await w.run(seconds=10)
    assert abs(w.sensor.snapshot()["host_placement_ms"]) < 0.4


async def test_the_comb_lag_is_credited_back(air_world):
    """The written energy lags its PCM by d_comb = 1.5 samples (34 µs): flipping it is off by 68 µs.

    The fixture is deterministic and its bias is ~10 µs, so this tolerance does not flake."""
    w = air_world(H_true_ms=0.0)
    await w.run(seconds=10)
    assert abs(w.sensor.snapshot()["host_placement_ms"]) < 0.03


async def test_air_error_uses_calibration_and_output_delay(air_world):
    w = air_world(H_true_ms=37.0, output_delay_ms=180.0)
    w.sensor.set_calibration(203.0)
    await w.run(seconds=10)
    assert w.sensor.snapshot()["air_error_ms"] == pytest.approx(37.0 + 203.0 - 180.0, abs=0.5)


async def test_engine_jump_moves_h_not_the_lag(air_world):
    w = air_world(H_true_ms=37.0)
    await w.run(seconds=10)
    lag = w.sensor.snapshot()["lag_cells"]
    w.shift_schedule_ms(-41.5)
    await w.run(seconds=6)
    s = w.sensor.snapshot()
    assert s["host_placement_ms"] == pytest.approx(78.5, abs=1.0) and s["lag_cells"] == pytest.approx(lag, abs=0.3)


async def test_pa_skip_anchor_reset_keeps_the_lag(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    lag = w.sensor.snapshot()["lag_cells"]
    w.pa_skip_ms(20.0)  # contiguous sequence, RTP step, anchor reset, same epoch
    await w.run(seconds=4)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["epochs"] == 1 and s["host_placement_ms"] == pytest.approx(10.0, abs=1.0)
    assert s["lag_cells"] == pytest.approx(lag, abs=0.3)


async def test_reopen_reacquires(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.reopen_output(H_true_ms=-60.0)  # every stream start/seek reopens the output
    await w.run(seconds=0.5)
    assert w.sensor.snapshot()["state"] == "acquiring"
    await w.run(seconds=6)
    assert w.sensor.snapshot()["host_placement_ms"] == pytest.approx(-60.0, abs=0.5)


async def test_acquisition_range_is_500ms(air_world):
    w = air_world(H_true_ms=420.0)  # e.g. a PipeWire fallback latency guess far off
    await w.run(seconds=10)
    assert w.sensor.snapshot()["host_placement_ms"] == pytest.approx(420.0, abs=0.5)


async def test_muted_windows_are_skipped_not_rejected(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.mute()
    await w.run(seconds=6)
    s = w.sensor.snapshot()
    assert s["state"] == "holding" and s["windows_skipped"] > 0 and s["windows_rejected"] == 0


async def test_silence_holds_then_loses_lock(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.silence()
    await w.run(seconds=6)
    assert w.sensor.snapshot()["state"] == "holding"
    await w.run(seconds=NO_LOCK_S)
    s = w.sensor.snapshot()
    assert s["state"] == "no_lock" and s["host_placement_ms"] is None


async def test_a_single_wrong_peak_is_not_accepted(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    lag = w.sensor.snapshot()["lag_cells"]
    w.inject_one_window_with_lag_offset(20)
    await w.run(seconds=1)
    assert w.sensor.snapshot()["lag_cells"] == pytest.approx(lag, abs=0.3)


async def test_unsupported_input_and_source_states(air_world):
    w = air_world(H_true_ms=10.0, input_rate=96000)
    await w.run(seconds=2)
    assert w.sensor.snapshot()["state"] == "unsupported_input"
    w2 = air_world(H_true_ms=10.0)
    w2.source(state="unsupported_codec", reason="codec:aac")
    assert w2.sensor.snapshot()["state"] == "unsupported_codec"


async def test_fallback_withdraws_accepts(air_world):
    w = air_world(H_true_ms=10.0)
    w.sensor.on_engine_event("fallback")
    s = w.sensor.snapshot()
    assert (s["state"], s["reason"], s["accepts"]) == ("unavailable", "playout_fallback", [])


async def test_windows_run_through_the_runner(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    assert w.runner_calls > 0 and w.windows_computed_on_loop == 0


# -- further behaviour ----------------------------------------------------------------------------


async def test_delays_follow_the_sbc_and_comb_models():
    assert enc_delay_us(8, 44100) == pytest.approx(4.5 * 8 / 44100 * 1e6)
    assert enc_delay_us(4, 48000) == pytest.approx(4.5 * 4 / 48000 * 1e6)
    assert comb_delay_us(44100) == pytest.approx(1.5 / 44100 * 1e6)
    assert comb_delay_us(48000) == pytest.approx(1.5 / 48000 * 1e6)


async def test_the_wrong_peak_is_counted_rejected_and_the_lag_stays(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    before = w.sensor.snapshot()
    w.inject_one_window_with_lag_offset(20)
    await w.run(seconds=3)
    s = w.sensor.snapshot()
    assert w.injected == 1
    assert s["windows_rejected"] == before["windows_rejected"] + 1
    assert s["state"] == "locked" and s["lag_cells"] == pytest.approx(before["lag_cells"], abs=0.3)
    assert s["host_placement_ms"] == pytest.approx(10.0, abs=0.5)


async def test_an_underrun_widens_the_search_and_two_windows_move_the_lag(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    lag = w.sensor.snapshot()["lag_cells"]
    gap_ms = 30 * FRAME_US / 1000.0  # 30 cells: outside the ±20 tracking range
    w.underrun_ms(gap_ms)
    await w.run(seconds=8)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["lag_cells"] == pytest.approx(lag - 30, abs=0.3)
    assert s["host_placement_ms"] == pytest.approx(10.0 + gap_ms, abs=1.0)


async def test_a_new_epoch_reacquires(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.epoch += 1
    w.seq = 0
    await w.run(seconds=0.5)
    s = w.sensor.snapshot()
    assert s["state"] == "acquiring" and s["epochs"] == 2 and s["host_placement_ms"] is None
    await w.run(seconds=6)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["host_placement_ms"] == pytest.approx(10.0, abs=0.5)


async def test_a_sequence_jump_drops_the_batch_and_reacquires(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.seq += 5
    await w.run(seconds=0.3)
    s = w.sensor.snapshot()
    assert s["state"] == "acquiring" and s["dropped_messages"] == 1 and s["epochs"] == 1


async def test_a_format_change_on_the_tap_is_treated_like_a_reopen(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.tap.reset()  # the generation changes without a reopen event
    await w.run(seconds=0.3)
    assert w.sensor.snapshot()["state"] == "acquiring"


async def test_an_internal_error_never_reaches_the_daemon(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=5)

    def boom(*_args, **_kwargs):
        raise RuntimeError("tap broke")

    w.tap.slice = boom  # type: ignore[method-assign]
    await w.run(seconds=2)  # must not raise
    s = w.sensor.snapshot()
    assert (s["state"], s["reason"]) == ("unavailable", "internal_error")


async def test_holding_reports_the_last_value_and_decays_confidence(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    locked = w.sensor.snapshot()["confidence"]
    w.silence()
    await w.run(seconds=10)
    s = w.sensor.snapshot()
    assert s["state"] == "holding" and s["host_placement_ms"] == pytest.approx(10.0, abs=0.5)
    assert 0.0 <= s["confidence"] < locked


async def test_snapshot_carries_the_status_fields(air_world):
    w = air_world(H_true_ms=10.0, output_delay_ms=50.0)
    w.sensor.submit(SetAirCalibration(latency_ms=50.0))
    await w.run(seconds=10)
    s = w.sensor.snapshot()
    assert s["calibrated"] is True and s["air_to_sound_ms"] == 50.0
    assert s["air_error_ms"] == pytest.approx(10.0, abs=0.5)
    assert s["codec"] == "sbc" and s["adapter"] == "C0:FB:F9:62:D7:D6" and s["anchor"] == "send"
    assert s["timestamp"] == "kernel" and s["complete_lag_ms"] == pytest.approx(8.0)
    assert s["accepts"] == [1] and s["fold"] is None and s["last_confirmed_at"] == "2026-10-28T12:00:00+00:00"
    assert s["frames"] > 3000 and s["missing_frames"] == 0 and s["windows_confirmed"] >= 2
    assert s["jitter_ms"] is not None and s["jitter_ms"] < 0.5 and s["psr"] >= 1.17
    for key in ("monitor_drops", "ipc_dropped", "dropped_messages", "epochs", "reason"):
        assert key in s


async def test_snapshot_reports_the_sbc_rate_of_the_air_frames(air_world):
    """The parent picks the calibration by the SBC rate the daemon sees (R-12)."""
    w = air_world(H_true_ms=10.0)
    assert w.sensor.snapshot()["rate"] is None
    await w.run(seconds=2)
    assert w.sensor.snapshot()["rate"] == 44100


async def test_a_lock_regained_after_no_lock_reports_only_fresh_windows(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.silence()
    await w.run(seconds=NO_LOCK_S + 3)
    assert w.sensor.snapshot()["state"] == "no_lock"
    w.shift_schedule_ms(-41.5)  # the lag stays, H moves
    w.resume()
    await w.run(seconds=4.5)  # one or two windows since the lock came back
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["host_placement_ms"] == pytest.approx(51.5, abs=0.5)


async def test_a_skipped_window_after_an_underrun_keeps_the_wide_search(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    lag = w.sensor.snapshot()["lag_cells"]
    w.underrun_ms(30 * FRAME_US / 1000.0)
    w.flat_next = 4  # every window up to the first one wholly after the event is flat
    await w.run(seconds=10)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["lag_cells"] == pytest.approx(lag - 30, abs=0.3)


async def test_the_anchor_reset_reverification_waits_for_a_confirmed_window(air_world, monkeypatch, caplog):
    monkeypatch.setenv("SENDSPIN_AIR_DEBUG", "1")
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.pa_skip_ms(20.0)
    w.flat_next = 1
    caplog.clear()
    with caplog.at_level("DEBUG", logger=air_sensor_module.__name__):
        await w.run(seconds=3)
    modes = [r.getMessage().split("mode=")[1].split()[0] for r in caplog.records if "Air window:" in r.getMessage()]
    assert modes and modes[0] == "reset"


async def test_after_no_lock_the_first_lag_needs_two_windows(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    w.silence()
    await w.run(seconds=NO_LOCK_S + 3)
    w.resume()
    w.inject_one_window_with_lag_offset(2)  # close to the old lag, but alone
    w.flat_after_inject = 100
    await w.run(seconds=6)
    s = w.sensor.snapshot()
    assert w.injected == 1 and s["state"] == "no_lock" and s["lag_cells"] is None


async def test_a_window_computed_across_a_tap_generation_change_is_dropped(air_world):
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    before = w.sensor.snapshot()["windows_confirmed"]
    w.during_window = w.tap.reset
    await w.run(seconds=1.1)
    s = w.sensor.snapshot()
    assert s["windows_confirmed"] == before and s["state"] == "acquiring"


async def test_bad_inputs_never_raise_into_the_daemon(air_world):
    w = air_world(H_true_ms=10.0)
    w.sensor.set_calibration("not a number")  # type: ignore[arg-type]
    assert (w.sensor.snapshot()["state"], w.sensor.snapshot()["reason"]) == ("unavailable", "internal_error")


async def test_no_source_and_no_air_is_no_signal():
    sensor = AirSensor(tap=WrittenEnvelope(), now_us=lambda: START_US, output_delay_ms=lambda: 0.0)
    assert sensor.snapshot()["state"] == "no_signal"


# -- AirStream -----------------------------------------------------------------------------------


def env(epoch=0, seq=0, u0=0.0, t0=START_US, n=4, anchor_reset=False, e=None, frame_us=FRAME_US):
    return AirEnvelope(
        epoch=epoch,
        seq=seq,
        u0_us=u0,
        t0_us=t0,
        frame_us=frame_us,
        timestamp="kernel",
        rate=AIR_RATE,
        subbands=SBC_SUBBANDS,
        blocks=SBC_BLOCKS,
        bands=(1, 2),
        complete_lag_us=None,
        anchor_reset=anchor_reset,
        e=tuple(e if e is not None else range(10, 10 + n)),
    )


async def test_air_stream_epochs_resets_and_drops():
    st = AirStream()
    assert st.add(env()) == "new_epoch" and st.epoch == 0
    assert st.add(env(seq=4, u0=4 * FRAME_US, t0=START_US + 4 * FRAME_US)) == "ok"
    assert st.add(env(seq=8, u0=20 * FRAME_US, t0=START_US + 20 * FRAME_US, anchor_reset=True)) == "anchor_reset"
    assert st.add(env(seq=13, u0=30 * FRAME_US)) == "dropped"  # sequence jump
    assert st.add(env(epoch=1, seq=0)) == "new_epoch" and st.epoch == 1


async def test_air_stream_window_on_the_media_grid():
    st = AirStream()
    st.add(env(n=8, e=[10, 20, 30, 40, 255, 60, 70, 80]))
    cells, u_first, t_mid = st.window(7 * FRAME_US, 8, FRAME_US)
    assert cells == [10, 20, 30, 40, None, 60, 70, 80]
    assert u_first == pytest.approx(0.0) and t_mid == pytest.approx(START_US + 4 * FRAME_US)
    # half a cell off the air grid: linear interpolation, missing neighbours stay missing
    cells, _u, _t = st.window(6.5 * FRAME_US, 4, FRAME_US)
    assert cells == [None, None, pytest.approx(65.0), pytest.approx(75.0)]
    assert st.window(20 * FRAME_US, 8, FRAME_US) is None  # beyond the newest frame


async def test_air_stream_does_not_interpolate_across_a_skip():
    st = AirStream()
    st.add(env(n=4, e=[10, 20, 30, 40]))
    st.add(env(seq=4, u0=6 * FRAME_US, t0=START_US + 6 * FRAME_US, n=4, anchor_reset=True, e=[50, 60, 70, 80]))
    cells, _u, _t = st.window(9 * FRAME_US, 10, FRAME_US)
    assert cells == [10, 20, 30, 40, None, None, 50, 60, 70, 80]


async def test_air_time_of_a_middle_inside_a_skip_follows_the_frame_count():
    """R-18: across a PA skip the air time runs on the frame count, not on media time. A window
    middle in the skipped media span lies between the last frame before it and the first one
    after it on the air; extrapolating media time would put it up to the skip later."""
    st = AirStream()
    skip_us = 20_000.0
    st.add(env(n=100, e=[40] * 100))
    st.add(env(seq=100, u0=100 * FRAME_US + skip_us, t0=START_US + 100 * FRAME_US, n=100, anchor_reset=True))
    u_mid = 100 * FRAME_US + skip_us / 2
    _cells, _u, t_mid = st.window(u_mid + 50 * FRAME_US, 101, FRAME_US)
    t_before, t_after = START_US + 99 * FRAME_US, START_US + 100 * FRAME_US
    assert t_before <= t_mid <= t_after
    expected = t_before + (u_mid - 99 * FRAME_US) / (FRAME_US + skip_us) * FRAME_US
    assert t_mid == pytest.approx(expected, abs=1.0)
    # off the gap, the frame-count air time is the frame's own (the frames after the skip)
    _cells, _u, t_mid = st.window(u_mid + 50 * FRAME_US + 10_000.0, 101, FRAME_US)
    u_mid2 = u_mid + 10_000.0
    assert t_mid == pytest.approx(START_US + 100 * FRAME_US + (u_mid2 - 100 * FRAME_US - skip_us), abs=1.0)


async def test_a_pa_skip_on_the_frame_count_axis_steps_h_once(air_world):
    """R-18 end to end: PulseAudio skips media while the air runs on (the frames keep their count
    cadence), so the content after the skip airs ``D`` earlier than scheduled: ``H`` steps by
    ``−D`` once, with no window outside the two levels, and the lag stays."""
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    lag = w.sensor.snapshot()["lag_cells"]
    before = len(w.sensor.accepted_windows())
    t_skip = w.now
    w.pa_skip_count_axis_ms(20.0)
    await w.run(seconds=8)
    s = w.sensor.snapshot()
    assert s["state"] == "locked" and s["epochs"] == 1
    assert s["host_placement_ms"] == pytest.approx(-10.0, abs=1.0)
    assert s["lag_cells"] == pytest.approx(lag, abs=0.3)
    after = [win for win in w.sensor.accepted_windows() if win.at_us > t_skip]
    assert len(after) >= 3 and len(w.sensor.accepted_windows()) > before
    assert all(-11_000.0 <= win.h_us <= 11_000.0 for win in after)
    assert after[-1].h_us == pytest.approx(-10_000.0, abs=1000.0)


async def test_air_that_stopped_arriving_is_not_correlated_again(air_world):
    """Found by the A10 simulation: across an AVDTP gap the same air was recomputed every hop and
    two copies of one window "agreed", which confirmed a lag on unrelated (noise-control) audio."""
    w = air_world(H_true_ms=10.0)
    await w.run(seconds=10)
    before = w.sensor.snapshot()
    calls = w.runner_calls
    w._produce_air = lambda: None  # the link went quiet: nothing new on the air
    await w.run(seconds=4)
    s = w.sensor.snapshot()
    # At most the one window over the air that arrived since the last one; no recomputations.
    assert w.runner_calls <= calls + 1
    assert (
        s["windows_confirmed"] + s["windows_rejected"] <= before["windows_confirmed"] + before["windows_rejected"] + 1
    )


def _window_lines(caplog) -> list[tuple[float, float | None]]:
    """``(t_mid µs, H ms or None)`` of every ``Air window:`` debug line."""
    out = []
    for r in caplog.records:
        msg = r.getMessage()
        if "Air window:" in msg:
            t_mid = float(msg.split("t_mid=")[1].split()[0])
            h = msg.split(" H=")[1].split()[0]
            out.append((t_mid, None if h == "-" else float(h.removesuffix("ms"))))
    return out


async def test_a_window_reports_the_schedule_segment_at_its_middle(air_world, monkeypatch, caplog):
    """H describes the window's middle: with s stepping twice inside the window (a 0.8 s segment
    around the middle, longer ones either side), the middle's segment is used, not the longest."""
    monkeypatch.setenv("SENDSPIN_AIR_DEBUG", "1")
    with caplog.at_level("DEBUG", logger=air_sensor_module.__name__):
        a = air_world(H_true_ms=10.0)
        await a.run(seconds=16)
    tau = next(t for t, h in _window_lines(caplog) if h is not None and t > a.out.t_air(0) + 13e6)
    caplog.clear()
    with caplog.at_level("DEBUG", logger=air_sensor_module.__name__):
        b = air_world(H_true_ms=10.0)
        for at_air, ms in ((tau - 0.4e6, -20.0), (tau + 0.4e6, 20.0)):
            # what is written now plays PA_LATENCY_US later, H_true later on the air
            while b.now < at_air - 10_000.0 - PA_LATENCY_US:
                await b.run(seconds=TICK_US / 1e6)
            b.shift_schedule_ms(ms)
        await b.run(seconds=3)
    h = next(h for t, h in _window_lines(caplog) if abs(t - tau) < 1.0)
    assert h == pytest.approx(30.0, abs=0.5)


async def test_the_written_look_back_is_credited_back_like_the_comb():
    """A written cell's span reaches LOOKBACK_FRAMES back, so its energy is centred half of that
    earlier; with M = 8 at 44.1 kHz that cancels the SBC analysis delay exactly."""
    from sendspin_bridge.services.audio.playout.air_sensor import lookback_delay_us

    assert lookback_delay_us(44100) == pytest.approx(36 / 44100 * 1e6)
    assert lookback_delay_us(48000) == pytest.approx(36 / 48000 * 1e6)
    assert enc_delay_us(8, 44100) - lookback_delay_us(44100) == pytest.approx(0.0, abs=1e-9)


async def test_a_half_cell_lag_keeps_the_sub_cell_bias_within_bounds(air_world, monkeypatch, record_property):
    """At a fractional true lag the parabolic refinement pulls toward the whole cell: about 0.3 ms
    on this fixture at half a cell (0.002 ms at a whole cell). Kept visible with its own bound."""
    monkeypatch.setattr(sys.modules[__name__], "FIRST_MEDIA", FIRST_MEDIA - FRAME_SAMPLES // 2)
    w = air_world(H_true_ms=0.0)
    await w.run(seconds=10)
    h = w.sensor.snapshot()["host_placement_ms"]
    record_property("half_cell_bias_ms", h)
    assert h is not None and abs(h) <= 0.5


async def test_an_m4_stream_sets_the_written_look_back_and_its_credit(air_world):
    """The air side's sub-bands drive the tap's look-back (9·M) and the credit follows it."""
    from sendspin_bridge.services.audio.playout.air_sensor import lookback_delay_us

    w = air_world(H_true_ms=10.0)
    await w.run(seconds=1)
    assert w.tap.lookback_frames == 72
    from dataclasses import replace

    w.sensor.on_envelope(replace(env(epoch=5, t0=w.now, n=4, frame_us=64 * 1e6 / AIR_RATE), subbands=4))
    assert w.tap.lookback_frames == 36
    assert lookback_delay_us(44100, 36) == pytest.approx(enc_delay_us(4, 44100))


async def test_accepted_windows_are_readable(air_world):
    w = air_world(H_true_ms=37.0)
    await w.run(seconds=10)
    wins = w.sensor.accepted_windows()
    assert len(wins) >= 2 and all(abs(x.h_us / 1000.0 - 37.0) < 0.5 and x.psr > 1.0 for x in wins)
    assert wins == tuple(sorted(wins, key=lambda x: x.at_us))


# -- windows carry the written stream time (B2) -------------------------------------------------


async def test_a_window_carries_the_written_span_it_matched(air_world):
    seen: list = []
    w = air_world(H_true_ms=37.0, on_window=lambda win, conf: seen.append((win, conf)))
    await w.run(seconds=10)
    wins = w.sensor.accepted_windows()
    assert wins
    cell_ns = 128e9 / w.fmt.sample_rate
    for win in wins:
        assert win.gen == w.tap.gen
        assert win.x_hi_ns - win.x_lo_ns == pytest.approx(3e9, abs=1.5 * cell_ns)
        # no engine shift here: s(x) = s0 + x / rate, so the span's middle is where s_mid says
        x_mid = (win.s_mid - w.out.s0) * 1e3
        assert (win.x_lo_ns + win.x_hi_ns) / 2 == pytest.approx(x_mid, abs=2 * cell_ns)


async def test_on_window_is_called_per_accepted_window_with_the_confidence(air_world):
    seen: list = []
    w = air_world(H_true_ms=37.0, on_window=lambda win, conf: seen.append((win, conf)))
    await w.run(seconds=10)
    assert len(seen) == w.sensor.snapshot()["windows_confirmed"] >= 2
    # a candidate confirmed by its successor arrives first: both are reported, oldest first
    assert [win for win, _ in seen] == sorted((win for win, _ in seen), key=lambda x: x.at_us)
    assert seen[-1][1] == pytest.approx(w.sensor._confidence("locked", w.now))


async def test_an_on_window_that_raises_changes_nothing(air_world, caplog):
    def boom(win, conf):
        raise RuntimeError("hook")

    ref = air_world(H_true_ms=37.0)
    await ref.run(seconds=10)
    with caplog.at_level("DEBUG", logger=air_sensor_module.__name__):
        w = air_world(H_true_ms=37.0, on_window=boom)
        await w.run(seconds=10)
    assert w.sensor.snapshot() == ref.sensor.snapshot()
    assert any("hook" in r.getMessage() or r.exc_info for r in caplog.records if r.levelname == "DEBUG")


async def test_lock_state_and_key_match_the_snapshot(air_world):
    w = air_world(H_true_ms=37.0)
    assert w.sensor.lock_state()[0] == w.sensor.snapshot()["state"]
    await w.run(seconds=10)
    s = w.sensor.snapshot()
    assert w.sensor.lock_state() == (s["state"], s["confidence"]) and s["state"] == "locked"
    assert w.sensor.key() == (s["codec"], s["rate"], s["adapter"]) and w.sensor.key()[1] is not None


# -- INFO on state changes (A13) -----------------------------------------------------------------


def _info_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "INFO" and r.getMessage().startswith("Air sensor")]


async def test_the_lock_is_logged_with_h_and_psr(air_world, caplog):
    w = air_world(H_true_ms=37.0)
    with caplog.at_level("INFO", logger=air_sensor_module.__name__):
        await w.run(seconds=10)
    locked = [m for m in _info_lines(caplog) if "locked" in m]
    assert len(locked) == 1
    assert "H=37." in locked[0] and "PSR=" in locked[0]


async def test_an_unavailable_state_is_logged_with_its_reason(air_world, caplog):
    w = air_world(H_true_ms=10.0)
    w.source(state="unavailable", reason="no_cap_net_raw")
    with caplog.at_level("INFO", logger=air_sensor_module.__name__):
        await w.run(seconds=1)
    assert _info_lines(caplog) == ["Air sensor: unavailable (no_cap_net_raw)"]


async def test_state_changes_are_logged_at_most_once_a_minute_per_kind(air_world, caplog):
    w = air_world(H_true_ms=10.0)
    with caplog.at_level("INFO", logger=air_sensor_module.__name__):
        for _ in range(3):
            w.source(state="unavailable", reason="monitor_error")
            await w.run(seconds=1)
            w.source(state="unsupported_codec", reason="codec:aac")
            await w.run(seconds=1)
    lines = _info_lines(caplog)
    assert lines == ["Air sensor: unavailable (monitor_error)", "Air sensor: unsupported_codec (codec:aac)"]


# -- written fold (A16b) ------------------------------------------------------------------------
#
# ``click_world`` writes the sync click track through the real tap at 44.1 kHz: track sample ``p``
# is scheduled at the port at ``s(p) = s0 + p/rate`` and heard at ``T = s + D_out``, with ``s0``
# chosen so that click 0's chirp starts (track time LEAD_IN_S) at ``T = click0_heard_raw_us``. The
# fold must give that instant on the click cycle: ``(click0 - LEAD_IN) mod 10 s``.

CLICK_RATE = 44100
CLICK_TRACK_S = 20.0


@lru_cache(maxsize=1)
def click_track_pcm() -> bytes:
    from sendspin_sync import click_track as ct

    return ct.render_pcm(duration_s=CLICK_TRACK_S, sample_rate=CLICK_RATE)


class ClickWorld:
    def __init__(self, *, output_delay_ms: float, click0_heard_raw_us: float, muted: bool | str = False) -> None:
        from sendspin_sync import click_track as ct

        self.tap = WrittenEnvelope()
        self.fmt = InputFormat(CLICK_RATE, 2, 2)
        self.d_out_us = output_delay_ms * 1000.0
        self.s0 = click0_heard_raw_us - ct.LEAD_IN_S * 1e6 - self.d_out_us
        self.now = self.s0
        self.pos = 0  # track samples written
        self.out_start = 0  # track sample of the open output's x = 0
        self.muted = muted
        self.silent = False
        self.sensor = AirSensor(tap=self.tap, now_us=lambda: self.now, output_delay_ms=lambda: output_delay_ms)
        if muted is True or muted == "user":
            self.tap.mute_transition(self.s0 - 1e6, True)  # user mute (or volume 0) before anything plays

    def s(self, pos: float) -> float:
        return self.s0 + pos * 1e6 / CLICK_RATE

    def t(self, track_s: float) -> float:
        """The heard RAW time ``T`` of track second ``track_s``."""
        return self.s0 + track_s * 1e6 + self.d_out_us

    def write_seconds(self, seconds: float) -> None:
        pcm = click_track_pcm()
        block = round(CLICK_RATE * 0.02)
        end = self.pos + round(seconds * CLICK_RATE)
        while self.pos < end:
            n = min(block, end - self.pos)
            x = self.pos - self.out_start
            blk = Block(
                pcm=bytes(n * 4) if self.silent else pcm[self.pos * 4 : (self.pos + n) * 4],
                x0_ns=round(x * 1e9 / CLICK_RATE),
                duration_ns=round(n * 1e9 / CLICK_RATE),
                program=True,
                dropped=0,
                inserted=0,
                late_drop_us=0.0,
            )
            start = self.out_start

            def sched(x_ns: int, start: int = start) -> float:
                return self.s(start + round(x_ns * CLICK_RATE / 1e9))

            self.tap.add_block(blk, self.fmt, sched, lambda _x: "program", self.d_out_us)
            self.pos += n
            self.now = self.s(self.pos)
            if self.muted == "tail" and self.pos >= 6 * CLICK_RATE:
                self.tap.mute_transition(self.s(6 * CLICK_RATE), True)  # MA's tail mute mid-span

    def reopen(self) -> None:
        self.tap.reset()
        self.out_start = self.pos
        self.sensor.on_engine_event("reopen")

    def muted_fraction(self) -> float:
        newest = self.tap.newest_cell()
        assert newest is not None
        sl = self.tap.slice(newest - 1000, newest)
        assert sl is not None
        return sum(sl.muted) / len(sl.muted)


@pytest.fixture
def click_world():
    return ClickWorld


def _cycle_err(a: float, b: float) -> float:
    return (a - b + 5000.0) % 10000.0 - 5000.0


async def test_written_fold_on_the_heard_axis(click_world):
    w = click_world(output_delay_ms=180.0, click0_heard_raw_us=5_000_123_000.0)
    w.write_seconds(14)
    phase, reason = w.sensor.fold(w.t(13.0))
    assert reason is None
    assert phase == pytest.approx((5_000_123_000.0 / 1000.0 - 1000.0) % 10_000.0, abs=0.5)


@pytest.mark.parametrize("muted", ["user", "tail"])
async def test_written_fold_ignores_mute(click_world, muted):
    """MA mutes the speaker during other members' solos; mute acts after the tap, the PCM still has clicks."""
    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0, muted=muted)
    w.write_seconds(14)
    assert w.muted_fraction() > 0.9  # the correlation would skip these cells as muted
    phase, reason = w.sensor.fold(w.t(13.0))
    assert reason is None and abs(_cycle_err(phase, (1_000_000.0 / 1000.0 - 1000.0) % 10_000.0)) <= 0.5  # 0 wraps


async def test_written_fold_needs_5s_since_the_last_reopen(click_world):
    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.write_seconds(14)
    w.reopen()
    w.write_seconds(3)
    assert w.sensor.fold(w.t(16.5)) == (None, "no_written_clicks")


async def test_written_fold_counts_only_cells_inside_the_span(click_world):
    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.write_seconds(14)
    assert w.sensor.fold(w.t(13.0), span_s=4.0) == (None, "no_written_clicks")
    phase, reason = w.sensor.fold(w.t(13.0), span_s=6.0)
    assert reason is None and abs(_cycle_err(phase, 0.0)) <= 0.5


async def test_written_fold_without_clicks_is_no_written_clicks(click_world):
    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.silent = True  # 14 s of program that carries no clicks
    w.write_seconds(14)
    assert w.sensor.fold(w.t(13.0)) == (None, "no_written_clicks")


async def test_written_fold_on_an_empty_tap_is_no_written_clicks(click_world):
    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    assert w.sensor.fold(w.t(13.0)) == (None, "no_written_clicks")


async def test_an_air_fold_command_is_answered_in_the_status(click_world):
    from sendspin_bridge.services.ipc.commands import AirFold

    w = click_world(output_delay_ms=40.0, click0_heard_raw_us=2_000_250_000.0)
    w.write_seconds(14)
    w.sensor.submit(AirFold(id="c1f3", from_us=w.t(1.0), to_us=w.t(13.0)))
    await w.sensor.step()
    fold = w.sensor.snapshot()["fold"]
    assert fold["id"] == "c1f3" and fold["ok"] is True and fold["reason"] is None
    assert abs(_cycle_err(fold["phase_ms"], (2_000_250_000.0 / 1000.0 - 1000.0) % 10_000.0)) <= 0.5


async def test_an_air_fold_uses_the_commands_span(click_world):
    from sendspin_bridge.services.ipc.commands import AirFold

    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.write_seconds(14)
    w.sensor.submit(AirFold(id="short", from_us=w.t(10.0), to_us=w.t(13.0)))  # 3 s: too short
    await w.sensor.step()
    assert w.sensor.snapshot()["fold"] == {"id": "short", "phase_ms": None, "ok": False, "reason": "no_written_clicks"}


async def test_a_new_fold_id_replaces_the_pending_one(click_world):
    from sendspin_bridge.services.ipc.commands import AirFold

    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.write_seconds(14)
    w.sensor.submit(AirFold(id="old", from_us=w.t(1.0), to_us=w.t(13.0)))
    w.sensor.submit(AirFold(id="new", from_us=w.t(1.0), to_us=w.t(13.0)))
    await w.sensor.step()
    assert w.sensor.snapshot()["fold"]["id"] == "new"


async def test_the_fold_runs_off_the_event_loop(click_world, monkeypatch):
    import threading

    from sendspin_bridge.services.ipc.commands import AirFold

    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.write_seconds(14)
    threads = []
    real = air_sensor_module.click_phase_ms

    def spy(*a, **kw):
        threads.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(air_sensor_module, "click_phase_ms", spy)
    w.sensor.submit(AirFold(id="t", from_us=w.t(1.0), to_us=w.t(13.0)))
    await w.sensor.step()
    assert threads and threading.get_ident() not in threads
    assert w.sensor.snapshot()["fold"]["ok"] is True


async def test_a_failing_fold_is_answered_and_leaves_the_sensor_running(click_world, monkeypatch):
    from sendspin_bridge.services.ipc.commands import AirFold

    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    w.write_seconds(14)

    def boom(*a, **kw):
        raise RuntimeError("fold")

    monkeypatch.setattr(air_sensor_module, "click_phase_ms", boom)
    w.sensor.submit(AirFold(id="x", from_us=w.t(1.0), to_us=w.t(13.0)))
    await w.sensor.step()
    snap = w.sensor.snapshot()
    assert snap["fold"] == {"id": "x", "phase_ms": None, "ok": False, "reason": "internal_error"}
    assert snap["reason"] != "internal_error"


async def test_a_written_fold_longer_than_the_ring_is_clamped_to_it(click_world):
    """AirFold allows 30 s; the tap holds 20 s: the span is clamped to the ring (documented)."""
    from sendspin_bridge.services.audio.playout.air_sensor import written_fold_span_s

    w = click_world(output_delay_ms=0.0, click0_heard_raw_us=1_000_000.0)
    assert written_fold_span_s(w.tap, 30.0) == 20.0
    assert written_fold_span_s(w.tap, 12.0) == 12.0
    w.write_seconds(14)
    phase, reason = w.sensor.fold(w.t(13.0), span_s=30.0)
    assert reason is None and abs(_cycle_err(phase, 0.0)) <= 0.5
