from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def gst_pts_ns(play_time_us: int, raw_now_us: int, gst_now_ns: int) -> int:
    return gst_now_ns + (play_time_us - raw_now_us) * 1000


@dataclass(frozen=True, slots=True)
class PcmFormat:
    sample_rate: int
    channels: int
    bit_depth: int

    def frame_bytes(self) -> int:
        return self.channels * (self.bit_depth // 8)


class StreamPlayer:
    def __init__(
        self,
        *,
        sink_factory: Callable[[], object],
        raw_now_us: Callable[[], int] | None = None,
        clock: object | None = None,
        output_latency_ns: int = 0,
    ) -> None:
        self._sink_factory = sink_factory
        # The pipeline latency, fixed on every new pipeline and taken out of
        # every timestamp, so a buffer sounds at its play time instead of play
        # time + whatever latency the sink negotiated. 0 keeps GStreamer's own.
        self._output_latency_ns = max(0, int(output_latency_ns))
        self._raw_now_us = raw_now_us or _raw_now_us
        self._clock = clock
        self._pipeline: Any = None
        self._appsrc: Any = None
        self._sink: Any = None
        self._format: PcmFormat | None = None
        self._discontinuities = 0
        self._last_pts_ns: int | None = None
        self._last_duration_ns: int | None = None
        self._sync_error_ns: int | None = None
        self._rendered = 0
        self._stream_closed = False
        # Stream volume/mute once set; re-applied to every new sink element.
        self._volume: tuple[float, bool] | None = None
        # Audio handed to GStreamer that may not have played yet, as
        # (play_time_us, end_us, payload): ``resync`` re-times it after a delay change.
        self._queued: deque[tuple[int, int, bytes]] = deque(maxlen=_MAX_QUEUED_CHUNKS)
        self._resyncs = 0
        # Per-output start facts (see ``start_report``); a new output starts over.
        self._generation = 0
        self._first_lead_us: int | None = None
        self._first_margin_raw_ns: int | None = None
        self._overdue_dropped_us = 0.0

    @property
    def stream_generation(self) -> int:
        """How many outputs (pipelines) this player has built so far."""
        return self._generation

    def start(self, audio_format: PcmFormat) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst
        from sendspin_bridge.services.audio.player.pipeline import build_pipeline

        if self._pipeline is not None and self._stream_closed:
            self.stop()
        elif self._pipeline is not None and self._format == audio_format:
            return
        elif self._pipeline is not None:
            self._set_caps(audio_format)
            return
        sink = self._sink_factory()
        pipeline, appsrc = build_pipeline(sink=sink)
        self._pipeline = pipeline
        self._appsrc = appsrc
        self._sink = sink
        # The system clock (CLOCK_MONOTONIC) is what the pipeline picks anyway
        # while the sink provides no clock; forcing it keeps that true when a
        # diagnostic build lets the sink provide its audio clock for reading.
        pipeline.use_clock(self._clock if self._clock is not None else Gst.SystemClock.obtain())
        pipeline.set_start_time(Gst.CLOCK_TIME_NONE)
        pipeline.set_base_time(0)
        if self._output_latency_ns:
            pipeline.set_latency(self._output_latency_ns)
        self._generation += 1
        self._first_lead_us = None
        self._first_margin_raw_ns = None
        self._overdue_dropped_us = 0.0
        self._attach_sink_probe()
        self._apply_volume()
        self._set_caps(audio_format)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            # Keep nothing of it: a half-built pipeline would make every later
            # submit fail until the next stream, instead of the next start
            # building a fresh one.
            self.stop()
            raise RuntimeError("failed to set GStreamer pipeline to PLAYING")
        if self._clock is None:
            pipeline.get_state(Gst.SECOND)
        self._stream_closed = False

    def submit(self, play_time_us: int, payload: bytes) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._appsrc is None or self._pipeline is None or self._format is None:
            raise RuntimeError("player is not started")
        clock = self._pipeline.get_clock()
        gst_now_ns = int(clock.get_time()) if clock is not None else 0
        raw_now_us = self._raw_now_us()
        if self._first_lead_us is None:
            self._first_lead_us = play_time_us - raw_now_us
        frame = self._format.frame_bytes()
        rate = self._format.sample_rate
        n_frames = len(payload) // frame if frame else 0
        # Audio whose play time has passed can only sound late, so it never
        # reaches the output: whole chunks are dropped and a chunk that straddles
        # "now" loses its head up to the next frame, so the first sample handed
        # on is exactly the one due next (a late start or an underrun).
        skip = 0
        if n_frames and play_time_us < raw_now_us:
            skip = min(n_frames, -(-(raw_now_us - play_time_us) * rate // 1_000_000))
            self._overdue_dropped_us += skip * 1_000_000 / rate
            if skip == n_frames:
                self._forget_played(raw_now_us)
                self._poll_bus()
                return
            payload = payload[skip * frame :]
            n_frames -= skip
        skip_ns = skip * 1_000_000_000 // rate
        pts_ns = max(0, gst_pts_ns(play_time_us, raw_now_us, gst_now_ns) + skip_ns - self._output_latency_ns)
        play_time_us += skip_ns // 1000
        duration_ns = int(n_frames * 1_000_000_000 / rate) if n_frames else 0
        buf = Gst.Buffer.new_wrapped(payload)
        buf.pts = pts_ns
        buf.duration = duration_ns
        if self._is_discontinuity(pts_ns):
            buf.set_flags(Gst.BufferFlags.DISCONT)
            self._discontinuities += 1
        result = self._appsrc.emit("push-buffer", buf)
        if result != Gst.FlowReturn.OK:
            raise RuntimeError(f"appsrc rejected buffer: {result}")
        self._last_pts_ns = pts_ns
        self._last_duration_ns = duration_ns
        self._forget_played(raw_now_us)
        self._queued.append((play_time_us, play_time_us + duration_ns // 1000, payload))
        self._poll_bus()

    def resync(self, shift_us: int) -> bool:
        """Move all audio not yet played by ``shift_us`` on the play timeline, now.

        A changed output delay shifts every later play time, but buffers already
        in appsrc and the sink's ring buffer keep their old timestamps, and
        audiobasesink absorbs a step under its alignment threshold (40 ms) as
        jitter and a larger one only after discont-wait, as a jump. So the
        output is rebuilt, which drops everything queued downstream (flush
        events deadlocked some A2DP sinks, #464; teardown is the path every
        stream start takes), and the kept audio is handed back with the shifted
        play times. Audio whose new time has already passed is dropped: the
        cost is a short silence, never misaligned audio.

        Returns whether the output was rebuilt (False when nothing is playing).
        If the rebuild fails, ``start`` keeps nothing of it, so the next
        chunk's ``start`` builds a fresh output.
        """
        if self._pipeline is None or self._format is None:
            return False
        audio_format = self._format
        now_us = self._raw_now_us()
        keep = [(start_us + shift_us, payload) for start_us, _end_us, payload in self._queued]
        # A compensated output plays at the play time itself, so audio due
        # before the rebuilt output can take it would only be clipped.
        guard_us = _RESYNC_GUARD_US if self._output_latency_ns else 0
        keep = [(start_us, payload) for start_us, payload in keep if start_us >= now_us + guard_us]
        self.stop()
        self.start(audio_format)
        for play_time_us, payload in keep:
            self.submit(play_time_us, payload)
        self._resyncs += 1
        return True

    def _forget_played(self, raw_now_us: int) -> None:
        while self._queued and self._queued[0][1] <= raw_now_us:
            self._queued.popleft()

    def _forget_queued(self) -> None:
        self._queued.clear()

    def clear(self) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._pipeline is None:
            return
        self._pipeline.send_event(Gst.Event.new_flush_start())
        self._pipeline.send_event(Gst.Event.new_flush_stop(True))
        self._forget_queued()
        self._last_pts_ns = None
        self._last_duration_ns = None
        self._sync_error_ns = None
        self._poll_bus()

    def close_stream(self) -> None:
        if self._appsrc is None:
            return
        self._appsrc.emit("end-of-stream")
        self._stream_closed = True
        self._poll_bus()

    def set_volume(self, value: float, muted: bool) -> None:
        """The stream's own volume (linear) and mute, kept for later sinks too."""
        self._volume = (max(0.0, min(1.0, float(value))), bool(muted))
        self._apply_volume()

    def _apply_volume(self) -> None:
        sink = self._sink
        if sink is None or self._volume is None:
            return
        value, muted = self._volume
        if sink.find_property("volume") is not None:
            sink.set_property("volume", value)
        if sink.find_property("mute") is not None:
            sink.set_property("mute", muted)

    def stop(self) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._pipeline is None:
            return
        self._pipeline.set_state(Gst.State.NULL)
        self._pipeline = None
        self._appsrc = None
        self._sink = None
        self._format = None
        self._last_pts_ns = None
        self._last_duration_ns = None
        self._stream_closed = False
        self._forget_queued()

    def is_drained(self) -> bool:
        if self._appsrc is None:
            return True
        level = self._appsrc.get_property("current-level-bytes")
        return int(level or 0) == 0

    def metrics(self) -> dict[str, object]:
        buffered_ns = 0
        position_ns = None
        latency_ns = None
        if self._appsrc is not None:
            buffered_ns = int(self._appsrc.get_property("current-level-time") or 0)
        if self._pipeline is not None:
            ok, pos = self._pipeline.query_position(self._gst_format_time())
            if ok:
                position_ns = int(pos)
            query = self._latency_query()
            if query is not None:
                latency_ns = query
        self._poll_bus()
        return {
            "backend_output_latency_ms": _ns_to_ms(latency_ns),
            "buffered_audio_ms": _ns_to_ms(buffered_ns),
            "playback_position_us": None if position_ns is None else position_ns // 1000,
            "playback_sync_error_ms": _ns_to_ms(self._sync_error_ns),
            "discontinuity_count": self._discontinuities,
            "rendered_buffers": self._rendered,
            "resync_count": self._resyncs,
            "queued_chunks": len(self._queued),
        }

    def _set_caps(self, audio_format: PcmFormat) -> None:
        from sendspin_bridge.services.audio.player.pipeline import pcm_caps

        assert self._appsrc is not None
        # Queued audio of another format can't be handed back after a resync.
        self._forget_queued()
        self._appsrc.set_property(
            "caps",
            pcm_caps(
                sample_rate=audio_format.sample_rate,
                channels=audio_format.channels,
                bit_depth=audio_format.bit_depth,
            ),
        )
        self._format = audio_format

    def _attach_sink_probe(self) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._sink is None:
            return
        pad = self._sink.get_static_pad("sink")
        if pad is None:
            return

        def _probe(_pad, info):
            buf = info.get_buffer()
            if buf is None:
                return Gst.PadProbeReturn.OK
            self._rendered += 1
            clock = self._pipeline.get_clock() if self._pipeline is not None else None
            if clock is not None and buf.pts != Gst.CLOCK_TIME_NONE:
                self._sync_error_ns = int(clock.get_time()) - int(buf.pts)
                if self._first_margin_raw_ns is None:
                    self._first_margin_raw_ns = -self._sync_error_ns
            return Gst.PadProbeReturn.OK

        pad.add_probe(Gst.PadProbeType.BUFFER, _probe)

    def _is_discontinuity(self, pts_ns: int) -> bool:
        if self._last_pts_ns is None or self._last_duration_ns is None:
            return False
        expected = self._last_pts_ns + self._last_duration_ns
        gap_ns = abs(pts_ns - expected)
        return gap_ns > 20_000_000

    def _poll_bus(self) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._pipeline is None:
            return
        bus = self._pipeline.get_bus()
        while True:
            msg = bus.timed_pop_filtered(0, Gst.MessageType.QOS | Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg is None:
                break
            if msg.type == Gst.MessageType.QOS:
                self._discontinuities += 1
            elif msg.type == Gst.MessageType.ERROR:
                error, debug = msg.parse_error()
                detail = f" ({debug})" if debug else ""
                raise RuntimeError(f"GStreamer pipeline error: {error}{detail}")

    def start_report(self) -> dict[str, float | None] | None:
        """What the current output started with, in ms (None while nothing plays).

        ``first_lead_ms``: how far ahead of its play time the first chunk came.
        ``first_buffer_margin_ms``: how long before it was due to sound the
        first buffer reached the sink (negative: it was late).
        ``configured_latency_ms``: the latency the pipeline runs with;
        ``min_latency_ms``: the least the sink said it needs.
        ``overdue_dropped_ms``: audio dropped because its play time had passed.
        ``first_on_schedule_error_ms``: how late the first sample handed on
        (the first not dropped) still reached the sink; 0 when it was in time.
        """
        if self._pipeline is None:
            return None
        configured_ns = self._configured_latency_ns()
        min_ns = self._latency_query()
        margin_ms = None
        error_ms = None
        if self._first_margin_raw_ns is not None:
            margin_ms = _ns_to_ms(self._first_margin_raw_ns + (configured_ns or 0))
            error_ms = max(0.0, -(margin_ms or 0.0))
        return {
            "compensated_latency_ms": _ns_to_ms(self._output_latency_ns),
            "configured_latency_ms": _ns_to_ms(configured_ns),
            "min_latency_ms": _ns_to_ms(min_ns),
            "first_lead_ms": None if self._first_lead_us is None else round(self._first_lead_us / 1000.0, 3),
            "first_buffer_margin_ms": margin_ms,
            "overdue_dropped_ms": round(self._overdue_dropped_us / 1000.0, 3),
            "first_on_schedule_error_ms": error_ms,
        }

    def audio_clock_ns(self) -> int | None:
        """The sink's own audio clock (internal time), when the sink provides one.

        pulsesink provides it only with ``provide-clock=true``; the pipeline
        keeps running on the system clock either way, so this is read-only.
        """
        sink = self._sink
        if sink is None or not hasattr(sink, "provide_clock"):
            return None
        clock = sink.provide_clock()
        if clock is None:
            return None
        return int(clock.get_internal_time())

    def _configured_latency_ns(self) -> int | None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._pipeline is None:
            return None
        if self._output_latency_ns:
            return self._output_latency_ns
        value = self._pipeline.get_latency()
        if value == Gst.CLOCK_TIME_NONE:
            return self._latency_query()
        return int(value)

    def _gst_format_time(self):
        from sendspin_bridge.services.audio.player.gst_support import Gst

        return Gst.Format.TIME

    def _latency_query(self) -> int | None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._pipeline is None:
            return None
        query = Gst.Query.new_latency()
        if not self._pipeline.query(query):
            return None
        _live, min_latency, _max_latency = query.parse_latency()
        return int(min_latency)


# How soon after a resync a rebuilt, compensated output can play: its PulseAudio
# stream reconnects and the sink renders a little ahead (7 ms measured on a
# null sink, 25 ms + one block on bluez5).
_RESYNC_GUARD_US = 50_000

# Bounds the resync record if the clock stalls: far beyond any send-ahead
# (over 5 min of 20 ms chunks); played chunks are dropped by time anyway.
_MAX_QUEUED_CHUNKS = 16_384


def _raw_now_us() -> int:
    clock_id = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)
    return int(time.clock_gettime(clock_id) * 1_000_000)


def _ns_to_ms(value_ns: int | None) -> float | None:
    if value_ns is None:
        return None
    return round(value_ns / 1_000_000.0, 3)
