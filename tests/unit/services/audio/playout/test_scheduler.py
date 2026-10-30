import array

import pytest

from sendspin_bridge.services.audio.playout.scheduler import InputFormat, PlayoutScheduler
from sendspin_bridge.services.audio.playout.timeline import ScheduleMap

F48 = InputFormat(sample_rate=48000)


def tone(frames, value=1000):
    return array.array("h", [value, value] * frames).tobytes()


def sched():
    m = ScheduleMap()
    s = PlayoutScheduler(schedule=m)
    s.set_format(F48)
    return s, m


def frames_of(block):
    return len(block.pcm) // 4


def test_silence_when_nothing_is_queued():
    s, m = sched()
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert frames_of(b) == 480 and not b.program and set(b.pcm) == {0}
    assert m.kind_at(0) == "silence"


def test_first_chunk_is_placed_to_the_frame():
    s, m = sched()
    s.submit(1_005_000, tone(960))  # due 5 ms after the write head's audible time
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    pcm = array.array("h", b.pcm)
    first = next(i for i in range(0, len(pcm), 2) if pcm[i] != 0) // 2
    assert first == 240  # 5 ms of silence at 48 kHz
    assert m.at(b.x0_ns + 5_000_000) == pytest.approx(1_005_000.0)


def test_late_head_is_dropped_and_reported():
    s, _ = sched()
    s.submit(997_000, tone(960))  # 3 ms already due
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert b.late_drop_us == pytest.approx(3000.0, abs=21)
    assert b.program and frames_of(b) == 480


def test_contiguous_chunks_with_a_small_gap_are_not_replaced():
    s, _ = sched()
    s.submit(1_000_000, tone(480))
    s.submit(1_010_500, tone(480))  # 0.5 ms gap: left to the loop
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b2 = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    assert s.last_placement_reason is None or s.last_placement_reason == "start"
    assert set(array.array("h", b2.pcm)) == {1000}


def test_clear_drops_queued_audio_and_returns_the_write_head():
    s, _ = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    x_end = s.clear()
    assert x_end == s.x_w_ns and not s.queued
    assert not s.render(audible_us=1_010_000.0, now_us=10_000.0).program


def test_shift_moves_queued_audio_exactly():
    s, _ = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)  # first 10 ms written
    assert s.shift(+30_000)  # smaller delay: audio 30 ms later
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    pcm = array.array("h", b.pcm)
    assert pcm[0] == 0  # silence inserted until the shifted frame
    assert s.last_placement_reason == "delay"


def test_shift_drops_audio_whose_new_time_has_passed():
    s, _ = sched()
    s.submit(1_000_000, tone(960))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.shift(-50_000)  # earlier by more than what is left
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    assert not b.program


def test_44k1_input_advances_output_time_exactly():
    m = ScheduleMap()
    s = PlayoutScheduler(schedule=m)
    s.set_format(InputFormat(sample_rate=44100))
    xs = [s.render(audible_us=0.0, now_us=0.0).duration_ns for _ in range(100)]
    assert sum(xs) == 1_000_000_000


# --- rules stated in the brief's prose -------------------------------------------------


def first_program_frame(block):
    pcm = array.array("h", block.pcm)
    return next(i for i in range(0, len(pcm), 2) if pcm[i] != 0) // 2


def test_gap_over_2_ms_places_the_next_chunk_exactly():
    s, m = sched()
    s.submit(1_000_000, tone(240))
    s.submit(1_008_000, tone(480))  # 3 ms gap after the first chunk's 5 ms
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert s.last_placement_reason == "gap"
    pcm = array.array("h", b.pcm)
    assert set(pcm[: 240 * 2]) == {1000}
    assert set(pcm[240 * 2 : 384 * 2]) == {0}  # 3 ms = 144 frames of silence
    assert pcm[384 * 2] == 1000
    assert m.kind_at(b.x0_ns + 6_000_000) == "silence"
    assert m.at(b.x0_ns + 8_000_000) == pytest.approx(1_008_000.0)


