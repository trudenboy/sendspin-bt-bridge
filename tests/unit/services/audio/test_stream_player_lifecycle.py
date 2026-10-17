"""Outputs are released when they are done, and a rebuild opens off the loop.

Every output the player builds must be freed once it is stopped or
discarded: a sink pad probe that referenced the pipeline kept each one alive
(a reference cycle through GStreamer that Python cannot collect), so every
stream start and every rebuild leaked a whole pipeline. And a rebuild after a
delay change (beyond what applies in place) must not open the new output on
the event loop: the daemon opens it in a worker, as at a stream start, while
the audio waits.
"""

from __future__ import annotations

import asyncio
import gc
import threading
import time
import weakref

import pytest

from sendspin_bridge.services.audio.player.player import InPlaceShift, PcmFormat, StreamPlayer

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


def _recording_fakesink(handed: list[tuple[int, int]]):
    from sendspin_bridge.services.audio.player.gst_support import Gst

    def _make():
        sink = Gst.ElementFactory.make("fakesink", "sink")
        assert sink is not None
        sink.set_property("sync", False)
        sink.set_property("async", False)
        sink.set_property("signal-handoffs", True)
        sink.connect("handoff", lambda _s, buf, _pad: handed.append((int(buf.pts), buf.get_size())))
        return sink

    return _make


def _wait_until(predicate, *, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def _chunk() -> bytes:
    return bytes(480 * PCM.frame_bytes())


# ── No leaked outputs ────────────────────────────────────────────────────────


@requires_gst
def test_rebuilt_and_restarted_outputs_are_all_freed():
    player = StreamPlayer(sink_factory=_recording_fakesink([]), raw_now_us=lambda: 0, clock=_test_clock())
    pipelines: list[weakref.ref] = []
    sinks: list[weakref.ref] = []
    for _ in range(10):
        player.start(PCM)
        pipelines.append(weakref.ref(player._pipeline))
        sinks.append(weakref.ref(player._sink))
        player.submit(1_000_000, _chunk())
        assert player.resync(-3_000_000) is True  # beyond in place: a rebuild
        pipelines.append(weakref.ref(player._pipeline))
        player.stop()  # a stream end

    gc.collect()

    assert sum(ref() is not None for ref in pipelines) == 0
    assert sum(ref() is not None for ref in sinks) == 0


@requires_gst
def test_a_discarded_output_is_freed():
    player = StreamPlayer(sink_factory=_recording_fakesink([]), raw_now_us=lambda: 0, clock=_test_clock())
    output = player.prepare_output(PCM)
    ref = weakref.ref(output.pipeline)

    StreamPlayer.discard_output(output)
    del output
    gc.collect()

    assert ref() is None


@requires_gst
def test_the_sink_probe_still_reports_rendering_and_sync_error():
    handed: list[tuple[int, int]] = []
    player = StreamPlayer(sink_factory=_recording_fakesink(handed), raw_now_us=lambda: 0, clock=_test_clock())
    player.start(PCM)
    player.submit(1_000_000, _chunk())
    _wait_until(lambda: len(handed) == 1)

    metrics = player.metrics()
    assert metrics["rendered_buffers"] == 1
    assert metrics["playback_sync_error_ms"] == -1000.0
    player.stop()


# ── A rebuild opens its output off the loop ──────────────────────────────────


@requires_gst
def test_a_deferred_rebuild_closes_the_output_and_hands_the_audio_to_the_next_one():
    handed: list[tuple[int, int]] = []
    built: list[int] = []
    make = _recording_fakesink(handed)

    def factory():
        built.append(1)
        return make()

    player = StreamPlayer(
        sink_factory=factory,
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
        max_in_place_shift_us=2_000_000,
        defer_rebuild_open=True,
    )
    player.start(PCM)
    for i in range(3):
        player.submit(1_000_000 + i * 10_000, _chunk())
    _wait_until(lambda: len(handed) == 3)
    handed.clear()

    assert player.resync(2_500_000) is True

    assert len(built) == 1  # nothing opened here
    assert player.needs_output()
    assert player.awaiting_reopen == PCM
    player.adopt_output(player.prepare_output(PCM), PCM)
    _wait_until(lambda: len(handed) == 3)
    assert [pts for pts, _ in handed] == [(1_000_000 + i * 10_000 + 2_500_000) * 1000 - LATENCY_NS for i in range(3)]
    assert player.awaiting_reopen is None
    player.stop()


@requires_gst
def test_a_stream_end_forgets_the_audio_waiting_for_a_deferred_rebuild():
    handed: list[tuple[int, int]] = []
    player = StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        defer_rebuild_open=True,
    )
    player.start(PCM)
    player.submit(1_000_000, _chunk())
    _wait_until(lambda: len(handed) == 1)
    player.resync(-10_000)
    player.stop()
    handed.clear()

    player.start(PCM)

    time.sleep(0.05)
    assert handed == []
    assert player.awaiting_reopen is None
    player.stop()


