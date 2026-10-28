"""PlayoutPlayer: StreamPlayer's daemon-facing interface on the playout engine.

GStreamer only carries audio here. The pipeline
``appsrc ! audioconvert ! audioresample ! capsfilter(S16LE, 2 ch, out_rate) ! pulsesink``
runs with ``sync=false`` on the system clock; the sink's own audio clock is read as a
sensor (``GstPortClock``). What is written, and when, is decided by the ``PlayoutEngine``.

Threading: every public method enqueues a command under ``_cmd_lock`` and returns. One
feeder thread per output (``_run``, a 5 ms ``Event.wait``) applies the commands, keeps
``appsrc`` ``LEAD_MS`` ahead, calls ``engine.sense()`` every 100 ms with
``delivered = x_w − current-level-time`` and publishes what ``metrics()`` reports under
``_pub_lock``. The engine and every GStreamer element are touched by the feeder only;
the caller thread builds the output (``prepare_output``), closes it (``stop``, after the
feeder joined) and reads the sink's audio clock (``audio_clock_ns``, a read-only clock
query, as in ``StreamPlayer``).
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from sendspin_bridge.services.audio.playout.sensor import GstPortClock, PortSample

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.audio.player.player import PcmFormat
    from sendspin_bridge.services.audio.playout.air_tap import WrittenEnvelope
    from sendspin_bridge.services.audio.playout.scheduler import Block
    from sendspin_bridge.services.audio.playout.sensor import PortClock

logger = logging.getLogger(__name__)

LEAD_MS = 40  # appsrc is kept this far ahead of what the sink took
BLOCK_MS = 10  # the engine renders blocks of this length (PlayoutScheduler's default)
FEEDER_TICK_S = 0.005
SENSE_INTERVAL_US = 100_000.0
FALLBACK_AFTER_US = 2_000_000.0  # no audio clock from the port this long after PLAYING
DEFAULT_OUT_RATE = 48000
# Blocks written per feeder tick at most: an 800 ms ring fills in a few ticks.
MAX_BLOCKS_PER_TICK = 16
# A sink that never blocks (fakesink, a broken stream) is fed no faster than the elapsed
# time plus its buffer, the lead and this slack; a real port never reaches it (the port
# cannot consume faster than real time by more than R_max = 1000 ppm).
WRITE_AHEAD_SLACK_US = 100_000.0
WRITE_AHEAD_RATE = 1.001
JOIN_TIMEOUT_S = 2.0

_LEAD_NS = LEAD_MS * 1_000_000
# After a clear the stream stays muted until the port is this far past the clear's write
# head, so the old audio already in the PulseAudio buffer is not heard (R13).
TAIL_MUTE_MARGIN_NS = 20_000_000
_FALLBACK_NO_CLOCK = "no audio clock from the sink within 2 s of PLAYING"


def raw_now_us() -> int:
    """Now on the RAW clock (``CLOCK_MONOTONIC_RAW`` µs): the play-time axis of the engine and the air sensor."""
    clock_id = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)
    return time.clock_gettime_ns(clock_id) // 1000


def k0_from_latencies(*, resample_ns: int | None, convert_ns: int | None) -> int:
    """The port origin ``k₀``: the latency audioresample adds (0 when it passes through).

    Both values are latency queries answered upstream from the element's src pad, so
    their difference is audioresample's own share (appsrc's and audioconvert's cancel).
    """
    if resample_ns is None:
        return 0
    return max(0, int(resample_ns) - int(convert_ns or 0))


def sink_rate_from_pactl(listing: str, device: str) -> int | None:
    """The sample rate of ``device`` in ``pactl list short sinks`` output, else None."""
    for line in listing.splitlines():
        fields = line.split("\t")
        if len(fields) >= 4 and fields[1] == device:
            match = re.search(r"(\d+)Hz", fields[3])
            return int(match.group(1)) if match else None
    return None


def _pulse_sink_rate(device: str) -> int | None:
    # pulsectl's ``sample_spec`` points at memory freed after the query
    # returns (HAOS read 32655 Hz, 96 channels), so ask pactl instead.
    listing = subprocess.run(
        ["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=2, check=True
    ).stdout
    return sink_rate_from_pactl(listing, device)


def output_rate(device: str | None, *, probe: Callable[[str], int | None] = _pulse_sink_rate) -> int:
    """The sink's sample rate from its PulseAudio sample spec, else 48000."""
    if not device:
        return DEFAULT_OUT_RATE
    try:
        rate = probe(device)
    except Exception as exc:
        logger.debug("playout: sample rate of %s unavailable (%s); using %d Hz", device, exc, DEFAULT_OUT_RATE)
        return DEFAULT_OUT_RATE
    return int(rate) if rate else DEFAULT_OUT_RATE


