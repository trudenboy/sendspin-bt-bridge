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


class _VolumeSink:
    """A sink element stand-in that records volume/mute property writes."""

    def __init__(self) -> None:
        self.props: dict[str, object] = {}

    def find_property(self, name):
        return object() if name in ("volume", "mute") else None

    def set_property(self, name, value):
        self.props[name] = value

    def get_static_pad(self, _name):
        return None


@requires_gst
def test_a_stream_volume_set_before_playback_reaches_every_new_sink(monkeypatch):
    from unittest.mock import MagicMock

    import sendspin_bridge.services.audio.player.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "build_pipeline", lambda *, sink: (MagicMock(), MagicMock()))
    sinks: list[_VolumeSink] = []

    def factory():
        sinks.append(_VolumeSink())
        return sinks[-1]

    player = StreamPlayer(sink_factory=factory, clock=MagicMock())
    player.set_volume(0.25, True)
    player.start(PCM_S16_STEREO_48K)
    player.stop()
    player.start(PCM_S16_STEREO_48K)

    assert [s.props for s in sinks] == [{"volume": 0.25, "mute": True}] * 2


@requires_gst
def test_a_player_never_given_a_volume_leaves_the_sink_alone(monkeypatch):
    from unittest.mock import MagicMock

    import sendspin_bridge.services.audio.player.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "build_pipeline", lambda *, sink: (MagicMock(), MagicMock()))
    sink = _VolumeSink()
    player = StreamPlayer(sink_factory=lambda: sink, clock=MagicMock())

    player.start(PCM_S16_STEREO_48K)

    assert sink.props == {}


# ── Output-delay resync ──────────────────────────────────────────────────────
#
# A changed output delay moves every later play time by the same amount, but
# the audio already handed to GStreamer carries the old timestamps, and
# audiobasesink treats a step under its alignment threshold (40 ms) as jitter.
# ``resync`` must drop what is queued downstream and hand the not-yet-played
# audio back with the shifted timestamps, so any step (even 10 ms) lands
# exactly.


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


_CHUNK_US = 10_000
_CHUNK_FRAMES = 480  # 10 ms at 48 kHz


@requires_gst
@pytest.mark.parametrize("shift_us", [-10_000, -100_000, 100_000, 7_000])
def test_resync_moves_queued_audio_by_exactly_the_shift(shift_us):
    handed: list[int] = []
    player = StreamPlayer(sink_factory=_recording_fakesink(handed), raw_now_us=lambda: 0, clock=_test_clock())
    player.start(PCM_S16_STEREO_48K)
    play_times = [1_000_000 + i * _CHUNK_US for i in range(5)]
    for play_time in play_times:
        player.submit(play_time, _silence(PCM_S16_STEREO_48K, frames=_CHUNK_FRAMES))
    _wait_until(lambda: len(handed) == 5)
    assert handed == [t * 1000 for t in play_times]
    handed.clear()

    player.resync(shift_us)

    _wait_until(lambda: len(handed) == 5)
    assert handed == [(t + shift_us) * 1000 for t in play_times]
    assert player.metrics()["resync_count"] == 1
    # Audio arriving after the change continues the shifted timeline seamlessly.
    player.submit(play_times[-1] + _CHUNK_US + shift_us, _silence(PCM_S16_STEREO_48K, frames=_CHUNK_FRAMES))
    _wait_until(lambda: len(handed) == 6)
    assert handed[-1] == (play_times[-1] + _CHUNK_US + shift_us) * 1000
    assert player.metrics()["discontinuity_count"] == 0
    player.stop()


@requires_gst
def test_resync_drops_audio_that_is_already_played_or_now_due_in_the_past():
    handed: list[int] = []
    now = {"us": 0}
    player = StreamPlayer(sink_factory=_recording_fakesink(handed), raw_now_us=lambda: now["us"], clock=_test_clock())
    player.start(PCM_S16_STEREO_48K)
    for i in range(5):
        player.submit(i * _CHUNK_US, _silence(PCM_S16_STEREO_48K, frames=_CHUNK_FRAMES))
    _wait_until(lambda: len(handed) == 5)
    handed.clear()
    now["us"] = 15_000

    # Delay +10 ms: chunks move 10 ms earlier, to -10, 0, 10, 20 and 30 ms.
    player.resync(-10_000)

    _wait_until(lambda: len(handed) == 2)
    assert handed == [(20_000 - 15_000) * 1000, (30_000 - 15_000) * 1000]
    player.stop()


@requires_gst
def test_resync_rebuilds_the_output_so_nothing_queued_keeps_the_old_timing():
    """Nothing queued downstream with the old timestamps may survive."""
    player = StreamPlayer(sink_factory=_fakesink, raw_now_us=lambda: 0, clock=_test_clock())
    player.start(PCM_S16_STEREO_48K)
    old_sink = player._sink
    player.submit(1_000_000, _silence(PCM_S16_STEREO_48K, frames=_CHUNK_FRAMES))

    player.resync(-10_000)

    assert player._sink is not old_sink
    assert player._format == PCM_S16_STEREO_48K
    player.stop()


@requires_gst
def test_resync_after_clear_or_before_start_pushes_nothing():
    handed: list[int] = []
    player = StreamPlayer(sink_factory=_recording_fakesink(handed), raw_now_us=lambda: 0, clock=_test_clock())
    player.resync(-10_000)  # not started: no-op, no error
    player.start(PCM_S16_STEREO_48K)
    player.submit(1_000_000, _silence(PCM_S16_STEREO_48K, frames=_CHUNK_FRAMES))
    _wait_until(lambda: len(handed) == 1)
    player.clear()
    handed.clear()

    player.resync(-10_000)

    import time

    time.sleep(0.05)
    assert handed == []
    player.stop()