def test_small_gap_is_recorded_in_the_schedule_for_the_loop():
    s, m = sched()
    s.submit(1_000_000, tone(240))
    s.submit(1_005_500, tone(240))  # 0.5 ms gap: written contiguously, scheduled as sent
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert set(array.array("h", b.pcm)) == {1000}
    assert m.at(b.x0_ns + 5_000_000) == pytest.approx(1_005_500.0)


def test_silence_after_program_continues_the_schedule_linearly():
    s, m = sched()
    s.submit(1_000_000, tone(240))
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert m.kind_at(b.x0_ns + 7_000_000) == "silence"
    assert m.at(b.x0_ns + 7_000_000) == pytest.approx(1_007_000.0)


def test_placement_never_drops_after_inserting_silence():
    s, _ = sched()
    s.submit(1_030_000, tone(4800))  # placement decides 30 ms of silence
    s.render(audible_us=1_000_000.0, now_us=0.0)
    late = s.render(audible_us=1_050_000.0, now_us=10_000.0)  # prediction moved by +40 ms
    assert late.late_drop_us == 0.0 and not late.program
    s.render(audible_us=1_060_000.0, now_us=20_000.0)
    b = s.render(audible_us=1_070_000.0, now_us=30_000.0)
    assert b.late_drop_us == 0.0 and first_program_frame(b) == 0  # 1440 frames = 3 blocks


def test_placement_never_inserts_silence_after_dropping_the_head():
    s, _ = sched()
    s.submit(997_000, tone(960))
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert set(array.array("h", b.pcm)) == {1000}


def test_chunk_entirely_due_is_dropped_and_the_next_one_placed():
    s, m = sched()
    s.submit(980_000, tone(480))  # 980..990 ms, all due before 1000 ms
    s.submit(1_002_000, tone(480))
    b = s.render(audible_us=1_000_000.0, now_us=0.0)
    assert b.late_drop_us == pytest.approx(10_000.0)
    assert first_program_frame(b) == 96  # 2 ms of silence
    assert m.at(b.x0_ns + 2_000_000) == pytest.approx(1_002_000.0)


def test_request_placement_realigns_the_playing_chunk():
    s, _ = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.request_placement("stall")
    b = s.render(audible_us=1_015_000.0, now_us=10_000.0)  # write head is now 5 ms late
    assert s.last_placement_reason == "stall"
    assert b.late_drop_us == pytest.approx(5000.0)
    assert b.program and set(array.array("h", b.pcm)) == {1000}


def test_input_running_dry_places_the_next_chunk():
    s, _ = sched()
    s.submit(1_000_000, tone(240))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.submit(1_005_000, tone(480))  # contiguous in time, but silence was already written
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    assert s.last_placement_reason == "starved"
    assert b.late_drop_us == pytest.approx(5000.0)


def test_shift_with_nothing_queued_returns_false():
    s, _ = sched()
    assert s.shift(10_000) is False


def test_format_change_starts_a_new_exact_epoch():
    m = ScheduleMap()
    s = PlayoutScheduler(schedule=m)
    s.set_format(F48)
    s.render(audible_us=0.0, now_us=0.0)
    x0 = s.x_w_ns
    s.set_format(InputFormat(sample_rate=44100))
    for _ in range(100):
        s.render(audible_us=0.0, now_us=0.0)
    assert s.x_w_ns - x0 == 1_000_000_000


def test_fill_supplies_the_silence_signal():
    m = ScheduleMap()
    s = PlayoutScheduler(schedule=m, fill=lambda fmt, n: b"\x01\x00" * fmt.channels * n)
    s.set_format(F48)
    b = s.render(audible_us=0.0, now_us=0.0)
    assert set(array.array("h", b.pcm)) == {1} and not b.program


def test_a_positive_rate_drops_frames_at_the_write_head():
    """Stage B2 (#546): 400 ppm at 48 kHz drops one frame every ~52 ms, reported as a rate frame.

    A frame is 20 us of audio: the discontinuity is inaudible, unlike a 20 ms jump.
    """
    s, m = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    # 10 ms blocks at 48 kHz: 400 ppm owes one frame every ~5 blocks.
    blocks = [s.render(audible_us=1_000_000.0, now_us=0.0, rate=400e-6) for _ in range(8)]
    rate_frames = sum(b.rate_dropped for b in blocks)
    assert rate_frames >= 1
    assert all(b.rate_inserted == 0 for b in blocks)


