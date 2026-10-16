import pytest

from sendspin_sync import estimator, planner
from sendspin_sync import sequence as seq
from tests.support.sync_sequence import sequence_recording


def _measure(pcm, slots, sr=16000):
    offset = seq.align(seq.click_times(pcm, sr), slots, len(pcm) / 2 / sr)
    assert offset is not None
    recs = seq.windows(pcm, sr, slots, offset)
    return offset, {r.member: estimator.estimate(r) for r in recs}


def test_one_recording_of_a_sequence_measures_every_member_on_one_clock():
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012})
    offset, m = _measure(pcm, slots)
    assert offset == pytest.approx(1.7, abs=0.6)
    assert all(x.status == "ok" for x in m.values())
    lag = {k: planner.unwrap_lag_ms(v.phase_ms, m["a"].phase_ms) for k, v in m.items()}
    assert lag["b"] == pytest.approx(30.0, abs=1.0) and lag["c"] == pytest.approx(12.0, abs=1.0)


def test_alignment_holds_when_the_first_member_was_not_heard():
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.0}, silent=frozenset({"a"}), started_at_s=4.0)
    offset, m = _measure(pcm, slots)
    assert offset == pytest.approx(4.0, abs=0.6)
    assert m["a"].status == "not_heard"
    lag = planner.unwrap_lag_ms(m["b"].phase_ms, m["c"].phase_ms)
    assert lag == pytest.approx(30.0, abs=1.0)


def test_a_recording_without_clicks_cannot_be_aligned():
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.0}, silent=frozenset({"a", "b"}))
    assert seq.align(seq.click_times(pcm, 16000), slots, len(pcm) / 32000) is None


def test_a_window_outside_the_file_is_empty_not_a_crash():
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.0})
    recs = seq.windows(pcm[: 2 * 16000 * 5], 16000, slots, 1.7)  # cut short: only the lead-in and part of "a"
    assert recs[-1].pcm == b"" and estimator.estimate(recs[-1]).reason == "invalid_recording"


def test_the_longest_recording_allowed_grows_with_the_members():
    assert seq.max_duration_s(4, "position") > seq.max_duration_s(2, "device") > 60


# -- hard limits against crafted recordings (security review, UX round) ----------------------------


def test_align_is_bounded_for_any_number_of_events():
    import time

    slots = [seq.Slot(member=f"m{i}", start_s=3 + i * 12, end_s=3 + i * 12 + 7) for i in range(seq.MAX_MEMBERS)]
    events = [i * 0.001 for i in range(200_000)]  # far more "clicks" than any sequence can make
    t0 = time.perf_counter()
    seq.align(events, slots, 600.0)
    assert time.perf_counter() - t0 < 3.0


def test_click_detection_keeps_a_plausible_number_of_events_in_a_burst_storm():
    import random
    import struct
    import time

    rate, seconds = 16000, 60
    rnd = random.Random(1)
    x = [0] * (rate * seconds)
    for start in range(0, len(x), int(0.035 * rate)):  # a band burst every 35 ms
        for i in range(start, min(len(x), start + 40)):
            x[i] = rnd.choice((-20000, 20000))
    pcm = struct.pack(f"<{len(x)}h", *x)
    t0 = time.perf_counter()
    events = seq.click_times(pcm, rate)
    assert len(events) <= seq.max_events(seconds)
    seq.align(events, [seq.Slot("a", 3, 10)], seconds)
    assert time.perf_counter() - t0 < 10.0


def test_a_sequence_has_a_hard_member_limit():
    assert seq.MAX_MEMBERS <= 16
    assert seq.max_duration_s(seq.MAX_MEMBERS, "position") < 400