@dataclass(frozen=True, slots=True)
class PlayoutOutput:
    """A built, playing output not yet adopted by a player (``prepare_output``)."""

    pipeline: Any
    appsrc: Any
    sink: Any
    out_rate: int
    k0_ns: int
    buffer_time_us: int


@dataclass(slots=True)
class _Counters:
    """What the feeder publishes for ``metrics()`` (under the player's ``_pub_lock``)."""

    buffered_ns: int = 0
    position_us: int | None = None
    sync_error_ms: object = None
    output_latency_ms: object = None
    discontinuities: int = 0
    rendered: int = 0
    resyncs: int = 0
    queued_chunks: int = 0
    overdue_dropped_us: float = 0.0

    def as_metrics(self) -> dict[str, object]:
        """StreamPlayer.metrics()'s keys."""
        return {
            "backend_output_latency_ms": self.output_latency_ms,
            "buffered_audio_ms": round(self.buffered_ns / 1e6, 3),
            "playback_position_us": self.position_us,
            "playback_sync_error_ms": self.sync_error_ms,
            "discontinuity_count": self.discontinuities,
            "rendered_buffers": self.rendered,
            "resync_count": self.resyncs,
            "in_place_shift_count": 0,  # the engine never shifts in place: a resync places exactly
            "queued_chunks": self.queued_chunks,
            "overdue_dropped_ms": round(self.overdue_dropped_us / 1000.0, 3),
        }


@dataclass(frozen=True, slots=True)
class _EngineMarks:
    """The engine counters the air listener has already been told about."""

    underruns: int = 0
    pa_steps: int = 0
    suspended: bool = False


class _WatchedPort:
    """The port clock the engine reads, remembering the last reading for the player."""

    def __init__(self, port: PortClock) -> None:
        self._port = port
        self.last: PortSample | None = None
        self.ever = False

    def read(self) -> PortSample | None:
        sample = self._port.read()
        self.last = sample
        if sample is not None:
            self.ever = True
        return sample


