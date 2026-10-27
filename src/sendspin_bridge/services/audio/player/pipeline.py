from __future__ import annotations

import itertools
from collections.abc import Callable

from sendspin_bridge.services.audio.player.gst_support import Gst

ElementFactory = Callable[[], Gst.Element]


def default_pulsesink_factory(
    *,
    device: str | None,
    client_name: str,
    buffer_time_us: int,
    slave_method: str,
    provide_clock: bool = False,
) -> Callable[[], Gst.Element]:
    def _make() -> Gst.Element:
        sink = Gst.ElementFactory.make("pulsesink", "sink")
        if sink is None:
            raise RuntimeError("GStreamer element pulsesink is not available")
        if device:
            sink.set_property("device", device)
        sink.set_property("client-name", client_name)
        sink.set_property("sync", True)
        # Providing the clock only lets it be read (diagnostics): the player
        # keeps the pipeline on the system clock either way.
        sink.set_property("provide-clock", bool(provide_clock))
        _set_if_present(sink, "slave-method", _slave_method_value(slave_method))
        _set_if_present(sink, "buffer-time", buffer_time_us)
        return sink

    return _make


def build_pipeline(*, sink: Gst.Element) -> tuple[Gst.Pipeline, Gst.Element]:
    pipeline = Gst.Pipeline.new("stream-player")
    appsrc = Gst.ElementFactory.make("appsrc", "src")
    convert = Gst.ElementFactory.make("audioconvert", "convert")
    resample = Gst.ElementFactory.make("audioresample", "resample")
    if None in (appsrc, convert, resample):
        raise RuntimeError("GStreamer base audio elements are not available")

    appsrc.set_property("format", Gst.Format.TIME)
    appsrc.set_property("is-live", True)
    appsrc.set_property("do-timestamp", False)

    pipeline.add(appsrc)
    pipeline.add(convert)
    pipeline.add(resample)
    pipeline.add(sink)
    if not appsrc.link(convert):
        raise RuntimeError("failed to link appsrc to audioconvert")
    if not convert.link(resample):
        raise RuntimeError("failed to link audioconvert to audioresample")
    if not resample.link(sink):
        raise RuntimeError("failed to link audioresample to sink")
    return pipeline, appsrc


def playout_pulsesink_factory(
    *, device: str | None, client_name: str, buffer_time_us: int
) -> Callable[[], Gst.Element]:
    """pulsesink with sync=false, provide-clock=true, slave-method=none.

    The playout engine only uses GStreamer to carry audio: the sink writes what it gets as
    it gets it, and its audio clock is read as a sensor (the pipeline keeps the system clock).
    """

    def _make() -> Gst.Element:
        sink = Gst.ElementFactory.make("pulsesink", "sink")
        if sink is None:
            raise RuntimeError("GStreamer element pulsesink is not available")
        if device:
            sink.set_property("device", device)
        sink.set_property("client-name", client_name)
        sink.set_property("sync", False)
        sink.set_property("provide-clock", True)
        _set_if_present(sink, "slave-method", _slave_method_value("none"))
        _set_if_present(sink, "buffer-time", buffer_time_us)
        return sink

    return _make


def build_playout_pipeline(*, sink: Gst.Element, out_rate: int) -> tuple[Gst.Pipeline, Gst.Element]:
    """appsrc ! audioconvert ! audioresample ! capsfilter(S16LE, 2 ch, out_rate) ! sink; system clock forced.

    The fixed output format keeps the sink (and its PulseAudio stream) when the input
    format changes: only appsrc's caps change, convert and resample absorb it.
    """
    pipeline = Gst.Pipeline.new("playout-player")
    appsrc = Gst.ElementFactory.make("appsrc", "src")
    convert = Gst.ElementFactory.make("audioconvert", "convert")
    resample = Gst.ElementFactory.make("audioresample", "resample")
    capsfilter = Gst.ElementFactory.make("capsfilter", "outcaps")
    if None in (appsrc, convert, resample, capsfilter):
        raise RuntimeError("GStreamer base audio elements are not available")

    appsrc.set_property("format", Gst.Format.TIME)
    appsrc.set_property("is-live", True)
    appsrc.set_property("do-timestamp", False)
    capsfilter.set_property(
        "caps",
        Gst.Caps.from_string(f"audio/x-raw,format=S16LE,layout=interleaved,channels=2,rate={int(out_rate)}"),
    )

    elements = (appsrc, convert, resample, capsfilter, sink)
    for element in elements:
        pipeline.add(element)
    for upstream, downstream in itertools.pairwise(elements):
        if not upstream.link(downstream):
            raise RuntimeError(f"failed to link {upstream.get_name()} to {downstream.get_name()}")
    # sync=false makes the clock irrelevant for rendering; forcing the system clock keeps
    # the sink's provided audio clock a pure sensor.
    pipeline.use_clock(Gst.SystemClock.obtain())
    return pipeline, appsrc


def pcm_caps(*, sample_rate: int, channels: int, bit_depth: int) -> Gst.Caps:
    gst_format = {16: "S16LE", 24: "S24LE", 32: "S32LE"}.get(bit_depth)
    if gst_format is None:
        raise ValueError(f"unsupported PCM bit depth: {bit_depth}")
    return Gst.Caps.from_string(
        f"audio/x-raw,format={gst_format},layout=interleaved,rate={sample_rate},channels={channels}"
    )


def _set_if_present(element: Gst.Element, name: str, value: object) -> None:
    if element.find_property(name) is not None:
        element.set_property(name, value)


def _slave_method_value(name: str) -> int:
    mapping = {
        "resample": 0,
        "skew": 1,
        "none": 2,
    }
    try:
        return mapping[name]
    except KeyError:
        raise ValueError(f"unknown slave-method: {name}") from None