def test_a_negative_rate_inserts_frames_and_reports_them_as_rate_frames():
    s, _m = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    blocks = [s.render(audible_us=1_000_000.0, now_us=0.0, rate=-400e-6) for _ in range(8)]
    assert sum(b.rate_inserted for b in blocks) >= 1
    assert all(b.rate_dropped == 0 for b in blocks)


def test_shift_later_places_the_frame_exactly():
    s, m = sched()
    s.submit(1_000_000, tone(4800))  # 100 ms chunk
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.shift(+30_000)
    blocks = [s.render(audible_us=1_010_000.0 + 10_000.0 * k, now_us=0.0) for k in range(4)]
    assert not any(b.program for b in blocks[:3])
    assert first_program_frame(blocks[3]) == 0
    assert m.at(blocks[3].x0_ns) == pytest.approx(1_040_000.0)


def test_shift_earlier_mid_chunk_drops_the_due_head():
    s, m = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.shift(-5_000)
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0)
    assert b.late_drop_us == pytest.approx(5000.0)
    assert set(array.array("h", b.pcm)) == {1000}
    assert m.at(b.x0_ns) == pytest.approx(1_010_000.0)


def test_format_change_with_an_empty_queue_replaces_the_next_chunk():
    s, _ = sched()
    s.submit(1_000_000, tone(480))
    s.render(audible_us=1_000_000.0, now_us=0.0)  # exactly one block, nothing left
    s.set_format(InputFormat(sample_rate=44100))
    s.submit(1_010_000, tone(882))  # contiguous in time
    b = s.render(audible_us=1_012_000.0, now_us=10_000.0)
    assert s.last_placement_reason == "format"
    assert b.late_drop_us == pytest.approx(2000.0, abs=23)


# --- jumps (Ruling R8: measure mode issues steady jumps in Stage 1) -----------------------


def ramp(frames, start):
    return array.array("h", [v for j in range(frames) for v in (start + j, start + j)]).tobytes()


def first_value(block):
    return array.array("h", block.pcm)[0]


def test_positive_jump_drops_program_frames_at_the_write_head():
    s, m = sched()
    s.submit(1_000_000, ramp(4800, 1))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=5000.0)
    assert (b.dropped, b.inserted) == (240, 0)
    assert first_value(b) == 1 + 480 + 240
    assert frames_of(b) == 480 and b.program
    assert m.at(b.x0_ns) == pytest.approx(1_015_000.0)


def test_positive_jump_pops_chunks_consumed_whole():
    s, m = sched()
    s.submit(1_000_000, ramp(480, 1))
    s.submit(1_010_000, ramp(480, 1001))
    s.submit(1_020_000, ramp(480, 2001))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=15_000.0)
    assert b.dropped == 720
    assert first_value(b) == 2001 + 240
    assert m.at(b.x0_ns) == pytest.approx(1_025_000.0)
    assert m.kind_at(b.x0_ns) == "program"


def test_positive_jump_larger_than_the_queue_drops_what_is_queued():
    s, _ = sched()
    s.submit(1_000_000, tone(960))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=50_000.0)
    assert b.dropped == 480 and not b.program and not s.queued
    assert frames_of(b) == 480


def test_positive_jump_consumes_owed_placement_silence_first():
    s, m = sched()
    s.submit(1_030_000, tone(4800))  # placement owes 1440 frames of silence
    s.render(audible_us=1_000_000.0, now_us=0.0)  # 960 still owed
    b2 = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=5000.0)  # 720 owed
    assert b2.dropped == 240 and not b2.program
    b3 = s.render(audible_us=1_020_000.0, now_us=20_000.0)
    assert first_program_frame(b3) == 240 and b3.dropped == 0
    assert m.at(b3.x0_ns + 5_000_000) == pytest.approx(1_030_000.0)


def test_positive_jump_beyond_owed_silence_drops_program_too():
    s, m = sched()
    s.submit(1_030_000, ramp(4800, 1))
    s.render(audible_us=1_000_000.0, now_us=0.0)  # 960 owed
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=25_000.0)  # 1200 frames
    assert b.dropped == 1200
    assert first_value(b) == 1 + 240
    assert m.at(b.x0_ns) == pytest.approx(1_035_000.0)


