import pytest

import sendspin_bridge.services.audio.player.player as player_module
from sendspin_bridge.services.audio.player.player import PcmFormat, StreamPlayer, _raw_now_us, gst_pts_ns

PCM_S16_STEREO_48K = PcmFormat(sample_rate=48000, channels=2, bit_depth=16)


def test_maps_play_time_onto_gst_clock():
    play_time_us = 2_000_000
    raw_now_us = 1_500_000
    gst_now_ns = 10_000_000_000
    assert gst_pts_ns(play_time_us, raw_now_us, gst_now_ns) == 10_500_000_000


def test_raw_clock_falls_back_when_monotonic_raw_is_unavailable(monkeypatch):
    observed: list[int] = []
    monkeypatch.delattr(player_module.time, "CLOCK_MONOTONIC_RAW", raising=False)
    monkeypatch.setattr(player_module.time, "clock_gettime", lambda clock_id: observed.append(clock_id) or 1.25)

    assert _raw_now_us() == 1_250_000
    assert observed == [player_module.time.CLOCK_MONOTONIC]


def _gst_available() -> bool:
    try:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        return Gst.ElementFactory.make("fakesink", None) is not None
    except Exception:
        return False


requires_gst = pytest.mark.skipif(not _gst_available(), reason="GStreamer is not available")


@requires_gst
def test_stop_rejects_further_submit():
    player = StreamPlayer(sink_factory=_fakesink)
    player.start(PCM_S16_STEREO_48K)
    player.stop()
    with pytest.raises(RuntimeError):
        player.submit(0, bytes(384))


@requires_gst
def test_submitted_buffer_reaches_sink():
    handed: list[int] = []
    player = StreamPlayer(sink_factory=_recording_fakesink(handed), raw_now_us=lambda: 1_000_000)
    player.start(PCM_S16_STEREO_48K)
    player.submit(1_000_000, _silence(PCM_S16_STEREO_48K, frames=96))
    _wait_until(lambda: len(handed) >= 1)
    player.stop()


@requires_gst
def test_clear_drops_queued_audio_and_keeps_pipeline():
    player = StreamPlayer(sink_factory=_fakesink, raw_now_us=lambda: 0)
    player.start(PCM_S16_STEREO_48K)
    player.submit(5_000_000, _silence(PCM_S16_STEREO_48K, frames=4800))
    player.clear()
    assert player.is_drained()
    player.submit(0, _silence(PCM_S16_STEREO_48K, frames=96))
    player.stop()


@requires_gst
def test_same_format_start_is_a_noop():
    player = StreamPlayer(sink_factory=_fakesink)
    player.start(PCM_S16_STEREO_48K)
    pipeline = player._pipeline
    appsrc = player._appsrc

    player.start(PCM_S16_STEREO_48K)

    assert player._pipeline is pipeline
    assert player._appsrc is appsrc
    player.stop()


@requires_gst
def test_player_is_reusable_after_stream_end():
    player = StreamPlayer(sink_factory=_fakesink, raw_now_us=lambda: 0)
    player.start(PCM_S16_STEREO_48K)
    player.submit(0, _silence(PCM_S16_STEREO_48K, frames=96))
    player.close_stream()

    player.start(PCM_S16_STEREO_48K)
    player.submit(10_000, _silence(PCM_S16_STEREO_48K, frames=96))
    player.stop()


@requires_gst
def test_bus_error_is_surfaced():
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import GLib

    from sendspin_bridge.services.audio.player.gst_support import Gst

    player = StreamPlayer(sink_factory=_fakesink, raw_now_us=lambda: 0)
    player.start(PCM_S16_STEREO_48K)
    error = GLib.Error.new_literal(Gst.CoreError.quark(), "sink failed", Gst.CoreError.FAILED)
    player._pipeline.get_bus().post(Gst.Message.new_error(player._pipeline, error, "test failure"))

    with pytest.raises(RuntimeError, match="sink failed"):
        player.metrics()
    player.stop()


@requires_gst
def test_format_change_renegotiates_without_stop():
    handed: list[int] = []
    player = StreamPlayer(sink_factory=_recording_fakesink(handed), raw_now_us=lambda: 1_000_000)
    player.start(PCM_S16_STEREO_48K)
    player.submit(1_000_000, _silence(PCM_S16_STEREO_48K, frames=96))
    _wait_until(lambda: len(handed) >= 1)
    pcm_44k = PcmFormat(sample_rate=44100, channels=2, bit_depth=16)
    player.start(pcm_44k)
    player.submit(1_000_000, _silence(pcm_44k, frames=88))
    _wait_until(lambda: len(handed) >= 2)
    player.stop()


@requires_gst
def test_buffer_pts_matches_mapped_play_time():
    try:
        import gi

        gi.require_version("GstCheck", "1.0")
        from gi.repository import GstCheck
    except (ValueError, ImportError):
        pytest.skip("GstCheck.TestClock is not introspectable")

    clock = GstCheck.TestClock.new()
    clock.set_time(0)
    player = StreamPlayer(
        sink_factory=_fakesink_sync,
        raw_now_us=lambda: 0,
        clock=clock,
    )
    player.start(PCM_S16_STEREO_48K)
    play_time_us = 100_000
    player.submit(play_time_us, _silence(PCM_S16_STEREO_48K, frames=96))
    clock.wait_for_next_pending_id()
    clock.set_time(clock.get_next_entry_time())
    _wait_until(lambda: player.metrics()["rendered_buffers"] >= 1)
    metrics = player.metrics()
    assert metrics["playback_position_us"] >= play_time_us
    player.stop()


@requires_gst
def test_pts_gap_wider_than_20ms_counts_as_discontinuity():
    player = StreamPlayer(sink_factory=_fakesink, raw_now_us=lambda: 0)
    player.start(PCM_S16_STEREO_48K)
    chunk = _silence(PCM_S16_STEREO_48K, frames=96)
    player.submit(0, chunk)
    player.submit(1_000_000, chunk)
    assert player.metrics()["discontinuity_count"] >= 1
    player.stop()


def _fakesink():
    from sendspin_bridge.services.audio.player.gst_support import Gst

    sink = Gst.ElementFactory.make("fakesink", "sink")
    assert sink is not None
    sink.set_property("sync", False)
    sink.set_property("async", False)
    return sink


def _fakesink_sync():
    from sendspin_bridge.services.audio.player.gst_support import Gst

    sink = Gst.ElementFactory.make("fakesink", "sink")
    assert sink is not None
    sink.set_property("sync", True)
    sink.set_property("async", False)
    return sink


def _recording_fakesink(handed: list[int], *, sync: bool = False):
    from sendspin_bridge.services.audio.player.gst_support import Gst

    def _make():
        sink = Gst.ElementFactory.make("fakesink", "sink")
        assert sink is not None
        sink.set_property("sync", sync)
        sink.set_property("async", False)
        sink.set_property("signal-handoffs", True)
        sink.connect("handoff", lambda _s, buf, _pad: handed.append(int(buf.pts)))
        return sink

    return _make


def _silence(fmt: PcmFormat, *, frames: int) -> bytes:
    return bytes(frames * fmt.frame_bytes())


def _wait_until(predicate, *, timeout_s: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")
