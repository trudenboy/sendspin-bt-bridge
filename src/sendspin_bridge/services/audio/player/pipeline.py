from __future__ import annotations

from collections.abc import Callable

from sendspin_bridge.services.audio.player.gst_support import Gst

ElementFactory = Callable[[], Gst.Element]


def default_pulsesink_factory(
    *,
    device: str | None,
    client_name: str,
    buffer_time_us: int,
    slave_method: str,
) -> Callable[[], Gst.Element]:
    def _make() -> Gst.Element:
        sink = Gst.ElementFactory.make("pulsesink", "sink")
        if sink is None:
            raise RuntimeError("GStreamer element pulsesink is not available")
        if device:
            sink.set_property("device", device)
        sink.set_property("client-name", client_name)
        sink.set_property("sync", True)
        sink.set_property("provide-clock", False)
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
