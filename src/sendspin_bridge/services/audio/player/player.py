from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.audio.player.splice import (
    fade_frames,
    fade_in,
    frames_needed,
    silence_after,
    splice_region,
)

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
        max_in_place_shift_us: int = 0,
    ) -> None:
        self._sink_factory = sink_factory
        # The pipeline latency, fixed on every new pipeline and taken out of
        # every timestamp, so a buffer sounds at its play time instead of play
        # time + whatever latency the sink negotiated. 0 keeps GStreamer's own.
        self._output_latency_ns = max(0, int(output_latency_ns))
        self._raw_now_us = raw_now_us or _raw_now_us
        # A delay change up to this size is applied on the running output (see
        # ``resync``); a larger one, or any with 0, rebuilds the output.
        self._max_in_place_shift_us = max(0, int(max_in_place_shift_us))
        # An in-place shift waiting for the audio that follows it.
        self._splice: _PendingSplice | None = None
        self._in_place_shifts = 0
        # The last audio handed on (up to one fade): what a fade-out mirrors.
        self._tail = b""
        # Frames of a fade-in already applied, while the audio after inserted
        # silence still has to fade in (None: nothing to fade in).
        self._fade_in_done: int | None = None
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

    def needs_output(self) -> bool:
        """Whether the next ``start`` has to open a new output (the slow part)."""
        return self._pipeline is None or self._stream_closed

    def now_us(self) -> int:
        """Now on the play-time clock."""
        return self._raw_now_us()

    def start(self, audio_format: PcmFormat) -> None:
        if self._pipeline is not None and self._stream_closed:
            self.stop()
        elif self._pipeline is not None and self._format == audio_format:
            return
        elif self._pipeline is not None:
            self._set_caps(audio_format)
            return
        self.adopt_output(self.prepare_output(audio_format), audio_format)

    def prepare_output(self, audio_format: PcmFormat) -> PreparedOutput:
        """Build a playing output without touching the player's state.

        Opening the PulseAudio stream can take a second or more (a Bluetooth
        sink waits for its A2DP transport), so a caller may run this in a
        worker thread and hand the result to ``adopt_output`` on its own.
        """
        from sendspin_bridge.services.audio.player.gst_support import Gst
        from sendspin_bridge.services.audio.player.pipeline import build_pipeline, pcm_caps

        sink = self._sink_factory()
        pipeline, appsrc = build_pipeline(sink=sink)
        # The system clock (CLOCK_MONOTONIC) is what the pipeline picks anyway
        # while the sink provides no clock; forcing it keeps that true when a
        # diagnostic build lets the sink provide its audio clock for reading.
        pipeline.use_clock(self._clock if self._clock is not None else Gst.SystemClock.obtain())
        pipeline.set_start_time(Gst.CLOCK_TIME_NONE)
        pipeline.set_base_time(0)
        if self._output_latency_ns:
            pipeline.set_latency(self._output_latency_ns)
        output = PreparedOutput(pipeline=pipeline, appsrc=appsrc, sink=sink)
        self._attach_sink_probe(output)
        _apply_volume_to(sink, self._volume)
        appsrc.set_property(
            "caps",
            pcm_caps(
                sample_rate=audio_format.sample_rate,
                channels=audio_format.channels,
                bit_depth=audio_format.bit_depth,
            ),
        )
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            # Keep nothing of it: a half-built pipeline would make every later
            # submit fail until the next stream, instead of the next start
            # building a fresh one.
            pipeline.set_state(Gst.State.NULL)
            raise RuntimeError("failed to set GStreamer pipeline to PLAYING")
        if self._clock is None:
            pipeline.get_state(Gst.SECOND)
        return output

    def adopt_output(self, output: PreparedOutput, audio_format: PcmFormat) -> None:
        """Make a prepared output the current one (a new stream start)."""
        self.stop()
        self._pipeline = output.pipeline
        self._appsrc = output.appsrc
        self._sink = output.sink
        self._format = audio_format
        self._generation += 1
        self._first_lead_us = None
        self._first_margin_raw_ns = None
        self._overdue_dropped_us = 0.0
        self._stream_closed = False
        # A volume set while the output was being opened.
        self._apply_volume()

    @staticmethod
    def discard_output(output: PreparedOutput) -> None:
        """Close a prepared output that will not be used."""
        from sendspin_bridge.services.audio.player.gst_support import Gst

        output.pipeline.set_state(Gst.State.NULL)

    def submit(self, play_time_us: int, payload: bytes) -> None:
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
        full_duration_us = n_frames * 1_000_000 // rate if n_frames else 0
        # Kept whole for a resync: a later delay change may make its head due again.
        record = (play_time_us, play_time_us + full_duration_us, payload)
        handed_on = self._splice is not None and self._splice_take(play_time_us, payload, raw_now_us, gst_now_ns)
        if not handed_on:
            handed_on = self._submit_now(play_time_us, payload, raw_now_us, gst_now_ns)
        self._forget_played(raw_now_us)
        if handed_on:
            self._queued.append(record)
        self._poll_bus()

    def _submit_now(self, play_time_us: int, payload: bytes, raw_now_us: int, gst_now_ns: int) -> bool:
        """Hand a chunk on at its own play time; False when all of it was overdue."""
        assert self._format is not None
        frame = self._format.frame_bytes()
        rate = self._format.sample_rate
        n_frames = len(payload) // frame if frame else 0
        # A compensated output plays a buffer at its play time, so audio whose
        # play time has passed could only sound late (or be clipped by the
        # sink): it is not handed on. Whole chunks are dropped and a chunk that
        # straddles "now" loses its head up to the next frame (a late start or
        # an underrun). An uncompensated output plays everything a pipeline
        # latency later, on its own schedule, so it keeps it all.
        skip = 0
        if self._output_latency_ns and n_frames and play_time_us < raw_now_us:
            skip = min(n_frames, -(-(raw_now_us - play_time_us) * rate // 1_000_000))
            self._overdue_dropped_us += skip * 1_000_000 / rate
            if skip == n_frames:
                return False
            payload = payload[skip * frame :]
        skip_ns = skip * 1_000_000_000 // rate
        pts_ns = max(0, gst_pts_ns(play_time_us, raw_now_us, gst_now_ns) + skip_ns - self._output_latency_ns)
        if self._fade_in_done is not None:
            fade = fade_frames(rate)
            payload = fade_in(payload, done=self._fade_in_done, fade=fade, fmt=self._format)
            self._fade_in_done += len(payload) // frame
            if self._fade_in_done >= fade:
                self._fade_in_done = None
        self._push(pts_ns, payload)
        return True

    def _push(self, pts_ns: int, payload: bytes) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        assert self._format is not None and self._appsrc is not None
        frame = self._format.frame_bytes()
        n_frames = len(payload) // frame if frame else 0
        duration_ns = int(n_frames * 1_000_000_000 / self._format.sample_rate) if n_frames else 0
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
        keep = fade_frames(self._format.sample_rate) * frame
        self._tail = (self._tail + payload)[-keep:] if len(payload) < keep else payload[-keep:]

    def resync(self, shift_us: int) -> InPlaceShift | bool:
        """Move all audio not yet played by ``shift_us`` on the play timeline.

        A changed output delay shifts every later play time, but buffers already
        in appsrc and the sink's ring buffer keep their old timestamps, and
        audiobasesink absorbs a step under its alignment threshold (40 ms) as
        jitter and a larger one only after discont-wait, as a jump.

        Up to ``max_in_place_shift_us`` the shift is applied in place: the
        output, and so its PulseAudio stream, is kept (every new stream makes a
        Bluetooth speaker re-buffer with a new offset). What is already handed
        on plays as it was; the audio after it continues the timeline
        contiguously, with ``shift_us`` of silence inserted (later) or skipped
        (earlier), frame-exact and faded over 5 ms. No flush event is sent
        (#464). Everything after the junction then plays when a rebuild would
        have played it. Returns the ``InPlaceShift``.

        A larger shift rebuilds the output, which drops everything queued
        downstream (teardown is the path every stream start takes), and the
        kept audio is handed back with the shifted play times. Audio whose new
        time has already passed is dropped: the cost is a short silence, never
        misaligned audio. Returns True (False when nothing is playing). If the
        rebuild fails, ``start`` keeps nothing of it, so the next chunk's
        ``start`` builds a fresh output.
        """
        if self._pipeline is None or self._format is None:
            return False
        if shift_us and abs(shift_us) <= self._max_in_place_shift_us:
            return self._shift_in_place(shift_us)
        self._forget_shift()
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

    def _shift_in_place(self, shift_us: int) -> InPlaceShift:
        assert self._format is not None
        # The kept record follows the shift, so a later rebuild re-times it
        # from where it now belongs.
        self._queued = deque(((s + shift_us, e + shift_us, p) for s, e, p in self._queued), maxlen=_MAX_QUEUED_CHUNKS)
        self._in_place_shifts += 1
        rate = self._format.sample_rate
        splice = self._splice
        if splice is not None:
            if splice.shift_frames is not None:
                # Still waiting for audio after an earlier shift: what it holds
                # moves too. (Unmeasured, the first chunk will tell it all.)
                splice.shift_frames += round(shift_us * rate / 1_000_000)
                splice.next_play_us += shift_us
            return InPlaceShift(shift_us=shift_us)
        if self._last_pts_ns is None or self._last_duration_ns is None:
            return InPlaceShift(shift_us=shift_us)  # nothing handed on: nothing to continue
        junction_ns = self._last_pts_ns + self._last_duration_ns
        clock = self._pipeline.get_clock() if self._pipeline is not None else None
        gst_now_ns = int(clock.get_time()) if clock is not None else 0
        if junction_ns + self._output_latency_ns <= gst_now_ns + _SPLICE_GUARD_NS:
            return InPlaceShift(shift_us=shift_us)  # ran dry: the next chunk plays at its own time
        fade = fade_frames(rate)
        insert_frames = round(shift_us * rate / 1_000_000)
        if insert_frames >= fade:
            # Later: the server sends the next audio that much later too, so
            # the silence is handed on now and the output never runs dry.
            silent = self._fade_in_done is not None
            tail = b"" if silent else self._tail
            self._push_pieces(junction_ns, silence_after(tail, frames=insert_frames, fade=fade, fmt=self._format))
            self._fade_in_done = 0
        else:
            # Earlier (or later by less than a fade): it needs the audio that follows.
            self._splice = _PendingSplice(junction_ns=junction_ns, after_silence=self._fade_in_done is not None)
        return InPlaceShift(shift_us=shift_us)

    def _push_pieces(self, pts_ns: int, audio: bytes) -> int:
        """Hand on contiguous audio in pieces no longer than a usual chunk (so a
        long silence reaches the sink like any other audio); returns its frames."""
        assert self._format is not None
        rate = self._format.sample_rate
        frame = self._format.frame_bytes()
        piece = max(1, rate * _SPLICE_PIECE_MS // 1000) * frame
        frames = 0
        for offset in range(0, len(audio), piece):
            part = audio[offset : offset + piece]
            self._push(pts_ns + frames * 1_000_000_000 // rate, part)
            frames += len(part) // frame
        return frames

    def _splice_take(self, play_time_us: int, payload: bytes, raw_now_us: int, gst_now_ns: int) -> bool:
        """Feed a chunk to the pending splice; False when it plays as usual."""
        splice = self._splice
        assert splice is not None and self._format is not None
        rate = self._format.sample_rate
        frame = self._format.frame_bytes()
        if splice.shift_frames is None:
            # The first chunk after the shift: how far its play time sits from
            # the junction is the shift, as the output must apply it.
            if splice.junction_ns + self._output_latency_ns <= gst_now_ns + _SPLICE_GUARD_NS:
                self._splice = None  # the output ran dry: nothing to continue from
                return False
            pts_ns = gst_pts_ns(play_time_us, raw_now_us, gst_now_ns) - self._output_latency_ns
            shift_frames = round((pts_ns - splice.junction_ns) * rate / 1_000_000_000)
            if shift_frames == 0:
                self._splice = None
                return False
            splice.shift_frames = shift_frames
            splice.first_play_us = play_time_us
            splice.next_play_us = play_time_us
        elif abs(play_time_us - splice.next_play_us) > _SPLICE_CONTIGUITY_US:
            # Not the audio that follows: give up the splice, play everything as is.
            self._abandon_splice(raw_now_us, gst_now_ns)
            return False
        splice.audio += payload
        splice.next_play_us = play_time_us + (len(payload) // frame) * 1_000_000 // rate
        fade = fade_frames(rate)
        needed = frames_needed(splice.shift_frames, fade)
        if len(splice.audio) // frame < needed:
            return True
        if splice.junction_ns + self._output_latency_ns <= gst_now_ns + _SPLICE_GUARD_NS:
            self._abandon_splice(raw_now_us, gst_now_ns)
            return True
        self._splice = None
        audio = bytes(splice.audio)
        region, used = splice_region(
            audio, shift_frames=splice.shift_frames, fade=fade, fmt=self._format, fade_out=not splice.after_silence
        )
        self._fade_in_done = None  # the region fades the shifted audio in
        region_frames = self._push_pieces(splice.junction_ns, region)
        rest = audio[used * frame :]
        if rest:
            self._push(splice.junction_ns + region_frames * 1_000_000_000 // rate, rest)
        return True

    def _abandon_splice(self, raw_now_us: int, gst_now_ns: int) -> None:
        splice, self._splice = self._splice, None
        if splice is not None and splice.audio and splice.first_play_us is not None:
            self._submit_now(splice.first_play_us, bytes(splice.audio), raw_now_us, gst_now_ns)

    def _forget_shift(self) -> None:
        self._splice = None
        self._fade_in_done = None
        self._tail = b""

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
        self._forget_shift()
        self._last_pts_ns = None
        self._last_duration_ns = None
        self._sync_error_ns = None
        self._poll_bus()

    def close_stream(self) -> None:
        if self._appsrc is None:
            return
        if self._splice is not None and self._pipeline is not None:
            clock = self._pipeline.get_clock()
            self._abandon_splice(self._raw_now_us(), int(clock.get_time()) if clock is not None else 0)
        self._appsrc.emit("end-of-stream")
        self._stream_closed = True
        self._poll_bus()

    def set_volume(self, value: float, muted: bool) -> None:
        """The stream's own volume (linear) and mute, kept for later sinks too."""
        self._volume = (max(0.0, min(1.0, float(value))), bool(muted))
        self._apply_volume()

    def _apply_volume(self) -> None:
        _apply_volume_to(self._sink, self._volume)

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
        self._forget_shift()

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
            "in_place_shift_count": self._in_place_shifts,
            "queued_chunks": len(self._queued),
            "overdue_dropped_ms": round(self._overdue_dropped_us / 1000.0, 3),
        }

    def _set_caps(self, audio_format: PcmFormat) -> None:
        from sendspin_bridge.services.audio.player.pipeline import pcm_caps

        assert self._appsrc is not None
        # Queued audio of another format can't be handed back after a resync.
        self._forget_queued()
        self._forget_shift()
        self._appsrc.set_property(
            "caps",
            pcm_caps(
                sample_rate=audio_format.sample_rate,
                channels=audio_format.channels,
                bit_depth=audio_format.bit_depth,
            ),
        )
        self._format = audio_format

    def _attach_sink_probe(self, output: PreparedOutput) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        pad = output.sink.get_static_pad("sink")
        if pad is None:
            return
        pipeline = output.pipeline

        def _probe(_pad, info):
            buf = info.get_buffer()
            if buf is None:
                return Gst.PadProbeReturn.OK
            self._rendered += 1
            clock = pipeline.get_clock()
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
        if self._first_margin_raw_ns is not None and configured_ns is not None:
            margin_ms = _ns_to_ms(self._first_margin_raw_ns + configured_ns)
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

# How soon the junction of an in-place shift may come for the region to reach
# the output in time; nearer than that, it has (nearly) run dry.
_SPLICE_GUARD_NS = 5_000_000

# The longest buffer the splice region is handed on in.
_SPLICE_PIECE_MS = 20

# Audio after an in-place shift that does not follow the previous chunk (within
# this) is not the continuation the splice was waiting for.
_SPLICE_CONTIGUITY_US = 2_000

# Bounds the resync record if the clock stalls: far beyond any send-ahead
# (over 5 min of 20 ms chunks); played chunks are dropped by time anyway.
_MAX_QUEUED_CHUNKS = 16_384


@dataclass(frozen=True, slots=True)
class InPlaceShift:
    """A delay change applied on the running output (``StreamPlayer.resync``).

    ``shift_us`` > 0 plays later (that much silence inserted), < 0 earlier
    (that much audio skipped).
    """

    shift_us: int


@dataclass(slots=True)
class _PendingSplice:
    """An in-place shift waiting for the audio that follows the junction."""

    # Where the audio already handed on ends (pts): the splice starts there.
    junction_ns: int
    # The output is silent at the junction (inserted silence): nothing to fade out.
    after_silence: bool = False
    # Measured on the first chunk after the shift (None until then).
    shift_frames: int | None = None
    first_play_us: int | None = None
    next_play_us: int = 0
    audio: bytearray = field(default_factory=bytearray)


@dataclass(frozen=True, slots=True)
class PreparedOutput:
    """A built, playing output not yet adopted by a player."""

    pipeline: Any
    appsrc: Any
    sink: Any


def _apply_volume_to(sink: Any, volume: tuple[float, bool] | None) -> None:
    if sink is None or volume is None:
        return
    value, muted = volume
    if sink.find_property("volume") is not None:
        sink.set_property("volume", value)
    if sink.find_property("mute") is not None:
        sink.set_property("mute", muted)


def _raw_now_us() -> int:
    clock_id = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)
    return int(time.clock_gettime(clock_id) * 1_000_000)


def _ns_to_ms(value_ns: int | None) -> float | None:
    if value_ns is None:
        return None
    return round(value_ns / 1_000_000.0, 3)
