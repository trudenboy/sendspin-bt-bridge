"""Audio whose play time has passed is never handed to the output.

On a Bluetooth speaker the first chunks of a stream reached the player 0.7-1.6 s
after their play time (the output took that long to open). The player now drops
whatever is overdue, frame-exactly, so the first sample it hands on is the one
due next, and it counts what it dropped for the stream start line.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.audio.player.player import PcmFormat, StreamPlayer

PCM = PcmFormat(sample_rate=48000, channels=2, bit_depth=16)
LATENCY_NS = 200_000_000
FRAME_NS = 1_000_000_000 / 48000


def _gst_available() -> bool:
    try:
        from sendspin_bridge.services.audio.player.gst_support import Gst

        return Gst.ElementFactory.make("fakesink", None) is not None
    except Exception:
        return False


requires_gst = pytest.mark.skipif(not _gst_available(), reason="GStreamer is not available")


def _test_clock(at_ns: int):
    try:
        import gi

        gi.require_version("GstCheck", "1.0")
        from gi.repository import GstCheck
    except (ValueError, ImportError):
        pytest.skip("GstCheck.TestClock is not introspectable")
    clock = GstCheck.TestClock.new()
    clock.set_time(at_ns)
    return clock


def _recording_fakesink(handed: list[tuple[int, int]]):
    """Record (pts_ns, frames) of every buffer the sink receives."""
    from sendspin_bridge.services.audio.player.gst_support import Gst

    def _make():
        sink = Gst.ElementFactory.make("fakesink", "sink")
        assert sink is not None
        sink.set_property("sync", False)
        sink.set_property("async", False)
        sink.set_property("signal-handoffs", True)
        sink.connect(
            "handoff",
            lambda _s, buf, _pad: handed.append((int(buf.pts), buf.get_size() // PCM.frame_bytes())),
        )
        return sink

    return _make


def _audio(frames: int) -> bytes:
    return bytes(frames * PCM.frame_bytes())


def _wait_until(predicate, *, timeout_s: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


# Raw (Sendspin) time and the pipeline clock both stand at 10 s, so a play time
# of 10 s + x maps to PTS 10 s + x - latency.
NOW_US = 10_000_000
CLOCK_NS = 10_000_000_000


def _player(handed: list[tuple[int, int]], now: dict[str, int] | None = None) -> StreamPlayer:
    clock_now = now if now is not None else {"us": NOW_US}
    return StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: clock_now["us"],
        clock=_test_clock(CLOCK_NS),
        output_latency_ns=LATENCY_NS,
    )


def _pts_for(play_time_ns: float) -> int:
    return round(CLOCK_NS + (play_time_ns - NOW_US * 1000) - LATENCY_NS)


@requires_gst
def test_a_chunk_that_ended_before_now_is_not_played():
    handed: list[tuple[int, int]] = []
    player = _player(handed)
    player.start(PCM)

    player.submit(NOW_US - 30_000, _audio(480))  # 10 ms, over 20 ms ago
    player.submit(NOW_US + 100_000, _audio(480))
    _wait_until(lambda: len(handed) >= 1)
    report = player.start_report()
    player.stop()

    assert handed == [(_pts_for((NOW_US + 100_000) * 1000), 480)]
    assert report["overdue_dropped_ms"] == pytest.approx(10.0)


@requires_gst
def test_the_overdue_head_of_a_chunk_is_trimmed_to_the_next_frame():
    handed: list[tuple[int, int]] = []
    now = {"us": NOW_US + 10}  # 10 us = 0.48 frame past the chunk's start
    player = _player(handed, now)
    player.start(PCM)

    player.submit(NOW_US, _audio(480))
    _wait_until(lambda: len(handed) >= 1)
    report = player.start_report()
    player.stop()

    # Clock and raw time differ by those 10 us, so map from the raw "now" used.
    pts, frames = handed[0]
    first_kept_play_ns = NOW_US * 1000 + FRAME_NS  # one whole frame dropped
    assert frames == 479
    assert pts == round(CLOCK_NS + first_kept_play_ns - (NOW_US + 10) * 1000 - LATENCY_NS)
    assert report["overdue_dropped_ms"] == pytest.approx(FRAME_NS / 1e6, abs=1e-3)


@requires_gst
@pytest.mark.parametrize("lateness_ms", [0, 100, 700, 1600])
def test_a_late_start_hands_on_exactly_the_audio_due_from_now(lateness_ms):
    """Whatever the lateness, the first sample handed on is the one due now and
    the timeline after it is unbroken: the same playout offset on every start."""
    handed: list[tuple[int, int]] = []
    player = _player(handed)
    player.start(PCM)
    play_us = NOW_US - lateness_ms * 1000 - 7_000  # "now" falls 7 ms into a chunk
    while play_us < NOW_US + 300_000:
        player.submit(play_us, _audio(480))
        play_us += 10_000
    _wait_until(lambda: sum(f for _p, f in handed) >= 300 * 48 - 480)
    report = player.start_report()
    player.stop()

    first_pts, _frames = handed[0]
    assert first_pts == _pts_for(NOW_US * 1000)
    end = first_pts
    for pts, frames in handed:
        assert abs(pts - end) <= 1
        end = pts + round(frames * FRAME_NS)
    assert report["overdue_dropped_ms"] == pytest.approx(lateness_ms + 7.0, abs=0.03)


@requires_gst
def test_audio_that_falls_overdue_mid_stream_is_dropped_too():
    handed: list[tuple[int, int]] = []
    now = {"us": NOW_US}
    player = _player(handed, now)
    player.start(PCM)
    player.submit(NOW_US + 50_000, _audio(480))
    _wait_until(lambda: len(handed) >= 1)

    now["us"] = NOW_US + 500_000  # an underrun: the next chunk comes 440 ms late
    player.submit(NOW_US + 60_000, _audio(480))
    player.submit(NOW_US + 600_000, _audio(480))
    _wait_until(lambda: len(handed) >= 2)
    player.stop()

    assert [frames for _pts, frames in handed] == [480, 480]


@requires_gst
def test_the_start_report_tells_the_first_kept_sample_was_on_schedule():
    handed: list[tuple[int, int]] = []
    player = _player(handed)
    player.start(PCM)
    player.submit(NOW_US - 700_000, _audio(48_000))  # 1 s of audio, 700 ms overdue
    _wait_until(lambda: (player.start_report() or {}).get("first_on_schedule_error_ms") is not None)
    report = player.start_report()
    player.stop()

    assert report["overdue_dropped_ms"] == pytest.approx(700.0)
    # The kept audio reached the sink at its play time (test clock frozen at "now").
    assert report["first_on_schedule_error_ms"] == pytest.approx(0.0, abs=0.05)
    assert report["first_buffer_margin_ms"] == pytest.approx(0.0, abs=0.05)


@requires_gst
def test_a_new_output_starts_its_overdue_count_over():
    handed: list[tuple[int, int]] = []
    player = _player(handed)
    player.start(PCM)
    player.submit(NOW_US - 100_000, _audio(480))
    player.stop()
    player.start(PCM)
    report = player.start_report()
    player.stop()

    assert report["overdue_dropped_ms"] == 0.0
    assert report["first_on_schedule_error_ms"] is None