def test_negative_jump_inserts_silence_and_keeps_the_program_schedule():
    s, m = sched()
    s.submit(1_000_000, ramp(4800, 1))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=-5000.0)
    assert (b.dropped, b.inserted) == (0, 240)
    pcm = array.array("h", b.pcm)
    assert set(pcm[: 240 * 2]) == {0} and pcm[240 * 2] == 1 + 480
    assert m.kind_at(b.x0_ns + 1_000_000) == "silence"
    assert m.at(b.x0_ns + 1_000_000) == pytest.approx(1_011_000.0)  # linear continuation
    assert m.kind_at(b.x0_ns + 5_000_000) == "program"
    assert m.at(b.x0_ns + 5_000_000) == pytest.approx(1_010_000.0)  # the chunk's own time


def test_negative_jump_longer_than_a_block_continues_into_the_next():
    s, m = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b2 = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=-15_000.0)
    assert b2.inserted == 480 and not b2.program and frames_of(b2) == 480
    b3 = s.render(audible_us=1_020_000.0, now_us=20_000.0)
    assert b3.inserted == 240 and first_program_frame(b3) == 240
    assert m.at(b3.x0_ns + 5_000_000) == pytest.approx(1_010_000.0)


def test_jump_with_an_empty_queue():
    s, m = sched()
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=5000.0)
    assert b.dropped == 0 and not b.program
    b = s.render(audible_us=1_020_000.0, now_us=20_000.0, jump_us=-5000.0)
    assert b.inserted == 240 and not b.program and frames_of(b) == 480
    assert m.at(b.x0_ns + 5_000_000) == pytest.approx(1_025_000.0)


def test_zero_jump_is_no_jump():
    s, _ = sched()
    s.submit(1_000_000, ramp(4800, 1))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    b = s.render(audible_us=1_010_000.0, now_us=10_000.0, jump_us=0.0)
    assert (b.dropped, b.inserted) == (0, 0) and first_value(b) == 481


# --- engine support (Task 1.11) ---------------------------------------------------------


def test_placement_silence_is_scheduled_with_the_placed_frame():
    # The silence a placement owes leads straight into the placed frame, so s(x_w) is
    # already right while the silence is still being written (the engine's e_w uses it).
    s, m = sched()
    s.submit(1_000_000, tone(480))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.submit(1_200_000, tone(480))  # gap: the placement owes silence
    s.render(audible_us=1_050_000.0, now_us=10_000.0)  # the port prediction moved by +40 ms
    assert m.kind_at(s.x_w_ns - 1) == "silence"
    assert m.at(s.x_w_ns) == pytest.approx(1_060_000.0)


def test_placement_pending_is_visible():
    s, _ = sched()
    assert s.placement_pending  # the start placement
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    assert not s.placement_pending
    s.request_placement("stall")
    assert s.placement_pending


def test_reanchor_moves_the_silence_schedule_to_the_write_head():
    s, m = sched()
    s.render(audible_us=1_000_000.0, now_us=0.0)
    assert s.reanchor(2_000_000.0)
    assert m.at(s.x_w_ns) == pytest.approx(2_000_000.0)
    b = s.render(audible_us=0.0, now_us=10_000.0)
    assert m.at(b.x0_ns + 5_000_000) == pytest.approx(2_005_000.0)
    assert s.placement_count == 0  # program placements are counted, a silence re-anchor is not


def test_reanchor_refuses_while_program_is_queued():
    s, m = sched()
    s.submit(1_000_000, tone(4800))
    s.render(audible_us=1_000_000.0, now_us=0.0)
    before = m.at(s.x_w_ns)
    assert not s.reanchor(2_000_000.0)
    assert m.at(s.x_w_ns) == before


def test_reanchor_turns_a_pending_reason_into_starved():
    s, _ = sched()
    s.render(audible_us=1_000_000.0, now_us=0.0)
    s.request_placement("stall")
    assert s.reanchor(1_010_000.0)
    s.submit(1_020_000, tone(480))
    s.render(audible_us=1_010_000.0, now_us=10_000.0)
    assert s.last_placement_reason == "starved"
