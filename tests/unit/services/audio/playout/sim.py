"""Deterministic simulation harness for the playout engine (Task 1.11, extended in Task 3.3).

Time is the RAW clock of a :class:`FakeRaw`. :class:`SimulatedPort` models what sits behind
the engine: ``appsrc`` (the lead the feeder keeps), the sink ring of ``buffer_ms`` and the
port that consumes the ring at ``fs·(1+δ)``. ``A`` (the audio clock) is the ring position
leaving the port, in ns; data delivered after an underrun lands at the current ``A``.
:func:`drive` runs the real engine against it.
"""

from __future__ import annotations

import bisect
import random
from collections import deque
from typing import TYPE_CHECKING

from sendspin_bridge.services.audio.playout.air_bias import AirSample
from sendspin_bridge.services.audio.playout.sensor import PortSample

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.audio.playout.air_bias import AirFeed
    from sendspin_bridge.services.audio.playout.engine import PlayoutEngine
    from sendspin_bridge.services.audio.playout.scheduler import Block

_OUT_FRAME_BYTES = 4  # the output stream is S16LE stereo


class FakeRaw:
    def __init__(self, t_us: float = 0.0) -> None:
        self.t_us = t_us

    def advance(self, dt_us: float) -> None:
        self.t_us += dt_us


def _first_nonzero_frame(pcm: bytes) -> int | None:
    stripped = pcm.lstrip(b"\x00")
    if not stripped:
        return None
    return (len(pcm) - len(stripped)) // _OUT_FRAME_BYTES


