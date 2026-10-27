"""PlayoutPlayer: the playout engine behind StreamPlayer's daemon-facing interface (Task 1.12).

The feeder thread is replaced by one whose ``start()`` does nothing, so each test drives
``player._tick(now_us)`` by hand on a fake RAW timeline; GStreamer itself is real (fakesink).
"""

from __future__ import annotations

import itertools
import time
from types import SimpleNamespace

import pytest

from sendspin_bridge.services.audio.player.player import PcmFormat
from sendspin_bridge.services.audio.playout.engine import PlayoutSettings
from sendspin_bridge.services.audio.playout.sensor import PortSample

PCM_S16_STEREO_48K = PcmFormat(sample_rate=48000, channels=2, bit_depth=16)

# StreamPlayer.metrics() keys the daemon and the telemetry already read.
STREAM_PLAYER_METRIC_KEYS = {
    "backend_output_latency_ms",
    "buffered_audio_ms",
    "playback_position_us",
    "playback_sync_error_ms",
    "discontinuity_count",
    "rendered_buffers",
    "resync_count",
    "in_place_shift_count",
    "queued_chunks",
    "overdue_dropped_ms",
}


def _gst_available() -> bool:
    try:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        return Gst.ElementFactory.make("fakesink", None) is not None
    except Exception:
        return False


requires_gst = pytest.mark.skipif(not _gst_available(), reason="GStreamer is not available")


class FakePortClock:
    """A port clock that advances 100 ms per read, or never provides a clock."""

    def __init__(self, *, never: bool = False) -> None:
        self._never = never
        self._reads = 0

    def read(self) -> PortSample | None:
        if self._never:
            return None
        self._reads += 1
        t_us = 1_000_000.0 + self._reads * 100_000.0
        return PortSample(t_us=t_us, a_ns=self._reads * 100_000_000, mono_us=t_us, bracket_us=10.0)


class _ManualThread:
    def __init__(self, *args, **kwargs) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def join(self, timeout: float | None = None) -> None:
        return None

    def is_alive(self) -> bool:
        return False


def _manual(*args, **kwargs) -> _ManualThread:
    return _ManualThread(*args, **kwargs)


def _fakesink():
    from sendspin_bridge.services.audio.player.gst_support import Gst

    sink = Gst.ElementFactory.make("fakesink", "sink")
    assert sink is not None
    sink.set_property("sync", False)
    sink.set_property("async", False)
    return sink


def _recording_fakesink(handed: list):
    from sendspin_bridge.services.audio.player.gst_support import Gst

    def _make():
        sink = Gst.ElementFactory.make("fakesink", "sink")
        assert sink is not None
        sink.set_property("sync", False)
        sink.set_property("async", False)
        sink.set_property("signal-handoffs", True)
        # The buffer may be reused after the callback: keep its timestamps only.
        sink.connect(
            "handoff",
            lambda _s, buf, _pad: handed.append(SimpleNamespace(pts=int(buf.pts), duration=int(buf.duration))),
        )
        return sink

    return _make


def _player(**kw):
    from sendspin_bridge.services.audio.playout.gst_output import PlayoutPlayer

    kw.setdefault("sink_factory", _fakesink)
    kw.setdefault("settings", PlayoutSettings(mode="measure"))
    kw.setdefault("out_rate", 48000)
    kw.setdefault("port_factory", lambda sink: FakePortClock())
    kw.setdefault("thread_factory", _manual)
    return PlayoutPlayer(**kw)


