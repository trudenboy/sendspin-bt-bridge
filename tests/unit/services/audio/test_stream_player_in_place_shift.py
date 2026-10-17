"""An output delay change is applied in place, on the running output.

Rebuilding the output for every delay change opened a new PulseAudio stream
each time, and a Bluetooth speaker re-buffers every new stream with a different
offset (hundreds of ms), so tuning a speaker by ear could not converge. The
player now keeps its pipeline and stream: the audio already handed on plays as
it was, and from there the timeline continues contiguously with the shift
applied: a later play time inserts silence, an earlier one skips audio, both
frame-exact and with a short fade so nothing clicks. Everything after the shift
plays exactly when a rebuild would have played it.
"""

from __future__ import annotations

import math
from array import array
from itertools import pairwise

import pytest

from sendspin_bridge.services.audio.player.player import InPlaceShift, PcmFormat, StreamPlayer
from sendspin_bridge.services.audio.player.splice import fade_frames, splice_region

PCM = PcmFormat(sample_rate=48000, channels=2, bit_depth=16)
RATE = PCM.sample_rate
LATENCY_NS = 200_000_000
BASE_US = 1_000_000
CHUNK_FRAMES = 480  # 10 ms
CLICK_EVERY = 4800  # 100 ms
CLICK_AT = 100  # clicks sit 100 frames into their 100 ms period, off every chunk edge
JUNCTION_CHUNKS = 25  # 250 ms handed on before the delay changes
TOTAL_CHUNKS = 200  # 2 s of content


# ── Pure splice ──────────────────────────────────────────────────────────────


def _pcm(samples: list[int]) -> bytes:
    return array("h", samples).tobytes()


def _samples(data: bytes) -> list[int]:
    return list(array("h", data))


def _sine(frames: int, *, start: int = 0, amp: int = 10_000, hz: float = 1000.0) -> list[int]:
    out: list[int] = []
    for i in range(start, start + frames):
        v = round(amp * math.sin(2 * math.pi * hz * i / RATE))
        out += [v, v]
    return out


def test_the_fade_is_five_milliseconds():
    assert fade_frames(48000) == 240
    assert fade_frames(44100) == 220


def test_no_shift_splices_to_the_untouched_audio():
    x = _sine(1000)
    region, consumed = splice_region(_pcm(x), shift_frames=0, fade=240, fmt=PCM)
    assert consumed == 240
    assert _samples(region) == x[: 240 * 2]


@pytest.mark.parametrize("shift_frames", [-336, -4800, -48000])
def test_skipping_ahead_crossfades_into_the_audio_due_now(shift_frames):
    dropped = -shift_frames
    x = [7_000, -7_000] * (dropped + 2000)  # constant per channel: a crossfade of it is flat
    region, consumed = splice_region(_pcm(x), shift_frames=shift_frames, fade=240, fmt=PCM)
    assert consumed == 240 + dropped  # the region ends where audio x[consumed:] continues
    assert len(region) == 240 * PCM.frame_bytes()
    assert set(_samples(region)) == {7_000, -7_000}


@pytest.mark.parametrize("shift_frames", [336, 4800, 48000])
def test_inserting_silence_fades_out_then_back_in(shift_frames):
    x = [8_000, 8_000] * 1000
    region, consumed = splice_region(_pcm(x), shift_frames=shift_frames, fade=240, fmt=PCM)
    out = _samples(region)[::2]
    assert consumed == 240
    assert len(out) == shift_frames + 240
    assert out[0] == 8_000 and out[239] < 100  # faded out over 5 ms
    assert all(v == 0 for v in out[240:shift_frames])  # then silence
    assert out[shift_frames] == 0 and out[-1] > 7_900  # and back in, on the shifted timeline


@pytest.mark.parametrize("shift_frames", [-4801, -263, -23, 50, 333, 4800])
def test_a_shift_never_clicks(shift_frames):
    """The largest step between samples stays within the sine's own (plus the fade's slope)."""
    x = _sine(6000, start=37)
    natural = max(abs(a - b) for a, b in zip(x[2::2], x[:-2:2], strict=True))
    region, _ = splice_region(_pcm(x), shift_frames=shift_frames, fade=240, fmt=PCM)
    out = _sine(1, start=36) + _samples(region)  # from the sample handed on just before
    left = out[::2]
    worst = max(abs(a - b) for a, b in zip(left[1:], left[:-1], strict=True))
    assert worst <= natural + 10_000 / 240 * 2 + 2