class SimulatedPort:
    """Consumes the engine's output behind a ring of ``buffer_ms`` at fs·(1+δ) in RAW time; reports A(t)."""

    def __init__(
        self,
        raw: FakeRaw,
        *,
        buffer_ms: float = 600.0,
        delta_ppm: float = 0.0,
        noise_us: float = 0.0,
        seed: int = 1,
    ) -> None:
        self._raw = raw
        self._buffer_ns = buffer_ms * 1e6
        self._rate = 1.0 + delta_ppm * 1e-6  # port ns per RAW ns
        self._noise_us = noise_us
        self._rng = random.Random(seed)
        self._a_ns = 0.0  # ring position leaving the port
        self._t_anchor: float | None = None  # RAW µs up to which _a_ns is computed; None = not started
        self._stall_until = -1.0
        self._starve_until = -1.0
        self._write_ns = 0.0  # ring write position
        self.delivered_ns = 0  # output stream time handed to the sink
        self._appsrc: deque[Block] = deque()
        self._appsrc_ns = 0
        self._target_ns: float | None = None  # ring position of the first non-zero frame
        self._heard_nonzero: float | None = None
        self._first_out: float | None = None
        self.underrun_ns = 0.0  # silence the port played because the ring was empty
        # Reading errors of the reported clock (the true port is unaffected): a sawtooth
        # (amplitude ns, period µs, start µs) and linear slews (start µs, length µs, ns).
        self._saw: tuple[float, float, float] | None = None
        self._slews: list[tuple[float, float, float]] = []
        self.next_sense_us: float | None = None  # drive()'s sensor schedule, kept across calls
        # Called with the stream position of each delivery as it lands, before it is written
        # (the GStreamer feeder's buffer probe on appsrc's src pad).
        self.on_arrival: Callable[[int], None] | None = None
        # Truth log: A(t) as breakpoints (RAW µs, ring ns; piecewise linear, stalls flat, skips as
        # steps) and, per delivery, (output stream time x0, ring position) of its first frame.
        self._a_t: list[float] = []
        self._a_v: list[float] = []
        self._dl_x: list[int] = []
        self._dl_ring: list[float] = []

    def _log_a(self, t_us: float, a_ns: float) -> None:
        self._a_t.append(t_us)
        self._a_v.append(a_ns)

    # -- time -----------------------------------------------------------------------------

    def _sync(self) -> None:
        t = self._raw.t_us
        if self._t_anchor is None:
            return
        run_from = max(self._t_anchor, self._stall_until)
        run_us = max(0.0, t - run_from)
        a_new = self._a_ns + run_us * 1000.0 * self._rate
        if run_from > self._t_anchor and run_from <= t:  # the stall ended inside this span
            self._log_a(run_from, self._a_ns)
        self._log_a(t, a_new)
        if self._target_ns is not None and self._heard_nonzero is None and self._a_ns < self._target_ns <= a_new:
            self._heard_nonzero = run_from + (self._target_ns - self._a_ns) / (1000.0 * self._rate)
        self._a_ns = a_new
        self._t_anchor = t

    # -- the sink -------------------------------------------------------------------------

    def deliver(self, duration_ns: int, pcm: bytes | None = None) -> None:
        """Data handed to the sink (lands at the ring write position, or at A after an underrun)."""
        self._sync()
        if self._t_anchor is None:
            self._t_anchor = self._raw.t_us
            self._first_out = self._raw.t_us
            self._log_a(self._raw.t_us, 0.0)
        elif self.on_arrival is not None:
            self.on_arrival(self.delivered_ns)
        if self._a_ns > self._write_ns:
            self.underrun_ns += self._a_ns - self._write_ns
            self._write_ns = self._a_ns
        if pcm and self._target_ns is None:
            j = _first_nonzero_frame(pcm)
            if j is not None:
                frames = len(pcm) // _OUT_FRAME_BYTES
                self._target_ns = self._write_ns + j * duration_ns / frames
        self._dl_x.append(self.delivered_ns)
        self._dl_ring.append(self._write_ns)
        self._write_ns += duration_ns
        self.delivered_ns += duration_ns

    def read(self) -> PortSample:
        """PortClock protocol: the audio clock (plus noise) bracketed at the RAW time now."""
        self._sync()
        a = self._a_ns
        if self._noise_us:
            a += self._rng.gauss(0.0, self._noise_us) * 1000.0
        t = self._raw.t_us
        a += self._reading_error_ns(t)
        return PortSample(t_us=t, a_ns=int(a), mono_us=t, bracket_us=0.0)

    def skip(self, ms: float) -> None:
        """PA "Skipping": A jumps forward by ``ms`` (negative: the port falls behind by ``-ms``)."""
        self._sync()
        if self._t_anchor is not None:
            self._log_a(self._raw.t_us, self._a_ns)
        self._a_ns += ms * 1e6
        if self._t_anchor is not None:
            self._log_a(self._raw.t_us, self._a_ns)

    def sawtooth(self, amplitude_ms: float, period_s: float) -> None:
        """The reported clock lags the port by a ramp to ``amplitude_ms`` that resets every ``period_s``.

        The pulsesink clock under PipeWire (stand experiment E1): the smoother extrapolates
        slow, then a timing update steps it forward. The audio itself does not drift.
        """
        self._saw = (amplitude_ms * 1e6, period_s * 1e6, self._raw.t_us)

    def slew(self, ms: float, over_ms: float) -> None:
        """From now on the reported clock moves by ``ms`` relative to the port, linearly over ``over_ms``.

        Negative: the reading falls behind the port (audio looks late), as when a smoother
        that ran ahead during an underrun converges back once data flows again.
        """
        self._slews.append((self._raw.t_us, over_ms * 1000.0, ms * 1e6))

    def _reading_error_ns(self, t: float) -> float:
        err = 0.0
        if self._saw is not None:
            amp, period, t0 = self._saw
            err -= amp * (((t - t0) % period) / period)
        for t0, length, size in self._slews:
            if t > t0:
                err += size * min(1.0, (t - t0) / length)
        return err

    def starve(self, ms: float) -> None:
        """Deliveries are held back for the next ``ms`` (the port keeps playing)."""
        self._starve_until = self._raw.t_us + ms * 1000.0

    def stall(self, ms: float) -> None:
        """The port stops consuming for ``ms`` (sink suspended), then resumes where it was."""
        self._sync()
        self._stall_until = self._raw.t_us + ms * 1000.0

    # -- appsrc in front of the sink ------------------------------------------------------

    @property
    def appsrc_ns(self) -> int:
        return self._appsrc_ns

    def push(self, block: Block) -> None:
        self._appsrc.append(block)
        self._appsrc_ns += block.duration_ns

    def pump(self) -> None:
        """Move blocks from appsrc into the ring while it has room."""
        if self._raw.t_us < self._starve_until:
            return
        self._sync()
        while self._appsrc and (self._t_anchor is None or self._write_ns - self._a_ns < self._buffer_ns):
            block = self._appsrc.popleft()
            self._appsrc_ns -= block.duration_ns
            self.deliver(block.duration_ns, block.pcm)

    # -- observation ----------------------------------------------------------------------

    def true_port_time_us(self, x_ns: float) -> float | None:
        """RAW time the output frame at stream time ``x_ns`` left the (true) port.

        The ring position of the frame comes from the delivery that carried it (a delivery after
        an underrun lands at the then-current A, so gaps are in the log); ``A(t)`` is the logged
        piecewise-linear clock, inverted. None until the frame has left the port. Only forward
        skips are supported (``A`` stays non-decreasing).
        """
        self._sync()
        if not self._dl_x or x_ns < 0 or x_ns >= self.delivered_ns:
            return None
        i = bisect.bisect_right(self._dl_x, x_ns) - 1
        ring = self._dl_ring[i] + (x_ns - self._dl_x[i])
        j = bisect.bisect_left(self._a_v, ring)
        if j >= len(self._a_v):
            return None  # not played yet
        if j == 0:
            return self._a_t[0]
        a0, a1 = self._a_v[j - 1], self._a_v[j]
        t0, t1 = self._a_t[j - 1], self._a_t[j]
        if a1 <= a0:
            return t1
        return t0 + (t1 - t0) * (ring - a0) / (a1 - a0)

    def heard_at_us(self, *, first_nonzero: bool = True) -> float | None:
        """RAW time the first non-zero frame (or the first frame at all) left the port."""
        self._sync()
        return self._heard_nonzero if first_nonzero else self._first_out