def _wait_until(predicate, *, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def _drive_paced(player, ticks: int, *, start_us: float = 1_000_000.0) -> None:
    """Tick on the fake timeline, giving appsrc's streaming thread real time to drain between ticks."""
    for i in range(ticks):
        player._tick(now_us=start_us + i * 5_000)
        time.sleep(0.002)


# -- the brief's tests -------------------------------------------------------------------


@requires_gst
def test_feeder_keeps_the_lead_and_writes_contiguous_blocks():
    from sendspin_bridge.services.audio.playout.gst_output import PlayoutPlayer

    handed = []
    player = PlayoutPlayer(
        sink_factory=_recording_fakesink(handed),
        settings=PlayoutSettings(mode="measure"),
        out_rate=48000,
        port_factory=lambda sink: FakePortClock(),
        thread_factory=_manual,
    )
    player.start(PCM_S16_STEREO_48K)
    for i in range(20):
        player._tick(now_us=1_000_000.0 + i * 5_000)
    pts = [(b.pts, b.duration) for b in handed]
    assert all(p2 == p1 + d1 for (p1, d1), (p2, _) in itertools.pairwise(pts))
    player.stop()


@requires_gst
def test_input_rate_change_keeps_the_sink():
    player = _player()
    player.start(PCM_S16_STEREO_48K)
    sink = player._sink
    player.start(PcmFormat(sample_rate=44100, channels=2, bit_depth=16))
    assert player._sink is sink
    player.stop()


@requires_gst
def test_resync_never_rebuilds_the_pipeline():
    player = _player()
    player.start(PCM_S16_STEREO_48K)
    pipeline = player._pipeline
    player.submit(2_000_000, bytes(19200))
    player._tick(now_us=1_000_000.0)
    assert player.resync(10_000) is True
    player._tick(now_us=1_005_000.0)
    assert player._pipeline is pipeline
    player.stop()


@requires_gst
def test_a_sink_without_clock_reports_fallback():
    player = _player(port_factory=lambda sink: FakePortClock(never=True))
    player.start(PCM_S16_STEREO_48K)
    for i in range(500):
        player._tick(now_us=1_000_000.0 + i * 5_000)
    assert player.metrics()["playout"]["state"] == "fallback"
    player.stop()


# -- further behaviour -------------------------------------------------------------------


@requires_gst
def test_blocks_reach_the_sink_from_zero_with_their_durations():
    handed = []
    player = _player(sink_factory=_recording_fakesink(handed))
    player.start(PCM_S16_STEREO_48K)
    _drive_paced(player, 20)
    _wait_until(lambda: len(handed) >= 5)
    assert handed[0].pts == 0
    assert all(b.duration == 10_000_000 for b in handed[:5])  # BLOCK_MS at 48 kHz
    player.stop()


@requires_gst
def test_a_sink_that_never_blocks_is_fed_no_faster_than_time_plus_the_lead():
    handed = []
    player = _player(sink_factory=_recording_fakesink(handed))
    player.start(PCM_S16_STEREO_48K)
    _drive_paced(player, 200)  # 1 s on the fake timeline
    _wait_until(lambda: len(handed) >= 50)
    time.sleep(0.05)
    end_ns = handed[-1].pts + handed[-1].duration
    assert end_ns <= 1_200_000_000  # 1 s elapsed + lead + slack, not 8x real time
    player.stop()


@requires_gst
def test_a_sink_with_a_clock_does_not_fall_back():
    player = _player()
    player.start(PCM_S16_STEREO_48K)
    for i in range(500):
        player._tick(now_us=1_000_000.0 + i * 5_000)
    playout = player.metrics()["playout"]
    assert playout["state"] != "fallback"
    assert playout["fallback_reason"] is None
    player.stop()


@requires_gst
def test_fallback_names_the_reason():
    player = _player(port_factory=lambda sink: FakePortClock(never=True))
    player.start(PCM_S16_STEREO_48K)
    for i in range(390):  # 1.95 s: not yet
        player._tick(now_us=1_000_000.0 + i * 5_000)
    assert player.metrics()["playout"]["state"] != "fallback"
    for i in range(390, 430):
        player._tick(now_us=1_000_000.0 + i * 5_000)
    playout = player.metrics()["playout"]
    assert playout["state"] == "fallback"
    assert "clock" in playout["fallback_reason"]
    player.stop()


@requires_gst
def test_metrics_expose_stream_player_keys_and_playout():
    player = _player()
    assert set(player.metrics()) >= STREAM_PLAYER_METRIC_KEYS
    player.start(PCM_S16_STEREO_48K)
    player.submit(1_100_000, bytes(19200))
    for i in range(40):
        player._tick(now_us=1_000_000.0 + i * 5_000)
    metrics = player.metrics()
    assert set(metrics) >= STREAM_PLAYER_METRIC_KEYS
    assert metrics["rendered_buffers"] > 0
    assert metrics["playout"]["mode"] == "measure"
    assert metrics["playout"]["state"] == "playing"
    player.stop()


@requires_gst
def test_stop_is_idempotent_and_closes_the_output():
    player = _player()
    player.stop()  # before any start
    player.start(PCM_S16_STEREO_48K)
    player._tick(now_us=1_000_000.0)
    player.stop()
    player.stop()
    assert player._pipeline is None
    assert player.needs_output() is True
    with pytest.raises(RuntimeError):
        player.submit(0, bytes(384))


@requires_gst
def test_stop_joins_the_feeder_thread():
    joined = []

    class _Thread(_ManualThread):
        def join(self, timeout=None):
            joined.append(timeout)

    player = _player(thread_factory=lambda *a, **k: _Thread())
    player.start(PCM_S16_STEREO_48K)
    player.stop()
    assert len(joined) == 1


@requires_gst
def test_public_methods_only_enqueue_until_the_feeder_ticks():
    handed = []
    player = _player(sink_factory=_recording_fakesink(handed))
    player.start(PCM_S16_STEREO_48K)
    player.submit(1_000_000, bytes(19200))
    player.clear()
    player.set_volume(0.5, False)
    time.sleep(0.05)
    assert handed == []  # nothing reached GStreamer from the caller thread
    assert player.metrics()["rendered_buffers"] == 0
    player._tick(now_us=1_000_000.0)
    assert player.metrics()["rendered_buffers"] > 0
    player.stop()


@requires_gst
def test_volume_is_applied_by_the_feeder():
    from sendspin_bridge.services.audio.player.gst_support import Gst

    def _sink_with_volume():
        sink = Gst.ElementFactory.make("pulsesink", "sink")
        if sink is None:
            pytest.skip("pulsesink is not available")
        return sink

    player = _player()
    player.start(PCM_S16_STEREO_48K)
    # Swap in an element with volume/mute to observe what the feeder sets.
    probe = _sink_with_volume()
    player._sink = probe
    player.set_volume(0.25, True)
    assert probe.get_property("mute") is False
    player._tick(now_us=1_000_000.0)
    assert probe.get_property("volume") == pytest.approx(0.25)
    assert probe.get_property("mute") is True
    player._sink = None  # not part of the pipeline: do not tear it down
    player.stop()


@requires_gst
def test_resync_without_audio_reports_nothing_playing():
    player = _player()
    assert player.resync(10_000) is False  # no output
    player.start(PCM_S16_STEREO_48K)
    player._tick(now_us=1_000_000.0)
    assert player.resync(10_000) is False
    player.submit(2_000_000, bytes(19200))
    assert player.resync(10_000) is True  # a submit still waiting for the feeder counts
    player.clear()
    assert player.resync(10_000) is False
    player.stop()


@requires_gst
def test_daemon_facing_output_lifecycle():
    player = _player()
    assert player.needs_output() is True
    assert player.stream_generation == 0
    assert player.awaiting_reopen is None
    output = player.prepare_output(PCM_S16_STEREO_48K)
    assert player.needs_output() is True  # prepare touches no player state
    player.adopt_output(output, PCM_S16_STEREO_48K)
    assert player.needs_output() is False
    assert player.stream_generation == 1
    player.start(PCM_S16_STEREO_48K)  # the running output serves it
    assert player.stream_generation == 1
    player.forget_reopen()
    spare = player.prepare_output(PCM_S16_STEREO_48K)
    player.discard_output(spare)
    assert isinstance(player.now_us(), int)
    player.submit(player.now_us() + 500_000, bytes(19200))
    report = player.start_report()
    assert report is not None and report["first_lead_ms"] == pytest.approx(500.0, abs=50.0)
    assert player.audio_clock_ns() is None  # fakesink provides no clock
    player.stop()
    assert player.start_report() is None


@requires_gst
def test_end_stream_closes_like_stop_and_close_stream_keeps_the_output():
    player = _player()
    player.start(PCM_S16_STEREO_48K)
    pipeline = player._pipeline
    player.close_stream()
    assert player._pipeline is pipeline
    assert player.needs_output() is False
    player.end_stream()
    assert player._pipeline is None


@requires_gst
def test_is_drained_follows_queued_audio():
    player = _player()
    assert player.is_drained() is True
    player.start(PCM_S16_STEREO_48K)
    player.submit(5_000_000, bytes(19200))
    assert player.is_drained() is False
    player._tick(now_us=1_000_000.0)
    assert player.is_drained() is False
    player.clear()
    player._tick(now_us=1_005_000.0)
    assert player.is_drained() is True
    player.stop()


def test_k0_is_the_resampler_latency_never_negative():
    from sendspin_bridge.services.audio.playout.gst_output import k0_from_latencies

    assert k0_from_latencies(resample_ns=0, convert_ns=0) == 0
    assert k0_from_latencies(resample_ns=1_500_000, convert_ns=500_000) == 1_000_000
    assert k0_from_latencies(resample_ns=None, convert_ns=0) == 0
    assert k0_from_latencies(resample_ns=100, convert_ns=500) == 0


def test_output_rate_from_the_sink_sample_spec_else_48000():
    from sendspin_bridge.services.audio.playout.gst_output import output_rate

    assert output_rate(None) == 48000
    assert output_rate("bluez_sink.X", probe=lambda device: 44100) == 44100
    assert output_rate("bluez_sink.X", probe=lambda device: None) == 48000

    def _boom(device):
        raise OSError("no PulseAudio")

    assert output_rate("bluez_sink.X", probe=_boom) == 48000


# -- pipeline.py --------------------------------------------------------------------------


@requires_gst
def test_playout_pulsesink_runs_unsynced_and_provides_its_clock():
    from sendspin_bridge.services.audio.player.gst_support import Gst
    from sendspin_bridge.services.audio.player.pipeline import playout_pulsesink_factory

    if Gst.ElementFactory.find("pulsesink") is None:
        pytest.skip("pulsesink is not available")
    sink = playout_pulsesink_factory(device="bluez_sink.X.a2dp_sink", client_name="spk", buffer_time_us=800_000)()
    assert sink.get_property("sync") is False
    assert sink.get_property("provide-clock") is True
    assert int(sink.get_property("slave-method")) == 2  # none
    assert sink.get_property("buffer-time") == 800_000
    assert sink.get_property("device") == "bluez_sink.X.a2dp_sink"
    assert sink.get_property("client-name") == "spk"


@requires_gst
def test_playout_pipeline_fixes_the_output_format_and_the_system_clock():
    from sendspin_bridge.services.audio.player.gst_support import Gst
    from sendspin_bridge.services.audio.player.pipeline import build_playout_pipeline

    pipeline, appsrc = build_playout_pipeline(sink=_fakesink(), out_rate=44100)
    assert all(pipeline.get_by_name(name) is not None for name in ("src", "convert", "resample", "outcaps", "sink"))
    caps = pipeline.get_by_name("outcaps").get_property("caps").get_structure(0)
    assert (caps.get_value("format"), caps.get_value("channels"), caps.get_value("rate")) == ("S16LE", 2, 44100)
    assert appsrc.get_property("is-live") is True
    assert appsrc.get_property("format") == Gst.Format.TIME
    assert pipeline.get_clock() is None or pipeline.get_clock() == Gst.SystemClock.obtain()
    pipeline.set_state(Gst.State.PLAYING)
    assert pipeline.get_clock() == Gst.SystemClock.obtain()
    pipeline.set_state(Gst.State.NULL)


@requires_gst
def test_the_real_feeder_thread_feeds_and_stops():
    import threading

    handed = []
    player = _player(sink_factory=_recording_fakesink(handed), thread_factory=threading.Thread)
    player.start(PCM_S16_STEREO_48K)
    player.submit(player.now_us() + 100_000, bytes(19200))
    _wait_until(lambda: player.metrics()["rendered_buffers"] >= 10)
    _wait_until(lambda: player.metrics()["playout"]["state"] == "playing")
    feeder = player._thread
    player.stop()
    assert not feeder.is_alive()
    pts = [(b.pts, b.duration) for b in handed]
    assert len(pts) >= 10
    assert all(p2 == p1 + d1 for (p1, d1), (p2, _) in itertools.pairwise(pts))