@pytest.mark.parametrize("bit_depth", [24, 32])
def test_wider_samples_splice_too(bit_depth):
    fmt = PcmFormat(sample_rate=48000, channels=1, bit_depth=bit_depth)
    width = bit_depth // 8
    value = 3_000_000
    x = b"".join(value.to_bytes(width, "little", signed=True) for _ in range(2000))
    region, consumed = splice_region(x, shift_frames=-500, fade=240, fmt=fmt)
    assert consumed == 740
    assert {int.from_bytes(region[i : i + width], "little", signed=True) for i in range(0, len(region), width)} == {
        value
    }


# ── The player, on a real GStreamer pipeline ─────────────────────────────────


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


def _recording_fakesink(handed: list[tuple[int, bytes]]):
    from sendspin_bridge.services.audio.player.gst_support import Gst

    def _make():
        sink = Gst.ElementFactory.make("fakesink", "sink")
        assert sink is not None
        sink.set_property("sync", False)
        sink.set_property("async", False)
        sink.set_property("signal-handoffs", True)

        def _handoff(_s, buf, _pad):
            ok, info = buf.map(Gst.MapFlags.READ)
            assert ok
            handed.append((int(buf.pts), bytes(info.data)))
            buf.unmap(info)

        sink.connect("handoff", _handoff)
        return sink

    return _make


