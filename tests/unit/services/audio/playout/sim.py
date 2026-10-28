"""Deterministic simulation harness for the playout engine (Task 1.11, extended in Task 3.3).

Time is the RAW clock of a :class:`FakeRaw`. :class:`SimulatedPort` models what sits behind
the engine: ``appsrc`` (the lead the feeder keeps), the sink ring of ``buffer_ms`` and the
port that consumes the ring at ``fs·(1+δ)``. ``A`` (the audio clock) is the ring position
leaving the port, in ns; data delivered after an underrun lands at the current ``A``.
:func:`drive` runs the real engine against it.
"""

from __future__ import annotations

import random
from collections import deque
from typing import TYPE_CHECKING

from sendspin_bridge.services.audio.playout.sensor import PortSample

if TYPE_CHECKING:
    from collections.abc import Callable

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

    # -- time -----------------------------------------------------------------------------

    def _sync(self) -> None:
        t = self._raw.t_us
        if self._t_anchor is None:
            return
        run_from = max(self._t_anchor, self._stall_until)
        run_us = max(0.0, t - run_from)
        a_new = self._a_ns + run_us * 1000.0 * self._rate
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
        self._a_ns += ms * 1e6

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
) -> None:
    """Run the real engine in RAW time: blocks to keep the lead, sense at 10 Hz.

    Every step the feeder reports ``delivered`` to the engine. ``report_before_pump``: the sink
    takes data only after that report (the other order of the feeder's race with the streaming
    thread), so a delivery is first seen a step later or by the sensor. ``arrivals``: deliveries
    are reported as they land (the feeder's buffer probe) while the engine asks for them.
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
            port.push(engine.next_block(raw.t_us))
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
