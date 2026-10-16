"""The output latency the pipeline adds is fixed and taken out of the timestamps.

GStreamer plays a buffer at its timestamp plus the latency the pipeline
configured. With pulsesink that latency is whatever the sink reports for the
negotiated PulseAudio buffer (110 ms for ``buffer-time`` 200 ms on PulseAudio
17, 410 ms for 800 ms; PipeWire reports other values), so every speaker played
that much behind the schedule. The player now fixes the pipeline latency to a
known value and stamps every buffer earlier by exactly that value.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.audio.player.player import PcmFormat, StreamPlayer

PCM = PcmFormat(sample_rate=48000, channels=2, bit_depth=16)
LATENCY_NS = 200_000_000


def _gst_available() -> bool:
    try:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        return Gst.ElementFactory.make("fakesink", None) is not None
    except Exception:
        return False


requires_gst = pytest.mark.skipif(not _gst_available(), reason="GStreamer is not available")


def _test_clock():
    try:
        import gi

        gi.require_version("GstCheck", "1.0")
        from gi.repository import GstCheck
    except (ValueError, ImportError):
        pytest.skip("GstCheck.TestClock is not introspectable")
    clock = GstCheck.TestClock.new()
    clock.set_time(0)
    return clock


def _recording_fakesink(handed: list[int]):
    from sendspin_bridge.services.audio.player.gst_support import Gst

    def _make():
        sink = Gst.ElementFactory.make("fakesink", "sink")
        assert sink is not None
        sink.set_property("sync", False)
        sink.set_property("async", False)
        sink.set_property("signal-handoffs", True)
        sink.connect("handoff", lambda _s, buf, _pad: handed.append(int(buf.pts)))
        return sink

    return _make


def _silence(frames: int = 480) -> bytes:
    return bytes(frames * PCM.frame_bytes())


def _wait_until(predicate, *, timeout_s: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


@requires_gst
def test_buffers_are_stamped_earlier_by_the_compensated_latency():
    handed: list[int] = []
    player = StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
    )
    player.start(PCM)
    player.submit(1_000_000, _silence())
    _wait_until(lambda: len(handed) >= 1)
    player.stop()

    assert handed[0] == 1_000_000_000 - LATENCY_NS


@requires_gst
def test_uncompensated_player_keeps_the_plain_mapping():
    handed: list[int] = []
    player = StreamPlayer(sink_factory=_recording_fakesink(handed), raw_now_us=lambda: 0, clock=_test_clock())
    player.start(PCM)
    player.submit(1_000_000, _silence())
    _wait_until(lambda: len(handed) >= 1)
    player.stop()

    assert handed[0] == 1_000_000_000


@requires_gst
def test_the_pipeline_latency_is_fixed_to_the_compensated_value_on_every_start():
    player = StreamPlayer(
        sink_factory=_recording_fakesink([]),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
    )
    player.start(PCM)
    first = player._pipeline.get_latency()
    player.stop()
    player.start(PCM)
    second = player._pipeline.get_latency()
    player.stop()

    assert first == second == LATENCY_NS


@requires_gst
def test_resync_keeps_the_compensation():
    handed: list[int] = []
    player = StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
    )
    player.start(PCM)
    player.submit(1_000_000, _silence())
    _wait_until(lambda: len(handed) >= 1)
    player.resync(-10_000)
    _wait_until(lambda: len(handed) >= 2)
    player.stop()

    assert handed[1] == 990_000_000 - LATENCY_NS


@requires_gst
def test_start_report_tells_the_first_chunk_lead_and_the_compensation():
    player = StreamPlayer(
        sink_factory=_recording_fakesink([]),
        raw_now_us=lambda: 1_000_000,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
    )
    assert player.start_report() is None
    player.start(PCM)
    player.submit(1_300_000, _silence())
    player.submit(1_310_000, _silence())
    _wait_until(lambda: (player.start_report() or {}).get("first_buffer_margin_ms") is not None)
    report = player.start_report()
    player.stop()

    assert report["compensated_latency_ms"] == 200.0
    assert report["configured_latency_ms"] == 200.0
    assert report["first_lead_ms"] == 300.0
    # The test clock stands at 0 ns and raw time at 1 s: the first buffer is
    # due at its PTS + the configured latency = 0 + 300 ms.
    assert report["first_buffer_margin_ms"] == pytest.approx(300.0)


@requires_gst
def test_start_report_starts_over_with_every_new_output():
    player = StreamPlayer(sink_factory=_recording_fakesink([]), raw_now_us=lambda: 0, clock=_test_clock())
    player.start(PCM)
    player.submit(500_000, _silence())
    generation = player.stream_generation
    player.stop()
    player.start(PCM)
    report = player.start_report()
    player.stop()

    assert player.stream_generation == generation + 1
    assert report is not None
    assert report["first_lead_ms"] is None


@requires_gst
def test_audio_clock_is_unavailable_from_a_sink_that_provides_none():
    player = StreamPlayer(sink_factory=_recording_fakesink([]), raw_now_us=lambda: 0, clock=_test_clock())
    assert player.audio_clock_ns() is None
    player.start(PCM)
    assert player.audio_clock_ns() is None
    player.stop()