def _wait_until(predicate, *, timeout_s: float = 3.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met before timeout")


def _click_chunk(index: int) -> bytes:
    """10 ms of a click track: a one-frame click every 100 ms, its size telling which."""
    samples = [0] * (CHUNK_FRAMES * 2)
    for f in range(CHUNK_FRAMES):
        content = index * CHUNK_FRAMES + f
        if content % CLICK_EVERY == CLICK_AT:
            amp = 100 * (content // CLICK_EVERY + 1)
            samples[2 * f] = samples[2 * f + 1] = amp
    return _pcm(samples)


def _content_time_ns(content: int) -> int:
    return BASE_US * 1000 + content * 1_000_000_000 // RATE - LATENCY_NS


def _clicks_heard(handed: list[tuple[int, bytes]]) -> dict[int, float]:
    """Click number → time (ns, output clock) it is played at."""
    heard: dict[int, float] = {}
    for pts, data in handed:
        left = _samples(data)[::2]
        for j, v in enumerate(left):
            if v >= 50:
                heard[round(v / 100) - 1] = pts + j * 1e9 / RATE
    return heard


def _player(handed, **kwargs) -> StreamPlayer:
    return StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: 0,
        clock=_test_clock(),
        output_latency_ns=LATENCY_NS,
        max_in_place_shift_us=kwargs.pop("max_in_place_shift_us", 2_000_000),
        **kwargs,
    )


def _play_with_a_shift(player: StreamPlayer, handed, shift_us: int):
    player.start(PCM)
    for i in range(JUNCTION_CHUNKS):
        player.submit(BASE_US + i * 10_000, _click_chunk(i))
    _wait_until(lambda: len(handed) == JUNCTION_CHUNKS)
    pipeline, sink, generation = player._pipeline, player._sink, player.stream_generation

    outcome = player.resync(shift_us)

    for i in range(JUNCTION_CHUNKS, TOTAL_CHUNKS):
        player.submit(BASE_US + i * 10_000 + shift_us, _click_chunk(i))
    shift_frames = shift_us * RATE // 1_000_000
    expected_frames = TOTAL_CHUNKS * CHUNK_FRAMES + shift_frames
    _wait_until(lambda: sum(len(d) for _, d in handed) // PCM.frame_bytes() == expected_frames)
    return outcome, (pipeline, sink, generation)


@requires_gst
@pytest.mark.parametrize("shift_ms", [-1000, -100, -7, 7, 100, 1000])
def test_a_click_track_plays_on_the_shifted_schedule_without_a_new_output(shift_ms):
    handed: list[tuple[int, bytes]] = []
    player = _player(handed)
    shift_us = shift_ms * 1000

    outcome, (pipeline, sink, generation) = _play_with_a_shift(player, handed, shift_us)

    assert outcome == InPlaceShift(shift_us=shift_us)
    # The same output (pipeline, sink element, so the same PulseAudio stream).
    assert player._pipeline is pipeline and player._sink is sink
    assert player.stream_generation == generation
    junction = JUNCTION_CHUNKS * CHUNK_FRAMES
    expected: dict[int, int] = {}
    for n in range(TOTAL_CHUNKS * CHUNK_FRAMES // CLICK_EVERY):
        content = n * CLICK_EVERY + CLICK_AT
        if content < junction:
            expected[n] = _content_time_ns(content)
        elif shift_us < 0 and content < junction - shift_us * RATE // 1_000_000:
            continue  # skipped: the shifted schedule has already passed it
        else:
            expected[n] = _content_time_ns(content) + shift_us * 1000
    heard = _clicks_heard(handed)
    assert sorted(heard) == sorted(expected)
    for n, at_ns in expected.items():
        assert abs(heard[n] - at_ns) <= 1_000_000  # within 1 ms (in fact within a frame)
        assert abs(heard[n] - at_ns) < 1e9 / RATE
    # The output timeline stays contiguous: no gap, no overlap, no discontinuity.
    for (pts, data), (next_pts, _) in pairwise(handed):
        assert abs(pts + len(data) // PCM.frame_bytes() * 1e9 / RATE - next_pts) < 1e9 / RATE
    metrics = player.metrics()
    assert metrics["discontinuity_count"] == 0
    assert metrics["resync_count"] == 0
    assert metrics["in_place_shift_count"] == 1
    player.stop()


@requires_gst
def test_a_shift_beyond_the_in_place_limit_rebuilds_the_output():
    handed: list[tuple[int, bytes]] = []
    player = _player(handed, max_in_place_shift_us=2_000_000)
    player.start(PCM)
    player.submit(BASE_US, _click_chunk(0))
    _wait_until(lambda: len(handed) == 1)
    sink = player._sink

    assert player.resync(-2_500_000) is True

    assert player._sink is not sink
    assert player.metrics()["resync_count"] == 1
    assert player.metrics()["in_place_shift_count"] == 0
    player.stop()


@requires_gst
def test_a_player_without_an_in_place_limit_keeps_rebuilding():
    handed: list[tuple[int, bytes]] = []
    player = _player(handed, max_in_place_shift_us=0)
    player.start(PCM)
    player.submit(BASE_US, _click_chunk(0))
    _wait_until(lambda: len(handed) == 1)
    sink = player._sink

    assert player.resync(-10_000) is True
    assert player._sink is not sink
    player.stop()


@requires_gst
def test_nothing_playing_has_nothing_to_shift():
    handed: list[tuple[int, bytes]] = []
    player = _player(handed)
    assert player.resync(-10_000) is False


@requires_gst
def test_an_output_that_ran_dry_just_takes_the_new_play_times():
    """Nothing queued is left to continue from: the next chunk plays at its own time."""
    handed: list[tuple[int, bytes]] = []
    now = {"us": 0}
    player = StreamPlayer(
        sink_factory=_recording_fakesink(handed),
        raw_now_us=lambda: now["us"],
        clock=_test_clock(),
        max_in_place_shift_us=2_000_000,
    )
    player.start(PCM)
    player.submit(BASE_US, _click_chunk(0))
    _wait_until(lambda: len(handed) == 1)
    player._pipeline.get_clock().set_time(5_000_000_000)
    now["us"] = 5_000_000

    assert player.resync(-10_000) == InPlaceShift(shift_us=-10_000)
    player.submit(6_000_000, _click_chunk(1))

    _wait_until(lambda: len(handed) == 2)
    assert handed[1][0] == 6_000_000_000
    assert len(handed[1][1]) == CHUNK_FRAMES * PCM.frame_bytes()
    player.stop()


@requires_gst
def test_stop_forgets_a_shift_still_waiting_for_audio():
    handed: list[tuple[int, bytes]] = []
    player = _player(handed)
    player.start(PCM)
    player.submit(BASE_US, _click_chunk(0))
    _wait_until(lambda: len(handed) == 1)
    player.resync(-100_000)
    player.submit(BASE_US + 10_000 - 100_000, _click_chunk(1))  # held: 105 ms are needed
    player.stop()
    handed.clear()

    player.start(PCM)
    player.submit(3_000_000, _click_chunk(0))

    _wait_until(lambda: len(handed) == 1)
    assert handed[0][0] == 3_000_000_000 - LATENCY_NS
    player.stop()


@requires_gst
def test_a_rebuild_after_an_in_place_shift_keeps_the_shifted_timeline():
    """The record kept for a rebuild follows the in-place shift, so a later
    rebuild re-times the queued audio from where it now belongs."""
    handed: list[tuple[int, bytes]] = []
    player = _player(handed, max_in_place_shift_us=50_000)
    player.start(PCM)
    for i in range(3):
        player.submit(BASE_US + i * 10_000, _click_chunk(i))
    _wait_until(lambda: len(handed) == 3)
    player.resync(20_000)
    handed.clear()

    assert player.resync(100_000) is True

    _wait_until(lambda: len(handed) == 3)
    assert [pts for pts, _ in handed] == [(BASE_US + i * 10_000 + 120_000) * 1000 - LATENCY_NS for i in range(3)]
    player.stop()


def _sine_chunk(index: int) -> bytes:
    # The junction (after chunk 24) falls on a crest: a hard cut there would click.
    return _pcm(_sine(CHUNK_FRAMES, start=index * CHUNK_FRAMES + 1, hz=997.0))


def _left_output(handed: list[tuple[int, bytes]]) -> list[int]:
    out: list[int] = []
    for _pts, data in handed:
        out += _samples(data)[::2]
    return out


@requires_gst
def test_a_later_play_time_fills_the_gap_at_once():
    """Silence is handed on with the shift itself, so the output never runs dry
    waiting for the server, which sends the next audio that much later."""
    handed: list[tuple[int, bytes]] = []
    player = _player(handed)
    player.start(PCM)
    for i in range(JUNCTION_CHUNKS):
        player.submit(BASE_US + i * 10_000, _sine_chunk(i))
    _wait_until(lambda: len(handed) == JUNCTION_CHUNKS)
    before = _left_output(handed)

    player.resync(100_000)

    _wait_until(lambda: len(_left_output(handed)) == len(before) + 4800)
    after = _left_output(handed)[len(before) :]
    assert after[0] == pytest.approx(before[-1], abs=60)  # it carries on from the last sample...
    assert after[239] == pytest.approx(0, abs=60)  # ...fades out over 5 ms...
    assert all(v == 0 for v in after[240:])  # ...and is silent until the shifted audio
    end_pts, end_data = handed[-1]
    assert end_pts + len(end_data) // PCM.frame_bytes() * 1e9 / RATE == pytest.approx(
        _content_time_ns(JUNCTION_CHUNKS * CHUNK_FRAMES) + 100_000_000, abs=1e9 / RATE
    )
    player.stop()


@requires_gst
@pytest.mark.parametrize("shifts_ms", [[-100], [-7], [3], [7], [100], [100, -50], [-30, 60]])
def test_the_output_never_clicks_across_a_shift(shifts_ms):
    handed: list[tuple[int, bytes]] = []
    player = _player(handed)
    player.start(PCM)
    for i in range(JUNCTION_CHUNKS):
        player.submit(BASE_US + i * 10_000, _sine_chunk(i))
    for shift_ms in shifts_ms:
        player.resync(shift_ms * 1000)
    net_us = sum(shifts_ms) * 1000
    for i in range(JUNCTION_CHUNKS, 60):
        player.submit(BASE_US + i * 10_000 + net_us, _sine_chunk(i))
    _wait_until(lambda: len(_left_output(handed)) >= 50 * CHUNK_FRAMES)
    out = _left_output(handed)
    natural = max(abs(a - b) for a, b in zip(out[1:2000], out[: 2000 - 1], strict=True))
    worst = max(abs(a - b) for a, b in zip(out[1:], out[:-1], strict=True))
    assert worst <= natural + 2 * 10_000 / 240 + 2
    for (pts, data), (next_pts, _) in pairwise(handed):
        assert abs(pts + len(data) // PCM.frame_bytes() * 1e9 / RATE - next_pts) < 1e9 / RATE
    player.stop()


@requires_gst
def test_two_shifts_before_the_audio_that_follows_still_land_on_schedule():
    """+100 then -40 ms: the 100 ms of silence is already handed on, so the
    audio that the net +60 ms would have put inside it is skipped; the rest
    plays exactly on the net-shifted schedule."""
    handed: list[tuple[int, bytes]] = []
    player = _player(handed)
    player.start(PCM)
    for i in range(JUNCTION_CHUNKS):
        player.submit(BASE_US + i * 10_000, _click_chunk(i))
    player.resync(100_000)
    player.resync(-40_000)
    for i in range(JUNCTION_CHUNKS, TOTAL_CHUNKS):
        player.submit(BASE_US + i * 10_000 + 60_000, _click_chunk(i))
    _wait_until(lambda: sum(len(d) for _, d in handed) // PCM.frame_bytes() == TOTAL_CHUNKS * CHUNK_FRAMES + 2880)

    heard = _clicks_heard(handed)
    junction = JUNCTION_CHUNKS * CHUNK_FRAMES
    for n, at_ns in heard.items():
        content = n * CLICK_EVERY + CLICK_AT
        expected = _content_time_ns(content) + (60_000_000 if content >= junction else 0)
        assert abs(at_ns - expected) < 1e9 / RATE
    assert len(heard) == TOTAL_CHUNKS * CHUNK_FRAMES // CLICK_EVERY
    player.stop()