def drive(
    engine: PlayoutEngine,
    port: SimulatedPort,
    raw: FakeRaw,
    seconds: float,
    *,
    lead_ms: float = 40.0,
    block_ms: float = 10.0,
    sense_every_ms: float = 100.0,
    step_ms: float = 1.0,
    on_sense: Callable[[], None] | None = None,
    report_before_pump: bool = False,
    arrivals: bool = True,
    on_block: Callable[[Block], None] | None = None,
) -> None:
    """Run the real engine in RAW time: blocks to keep the lead, sense at 10 Hz.

    Every step the feeder reports ``delivered`` to the engine. ``report_before_pump``: the sink
    takes data only after that report (the other order of the feeder's race with the streaming
    thread), so a delivery is first seen a step later or by the sensor. ``arrivals``: deliveries
    are reported as they land (the feeder's buffer probe) while the engine asks for them.
    ``on_block`` is called with every rendered block right after the engine produced it.
    """
    del block_ms  # the engine's scheduler decides the block size
    end = raw.t_us + seconds * 1e6
    lead_ns = lead_ms * 1e6
    sense_every_us = sense_every_ms * 1000.0
    next_sense = port.next_sense_us if port.next_sense_us is not None else raw.t_us + sense_every_us
    if arrivals:

        def on_arrival(x_ns: int) -> None:
            if engine.arrival_wanted(raw.t_us):
                engine.note_arrival(x_ns, port.read())

        port.on_arrival = on_arrival
    else:
        port.on_arrival = None
    while raw.t_us < end:
        if not report_before_pump:
            port.pump()
        while port.appsrc_ns < lead_ns:
            block = engine.next_block(raw.t_us)
            if on_block is not None:
                on_block(block)
            port.push(block)
            if not report_before_pump:
                port.pump()
        engine.delivered(raw.t_us, port.delivered_ns)  # the feeder reports every tick
        if report_before_pump:
            port.pump()
        if raw.t_us >= next_sense:
            engine.sense(raw.t_us, port.delivered_ns)
            next_sense += sense_every_us
            if on_sense is not None:
                on_sense()
        raw.advance(step_ms * 1000.0)
    port.next_sense_us = next_sense