class PlayoutPlayer:
    """StreamPlayer's daemon-facing interface on the playout engine."""

    def __init__(
        self,
        *,
        sink_factory: Callable[[], Any],
        settings: PlayoutSettings,
        out_rate: int | None = None,
        raw_now_us: Callable[[], float] = raw_now_us,
        port_factory: Callable[[Any], PortClock] = GstPortClock,
        thread_factory: Callable[..., Any] = threading.Thread,
        air_tap: WrittenEnvelope | None = None,
        output_delay_us: Callable[[], float] | None = None,
        on_engine_event: Callable[[str], None] | None = None,
    ) -> None:
        self._sink_factory = sink_factory
        self._settings = settings
        self._out_rate = out_rate
        self._raw_now_us = raw_now_us
        self._port_factory = port_factory
        self._thread_factory = thread_factory
        # The air sensor's tap on the written PCM; None (switch off) = no hook at all.
        self._air_tap = air_tap
        self._output_delay_us = output_delay_us
        # The air sensor's listener for output reopens and engine events (feeder or caller
        # thread); None (switch off) = the engine's counters are never even compared.
        self._on_engine_event = on_engine_event

        # Caller-thread state (set by start/adopt/stop only).
        self._out: PlayoutOutput | None = None
        self._pipeline: Any = None
        self._appsrc: Any = None
        self._sink: Any = None
        self._format: PcmFormat | None = None
        self._volume: tuple[float, bool] | None = None
        self._generation = 0
        self._first_lead_us: float | None = None
        self._stream_closed = False
        self._thread: Any = None
        self._stop_evt: threading.Event | None = None

        # Commands for the feeder.
        self._cmd_lock = threading.Lock()
        self._commands: deque[tuple[Any, ...]] = deque()

        # Feeder-thread state (reset by adopt before the feeder starts).
        self._engine: PlayoutEngine | None = None
        self._port: _WatchedPort | None = None
        self._playing_at_us: float | None = None
        self._next_sense_us: float | None = None
        self._x_w_ns = 0
        self._input_format_set = False
        self._feeder_failed = False
        self._feeder_volume: tuple[float, bool] | None = None  # the user's, as last applied
        self._tail_until_ns: int | None = None  # muted until the port passes this x
        self._input_format: InputFormat | None = None
        self._air_muted: bool | None = None  # effective mute last handed to the air tap
        self._air_seen = _EngineMarks()  # engine counters last forwarded to the listener

        # Published for metrics()/resync()/is_drained().
        self._pub_lock = threading.Lock()
        self._counters = _Counters()
        self._playout: dict[str, object] = self._idle_playout()
        self._queued = False

    # -- daemon-facing output lifecycle -----------------------------------------------------

    @property
    def stream_generation(self) -> int:
        """How many outputs this player has adopted so far."""
        return self._generation

    @property
    def awaiting_reopen(self) -> PcmFormat | None:
        """Always None: a resync never closes the output, so nothing waits to be reopened."""
        return None

    def forget_reopen(self) -> None:
        """Nothing to forget (see ``awaiting_reopen``)."""

    def needs_output(self) -> bool:
        """Whether the next ``start`` has to open an output (the slow part)."""
        return self._out is None

    def now_us(self) -> int:
        """Now on the play-time (RAW) clock."""
        return int(self._raw_now_us())

    def start(self, audio_format: PcmFormat) -> None:
        """Open the output once (if none is open), then set the input format.

        A format change keeps the output: only appsrc's caps change, applied by the feeder.
        """
        self._stream_closed = False
        if self._out is None:
            self.adopt_output(self.prepare_output(audio_format), audio_format)
            return
        if audio_format != self._format:
            self._enqueue_format(audio_format)

    def prepare_output(self, audio_format: PcmFormat) -> PlayoutOutput:
        """Build a playing output without touching the player's state.

        Safe to run in a worker thread (opening a Bluetooth sink's PulseAudio stream can
        take a second or more); ``adopt_output`` then makes it current. The input format
        is not applied here: the feeder sets it once the output is adopted.
        """
        from sendspin_bridge.services.audio.player.gst_support import Gst
        from sendspin_bridge.services.audio.player.pipeline import build_playout_pipeline

        del audio_format  # the output format is the sink's, whatever the input
        sink = self._sink_factory()
        device = sink.get_property("device") if sink.find_property("device") is not None else None
        rate = self._out_rate or output_rate(device)
        pipeline, appsrc = build_playout_pipeline(sink=sink, out_rate=rate)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("failed to set GStreamer playout pipeline to PLAYING")
        pipeline.get_state(Gst.SECOND)
        k0_ns = k0_from_latencies(
            resample_ns=_src_latency_ns(pipeline.get_by_name("resample")),
            convert_ns=_src_latency_ns(pipeline.get_by_name("convert")),
        )
        buffer_time_us = int(sink.get_property("buffer-time")) if sink.find_property("buffer-time") is not None else 0
        return PlayoutOutput(
            pipeline=pipeline, appsrc=appsrc, sink=sink, out_rate=rate, k0_ns=k0_ns, buffer_time_us=buffer_time_us
        )

    def adopt_output(self, output: PlayoutOutput, audio_format: PcmFormat) -> None:
        """Make a prepared output the current one and start its feeder thread."""
        self.stop("replaced by a new output")
        self._out = output
        self._pipeline = output.pipeline
        self._appsrc = output.appsrc
        self._sink = output.sink
        self._port = _WatchedPort(self._port_factory(output.sink))
        self._engine = PlayoutEngine(settings=self._settings, port=self._port, k0_ns=output.k0_ns)
        self._playing_at_us = None
        self._next_sense_us = None
        self._x_w_ns = 0
        self._input_format_set = False
        self._feeder_failed = False
        self._feeder_volume = None
        self._tail_until_ns = None
        self._input_format = None
        self._air_muted = None
        self._air_seen = _EngineMarks()
        self._generation += 1
        self._first_lead_us = None
        self._stream_closed = False
        self._format = None
        with self._pub_lock:
            self._counters = _Counters()
            self._playout = self._idle_playout()
            self._queued = False
        with self._cmd_lock:
            self._commands.clear()
        self._reopen_air_tap()
        self._enqueue_format(audio_format)
        if self._volume is not None:
            self._enqueue(("volume", *self._volume))
        logger.info(
            "playout output open: %d Hz, buffer %d ms, k0 %d ns",
            output.out_rate,
            output.buffer_time_us // 1000,
            output.k0_ns,
        )
        stop_evt = threading.Event()
        self._stop_evt = stop_evt
        self._thread = self._thread_factory(target=self._run, args=(stop_evt,), name="playout-feeder", daemon=True)
        self._thread.start()

    def _reopen_air_tap(self) -> None:
        """Every adopted output restarts ``x`` at 0: forget the tap's cells, tell the listener."""
        tap = self._air_tap
        if tap is not None:
            try:
                tap.reset()
            except Exception:
                self._air_tap_failed(tap)
        self._emit_engine_event("reopen")

    def _emit_engine_event(self, kind: str) -> None:
        """Hand one event to the listener; nothing it does reaches the caller (or the feeder)."""
        listener = self._on_engine_event
        if listener is None:
            return
        try:
            listener(kind)
        except Exception:
            logger.debug("playout: engine event listener failed", exc_info=True)

    def _forward_engine_events(self, snap: dict[str, object]) -> None:
        """New underruns, PA steps and the start of a stall since the last snapshot, as events."""
        seen = self._air_seen
        underruns, pa_steps = snap.get("underruns"), snap.get("pa_steps")
        suspended = snap.get("state") == "suspended"
        if isinstance(underruns, int) and underruns > seen.underruns:
            self._emit_engine_event("underrun")
        if isinstance(pa_steps, int) and pa_steps > seen.pa_steps:
            self._emit_engine_event("pa_step")
        if suspended and not seen.suspended:
            self._emit_engine_event("stall")
        self._air_seen = _EngineMarks(
            underruns if isinstance(underruns, int) else seen.underruns,
            pa_steps if isinstance(pa_steps, int) else seen.pa_steps,
            suspended,
        )

    @staticmethod
    def discard_output(output: PlayoutOutput) -> None:
        """Close a prepared output that will not be used."""
        from sendspin_bridge.services.audio.player.gst_support import Gst

        output.pipeline.set_state(Gst.State.NULL)

    # -- stream commands --------------------------------------------------------------------

    def submit(self, play_time_us: int, payload: bytes) -> None:
        if self._out is None:
            raise RuntimeError("player is not started")
        if self._first_lead_us is None:
            self._first_lead_us = play_time_us - self._raw_now_us()
        self._enqueue(("submit", play_time_us, payload))

    def clear(self) -> None:
        if self._out is None:
            return
        self._enqueue(("clear",))

    def resync(self, shift_us: int) -> bool:
        """Shift queued audio by ``shift_us`` on the running output; True if audio was queued.

        The output is never rebuilt: the engine places the next frame exactly.
        """
        if self._out is None:
            return False
        with self._cmd_lock:
            playing = self._queued_after_pending()
            self._commands.append(("resync", shift_us))
        return playing

    def end_stream(self) -> None:
        """Stage 1: the same as ``stop()`` (Stage 2 holds the output warm instead)."""
        self.stop("end of stream")

    def close_stream(self) -> None:
        """Compatibility no-op: marks the stream closed, keeps the output running."""
        self._stream_closed = True

    def set_volume(self, value: float, muted: bool) -> None:
        """The stream's own volume (linear) and mute, kept for later outputs too."""
        self._volume = (max(0.0, min(1.0, float(value))), bool(muted))
        if self._out is not None:
            self._enqueue(("volume", *self._volume))

    def stop(self, reason: str = "stop") -> None:
        """Join the feeder and close the output; idempotent."""
        thread, self._thread = self._thread, None
        if thread is not None:
            if self._stop_evt is not None:
                self._stop_evt.set()
            if thread is not threading.current_thread():
                thread.join(timeout=JOIN_TIMEOUT_S)
                if thread.is_alive():
                    logger.warning("playout feeder did not stop within %.0f s", JOIN_TIMEOUT_S)
        self._stop_evt = None
        out, self._out = self._out, None
        self._pipeline = self._appsrc = self._sink = None
        self._format = None
        self._engine = None
        self._port = None
        with self._cmd_lock:
            self._commands.clear()
        with self._pub_lock:
            self._queued = False
            if self._playout.get("state") != "fallback":
                self._playout = {**self._playout, "state": "idle"}
        if out is not None:
            from sendspin_bridge.services.audio.player.gst_support import Gst

            out.pipeline.set_state(Gst.State.NULL)
            logger.info("playout output closed (%s)", reason)

    def is_drained(self) -> bool:
        """No program audio waits to be written (silence fill does not count)."""
        if self._out is None:
            return True
        with self._cmd_lock:
            return not self._queued_after_pending()

    # -- reports ----------------------------------------------------------------------------

    def metrics(self) -> dict[str, object]:
        """StreamPlayer's keys plus ``playout``: the engine's status snapshot."""
        with self._pub_lock:
            return {**self._counters.as_metrics(), "playout": dict(self._playout)}

    def start_report(self) -> dict[str, float | None] | None:
        """The stream-start facts StreamPlayer reports, as far as they apply (None: no output)."""
        out = self._out
        if out is None:
            return None
        with self._pub_lock:
            overdue = round(self._counters.overdue_dropped_us / 1000.0, 3)
        return {
            "compensated_latency_ms": None,
            "configured_latency_ms": out.buffer_time_us / 1000.0 if out.buffer_time_us else None,
            "min_latency_ms": None,
            "first_lead_ms": None if self._first_lead_us is None else round(self._first_lead_us / 1000.0, 3),
            "first_buffer_margin_ms": None,
            "overdue_dropped_ms": overdue,
            "first_on_schedule_error_ms": None,
        }

    def audio_clock_ns(self) -> int | None:
        """The sink's own audio clock (internal time), when the sink provides one."""
        sink = self._sink
        if sink is None or not hasattr(sink, "provide_clock"):
            return None
        clock = sink.provide_clock()
        if clock is None:
            return None
        return int(clock.get_internal_time())

    # -- feeder -----------------------------------------------------------------------------

    def _run(self, stop_evt: threading.Event) -> None:
        while not stop_evt.wait(FEEDER_TICK_S):
            try:
                self._tick(float(self._raw_now_us()))
            except Exception as exc:
                engine = self._engine
                if not self._feeder_failed:
                    self._feeder_failed = True
                    logger.exception("playout feeder failed")
                    if engine is not None:
                        engine.fall_back(f"feeder error: {exc}")
                        self._publish_snapshot(engine)

    def _tick(self, now_us: float) -> None:
        """One feeder tick: apply commands, keep the lead, sense every 100 ms, publish."""
        out, engine, port = self._out, self._engine, self._port
        if out is None or engine is None or port is None:
            return
        with self._cmd_lock:
            # Applied under the lock, so resync()/is_drained() never see a command that is
            # neither pending nor reflected in ``_queued``.
            commands = list(self._commands)
            self._commands.clear()
            for cmd in commands:
                self._apply(out, engine, cmd)
            if commands:
                with self._pub_lock:
                    self._queued = engine.queued
        if self._playing_at_us is None:
            self._playing_at_us = now_us
        self._poll_bus(out, engine)
        if self._input_format_set:
            self._feed(out, engine, now_us)
        sensed = False
        if self._next_sense_us is None or now_us >= self._next_sense_us:
            self._next_sense_us = now_us + SENSE_INTERVAL_US
            self._sense(out, engine, port, now_us)
            sensed = True
        with self._pub_lock:
            self._queued = engine.queued
            self._counters.queued_chunks = engine.queued_chunks
        if sensed:
            self._end_tail_mute(engine)
            self._publish_snapshot(engine)

    def _end_tail_mute(self, engine: PlayoutEngine) -> None:
        """Unmute once the port has played past the cleared audio (plus the margin)."""
        until = self._tail_until_ns
        x_port = engine.port_x_ns
        if until is None or x_port is None or x_port < until:
            return
        self._tail_until_ns = None
        self._apply_output_volume()

    def _apply_output_volume(self) -> None:
        """The user's volume and mute, with the tail mute on top (it never unmutes a user mute)."""
        tail = self._tail_until_ns is not None
        user = self._feeder_volume
        if user is None:
            _apply_mute_to(self._sink, tail)
        else:
            _apply_volume_to(self._sink, (user[0], user[1] or tail))
        tap = self._air_tap
        if tap is not None:
            muted = tail or (user is not None and (user[1] or user[0] <= 0.0))
            if muted != self._air_muted:
                self._air_muted = muted
                try:
                    tap.mute_transition(float(self._raw_now_us()), muted)
                except Exception:
                    self._air_tap_failed(tap)

    def _apply(self, out: PlayoutOutput, engine: PlayoutEngine, cmd: tuple[Any, ...]) -> None:
        kind = cmd[0]
        if kind == "submit":
            engine.submit(cmd[1], cmd[2])
        elif kind == "clear":
            self._tail_until_ns = engine.clear() + TAIL_MUTE_MARGIN_NS
            self._apply_output_volume()
        elif kind == "resync":
            engine.resync(cmd[1])
            with self._pub_lock:
                self._counters.resyncs += 1
        elif kind == "format":
            from sendspin_bridge.services.audio.player.pipeline import pcm_caps

            fmt: PcmFormat = cmd[1]
            out.appsrc.set_property(
                "caps", pcm_caps(sample_rate=fmt.sample_rate, channels=fmt.channels, bit_depth=fmt.bit_depth)
            )
            self._input_format = InputFormat(
                sample_rate=fmt.sample_rate, channels=fmt.channels, sample_bytes=fmt.bit_depth // 8
            )
            engine.set_format(self._input_format)
            self._input_format_set = True
        elif kind == "volume":
            self._feeder_volume = (cmd[1], cmd[2])
            self._apply_output_volume()

    def _feed(self, out: PlayoutOutput, engine: PlayoutEngine, now_us: float) -> None:
        """Write blocks until appsrc holds ``LEAD_MS`` (bounded per tick and by the elapsed time)."""
        assert self._playing_at_us is not None
        limit_ns = (
            (now_us - self._playing_at_us) * WRITE_AHEAD_RATE + out.buffer_time_us + WRITE_AHEAD_SLACK_US
        ) * 1000.0 + _LEAD_NS
        level_ns = _level_time_ns(out.appsrc)
        for _ in range(MAX_BLOCKS_PER_TICK):
            if level_ns >= _LEAD_NS or self._x_w_ns >= limit_ns:
                break
            block = engine.next_block(now_us)
            if block.duration_ns <= 0:
                break
            self._push(out, block)
            level_ns += block.duration_ns

    def _push(self, out: PlayoutOutput, block: Block) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        buf = Gst.Buffer.new_wrapped(block.pcm)  # already bytes (scheduler joins the parts)
        buf.pts = block.x0_ns
        buf.duration = block.duration_ns
        result = out.appsrc.emit("push-buffer", buf)
        if result == Gst.FlowReturn.FLUSHING:
            return  # the output is closing
        if result != Gst.FlowReturn.OK:
            raise RuntimeError(f"appsrc rejected buffer: {result}")
        self._x_w_ns = block.x0_ns + block.duration_ns
        with self._pub_lock:
            self._counters.rendered += 1
            self._counters.overdue_dropped_us += block.late_drop_us
        tap = self._air_tap
        if tap is not None:
            self._tap_block(tap, block)

    def _tap_block(self, tap: WrittenEnvelope, block: Block) -> None:
        """Hand a pushed block to the air tap; nothing it does reaches the feeder."""
        engine, fmt = self._engine, self._input_format
        if engine is None or fmt is None or tap.error is not None:
            return
        try:
            d_out_us = self._output_delay_us() if self._output_delay_us is not None else 0.0
            tap.add_block(block, fmt, engine.schedule_at, engine.kind_at, d_out_us)
        except Exception:
            self._air_tap_failed(tap)

    @staticmethod
    def _air_tap_failed(tap: WrittenEnvelope) -> None:
        """Disable the tap for this stream (``internal_error``); logged once, the feeder carries on."""
        if tap.error is None:
            logger.exception("playout: air tap failed, disabled for this stream")
        try:
            tap.fail()
        except Exception:
            logger.debug("playout: air tap could not be marked failed", exc_info=True)

    def _sense(self, out: PlayoutOutput, engine: PlayoutEngine, port: _WatchedPort, now_us: float) -> None:
        level_ns = _level_time_ns(out.appsrc)
        delivered_ns = max(0, self._x_w_ns - level_ns)
        engine.sense(now_us, delivered_ns)
        if not port.ever and self._playing_at_us is not None and now_us - self._playing_at_us >= FALLBACK_AFTER_US:
            engine.fall_back(_FALLBACK_NO_CLOCK)
        with self._pub_lock:
            self._counters.buffered_ns = level_ns

    def _publish_snapshot(self, engine: PlayoutEngine) -> None:
        snap = engine.snapshot()
        if self._on_engine_event is not None:
            self._forward_engine_events(snap)
        port = self._port
        sample = port.last if port is not None else None
        k_ns = snap.get("k_ns")
        position_us = None
        if sample is not None and isinstance(k_ns, int):
            position_us = max(0, sample.a_ns - k_ns) // 1000
        with self._pub_lock:
            self._playout = snap
            self._counters.sync_error_ms = snap.get("error_ms")
            self._counters.output_latency_ms = snap.get("host_latency_ms")
            self._counters.position_us = position_us

    def _poll_bus(self, out: PlayoutOutput, engine: PlayoutEngine) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        bus = out.pipeline.get_bus()
        while True:
            msg = bus.timed_pop_filtered(0, Gst.MessageType.QOS | Gst.MessageType.ERROR)
            if msg is None:
                return
            if msg.type == Gst.MessageType.QOS:
                with self._pub_lock:
                    self._counters.discontinuities += 1
            elif msg.type == Gst.MessageType.ERROR:
                error, debug = msg.parse_error()
                detail = f" ({debug})" if debug else ""
                logger.warning("playout pipeline error: %s%s", error, detail)
                engine.fall_back(f"GStreamer error: {error}")
                self._publish_snapshot(engine)

    # -- helpers ----------------------------------------------------------------------------

    def _queued_after_pending(self) -> bool:
        """Program audio queued once the pending commands are applied (call with ``_cmd_lock``)."""
        with self._pub_lock:
            queued = self._queued
        for cmd in self._commands:
            if cmd[0] == "submit":
                queued = True
            elif cmd[0] in ("clear", "format"):  # a format change drops queued audio
                queued = False
        return queued

    def _enqueue(self, cmd: tuple[Any, ...]) -> None:
        with self._cmd_lock:
            self._commands.append(cmd)

    def _enqueue_format(self, audio_format: PcmFormat) -> None:
        self._format = audio_format
        self._enqueue(("format", audio_format))

    def _idle_playout(self) -> dict[str, object]:
        return {"mode": self._settings.mode, "state": "idle", "fallback_reason": None}


def _level_time_ns(appsrc: Any) -> int:
    return int(appsrc.get_property("current-level-time") or 0)


def _src_latency_ns(element: Any) -> int | None:
    """Min latency of a latency query on the element's src pad (answered upstream), or None."""
    from sendspin_bridge.services.audio.player.gst_support import Gst

    pad = element.get_static_pad("src") if element is not None else None
    if pad is None:
        return None
    query = Gst.Query.new_latency()
    if not pad.query(query):
        return None
    _live, min_latency, _max_latency = query.parse_latency()
    return int(min_latency)


def _apply_mute_to(sink: Any, muted: bool) -> None:
    if sink is not None and sink.find_property("mute") is not None:
        sink.set_property("mute", muted)


def _apply_volume_to(sink: Any, volume: tuple[float, bool]) -> None:
    if sink is None:
        return
    value, muted = volume
    if sink.find_property("volume") is not None:
        sink.set_property("volume", value)
    if sink.find_property("mute") is not None:
        sink.set_property("mute", muted)