@requires_gst
@pytest.mark.asyncio
async def test_the_daemon_rebuilds_off_the_loop(tmp_path, caplog):
    import logging

    from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

    handed: list[tuple[int, int]] = []
    opened_in: list[str] = []
    player = StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        max_in_place_shift_us=2_000_000,
        defer_rebuild_open=True,
    )
    prepare = player.prepare_output

    def slow_prepare(fmt):
        opened_in.append(threading.current_thread().name)
        time.sleep(0.2)
        return prepare(fmt)

    player.prepare_output = slow_prepare  # type: ignore[method-assign]
    daemon = BridgeDaemon(
        DaemonArgs(client_id="id", client_name="Speaker", identity_path=str(tmp_path / "identity.key")), {}, None
    )
    daemon._schedule_start_timing_line = lambda: None  # type: ignore[method-assign]
    daemon._player = player
    player.adopt_output(prepare(PCM), PCM)
    player.submit(5_000_000, _chunk())
    _wait_until(lambda: len(handed) == 1)
    handed.clear()
    daemon._player_delay_ms = 3000.0
    daemon._target_delay_ms = 0.0

    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")
    began = time.monotonic()
    daemon._apply_output_delay()  # +3 s: beyond in place
    held_ms = (time.monotonic() - began) * 1000

    assert held_ms < 100
    assert "Output delay changed 3000→0 ms; player resynchronised (output rebuilt)" in caplog.text
    for _ in range(100):
        if handed:
            break
        await asyncio.sleep(0.02)
    assert opened_in and opened_in[0] != threading.current_thread().name
    assert handed == [((5_000_000 + 3_000_000) * 1000, 480 * PCM.frame_bytes())]
    player.stop()


# ── Inserted silence counts toward the in-place limit ────────────────────────


@requires_gst
def test_later_shifts_adding_up_past_the_limit_before_any_audio_rebuild():
    handed: list[tuple[int, int]] = []
    player = StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
        max_in_place_shift_us=2_000_000,
    )
    player.start(PCM)
    player.submit(1_000_000, _chunk())

    assert player.resync(1_500_000) == InPlaceShift(shift_us=1_500_000)
    assert player.resync(1_000_000) is True  # 2.5 s of silence before any audio: rebuild

    assert player.metrics()["resync_count"] == 1
    player.stop()


@requires_gst
def test_audio_after_inserted_silence_starts_the_count_over():
    handed: list[tuple[int, int]] = []
    player = StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
        max_in_place_shift_us=2_000_000,
    )
    player.start(PCM)
    player.submit(1_000_000, _chunk())
    assert player.resync(1_500_000) == InPlaceShift(shift_us=1_500_000)
    player.submit(1_010_000 + 1_500_000, _chunk())

    assert player.resync(1_000_000) == InPlaceShift(shift_us=1_000_000)
    assert player.metrics()["resync_count"] == 0
    player.stop()