class SimAir:
    """Air windows from the simulation's truth, delivered to an ``AirFeed`` like the daemon would.

    ``t_air(x) = true_port_time_us(x) + h0_us``; a window of ``window_s`` of air ending
    ``pipeline_us`` before ``now`` yields ``H = t_air(x_mid) - s(x_mid) + N(0, sigma_h)`` where ``s``
    is the engine's schedule recorded at write time (like the tap). ``heard_error_us`` is the
    truth the tests judge: ``t_air + L_true - (s + d_out)`` = sound minus client time.
    """

    def __init__(
        self,
        feed: AirFeed,
        port: SimulatedPort,
        engine: PlayoutEngine,
        *,
        h0_us: float,
        sigma_h_us: float = 500.0,
        seed: int = 1,
        window_s: float = 3.0,
        hop_s: float = 1.0,
        pipeline_us: float = 250_000.0,
        acquire_s: float = 4.0,
        d_out_us: float = 184_000.0,
        tap_gen: int = 1,
        confidence: float = 1.0,
    ) -> None:
        self._feed = feed
        self._port = port
        self._engine = engine
        self.h0_us = h0_us
        self._sigma = sigma_h_us
        self._rng = random.Random(seed)
        self._window_us = window_s * 1e6
        self._hop_us = hop_s * 1e6
        self._pipeline_us = pipeline_us
        self._acquire_us = acquire_s * 1e6
        self._d_out_us = d_out_us
        self._gen = tap_gen
        self._confidence = confidence
        self._x_w = 0
        self._rec_x: list[int] = []  # x0 of each recorded block
        self._rec: list[tuple[int, int, float, float]] = []  # x0, duration, s(x0), d_out
        self._next_tick_us: float | None = None
        self._stopped = False
        self._stopped_at_us: float | None = None
        self._resumed_at_us: float | None = None
        self._lock_lost_sent = False
        self.first_audio_x_ns: int | None = None
        self.windows_sent = 0
        self._feed.set_output_delay(d_out_us / 1000.0)

    # -- recording ------------------------------------------------------------------------

    def on_block(self, block: Block) -> None:
        x0 = self._x_w
        self._x_w += block.duration_ns
        if self.first_audio_x_ns is None and block.pcm.lstrip(b"\x00"):
            self.first_audio_x_ns = x0
        s0 = self._engine.schedule_at(x0)
        if s0 is None:
            return
        self._rec_x.append(x0)
        self._rec.append((x0, block.duration_ns, s0, self._d_out_us))

    def _row(self, x_ns: float) -> tuple[int, int, float, float] | None:
        i = bisect.bisect_right(self._rec_x, x_ns) - 1
        if i < 0:
            return None
        row = self._rec[i]
        return row if x_ns < row[0] + row[1] else None

    def s_at(self, x_ns: float) -> float | None:
        row = self._row(x_ns)
        return None if row is None else row[2] + (x_ns - row[0]) / 1000.0

    def d_out_at(self, x_ns: float) -> float | None:
        row = self._row(x_ns)
        return None if row is None else row[3]

    def t_air(self, x_ns: float) -> float | None:
        t = self._port.true_port_time_us(x_ns)
        return None if t is None else t + self.h0_us

    def set_d_out(self, d_out_us: float) -> None:
        self._d_out_us = d_out_us
        self._feed.set_output_delay(d_out_us / 1000.0)

    # -- the sensor -----------------------------------------------------------------------

    def stop(self) -> None:
        self._stopped = True
        self._stopped_at_us = self._engine_now_us()
        self._lock_lost_sent = False

    def resume(self) -> None:
        self._stopped = False
        self._resumed_at_us = self._engine_now_us()
        self._next_tick_us = None

    def _engine_now_us(self) -> float:
        return self._port._raw.t_us

    def _x_at_air_time(self, t_air_us: float) -> float | None:
        """The stream time whose frame was in the air at ``t_air_us`` (bisection on the true port)."""
        t_port = t_air_us - self.h0_us
        lo, hi = 0.0, float(self._port.delivered_ns)
        first = self._port.true_port_time_us(0.0)
        if first is None or t_port < first:
            return None
        for _ in range(48):
            mid = (lo + hi) / 2.0
            tm = self._port.true_port_time_us(mid)
            if tm is not None and tm <= t_port:
                lo = mid
            else:
                hi = mid
        return lo

    def tick(self, now_us: float) -> None:
        if self._stopped:
            if not self._lock_lost_sent and self._stopped_at_us is not None and now_us - self._stopped_at_us >= 20e6:
                self._feed.set_lock("no_lock", None)
                self._lock_lost_sent = True
            return
        if self._next_tick_us is not None and now_us < self._next_tick_us:
            return
        first = self._port.true_port_time_us(0.0)
        if first is None:
            return
        flow_from = first + self.h0_us
        if self._resumed_at_us is not None:
            flow_from = max(flow_from, self._resumed_at_us)
        t_hi = now_us - self._pipeline_us
        if t_hi - flow_from < self._acquire_us:
            return
        self._next_tick_us = now_us + self._hop_us if self._next_tick_us is None else self._next_tick_us + self._hop_us
        if self._next_tick_us <= now_us:
            self._next_tick_us = now_us + self._hop_us
        t_lo = t_hi - self._window_us
        x_lo = self._x_at_air_time(t_lo)
        x_hi = self._x_at_air_time(t_hi)
        if x_lo is None or x_hi is None:
            return
        t_mid = (t_lo + t_hi) / 2.0
        x_mid = self._x_at_air_time(t_mid)
        if x_mid is None:
            return
        s = self.s_at(x_mid)
        d_out = self.d_out_at(x_mid)
        if s is None or d_out is None:
            return
        h = t_mid - s + self._rng.gauss(0.0, self._sigma)
        self._feed.push(AirSample(self._gen, x_lo, x_hi, t_mid, h, d_out, self._confidence))
        self._feed.set_lock("locked", self._confidence)
        self.windows_sent += 1

    def heard_error_us(self, x_ns: float, l_true_ms: float) -> float:
        """Truth: ``t_air + L_true - (s + d_out)``, the sound minus the client time of frame ``x``."""
        t = self.t_air(x_ns)
        s = self.s_at(x_ns)
        d_out = self.d_out_at(x_ns)
        if t is None or s is None or d_out is None:
            raise ValueError(f"no truth for x={x_ns}")
        return t + l_true_ms * 1000.0 - (s + d_out)
