from __future__ import annotations

from dataclasses import dataclass
from time import CLOCK_MONOTONIC_RAW, clock_gettime
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
    ) -> None:
        self._sink_factory = sink_factory
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
        if self._clock is not None:
            pipeline.use_clock(self._clock)
        pipeline.set_start_time(Gst.CLOCK_TIME_NONE)
        pipeline.set_base_time(0)
        self._attach_sink_probe()
        self._set_caps(audio_format)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
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
        pts_ns = gst_pts_ns(play_time_us, self._raw_now_us(), gst_now_ns)
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
        self._poll_bus()

    def clear(self) -> None:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        if self._pipeline is None:
            return
        self._pipeline.send_event(Gst.Event.new_flush_start())
        self._pipeline.send_event(Gst.Event.new_flush_stop(True))
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
        sink = self._sink
        if sink is None:
            return
        if sink.find_property("volume") is not None:
            sink.set_property("volume", max(0.0, min(1.0, float(value))))
        if sink.find_property("mute") is not None:
            sink.set_property("mute", bool(muted))

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
        }

    def _set_caps(self, audio_format: PcmFormat) -> None:
        from sendspin_bridge.services.audio.player.pipeline import pcm_caps

        assert self._appsrc is not None
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


def _raw_now_us() -> int:
    return int(clock_gettime(CLOCK_MONOTONIC_RAW) * 1_000_000)


def _ns_to_ms(value_ns: int | None) -> float | None:
    if value_ns is None:
        return None
    return round(value_ns / 1_000_000.0, 3)
