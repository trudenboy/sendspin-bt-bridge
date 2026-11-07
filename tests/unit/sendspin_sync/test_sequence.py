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


def test_alignment_holds_when_the_last_member_was_not_heard():
    """A silent last member: the slot pattern shifted one member earlier explains the clicks just as
    well, and it used to win, handing a's phase to b and b's to c."""
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012}, silent=frozenset({"c"}))
    offset, m = _measure(pcm, slots)
    assert offset == pytest.approx(1.7, abs=0.6)
    assert (m["a"].status, m["b"].status, m["c"].status) == ("ok", "ok", "not_heard")
    assert planner.unwrap_lag_ms(m["b"].phase_ms, m["a"].phase_ms) == pytest.approx(30.0, abs=1.0)


def test_alignment_holds_when_the_last_two_members_were_not_heard():
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012}, silent=frozenset({"b", "c"}))
    offset, m = _measure(pcm, slots)
    assert offset == pytest.approx(1.7, abs=0.6)
    assert (m["a"].status, m["b"].status, m["c"].status) == ("ok", "not_heard", "not_heard")


def test_an_alignment_the_clicks_cannot_decide_is_refused_not_guessed():
    """Long silence on both sides and a silent end member: two offsets one member apart fit equally,
    and nothing in the clicks tells them apart. A guess would measure the wrong speakers."""
    pcm, slots = sequence_recording(
        {"a": 0.0, "b": 0.030, "c": 0.012}, silent=frozenset({"c"}), started_at_s=14.0, tail_s=14.0
    )
    assert seq.align(seq.click_times(pcm, 16000), slots, len(pcm) / 32000) is None


def test_an_expected_offset_decides_an_otherwise_ambiguous_alignment():
    """A recorder the bridge started knows where the sequence began; that settles it."""
    pcm, slots = sequence_recording(
        {"a": 0.0, "b": 0.030, "c": 0.012}, silent=frozenset({"c"}), started_at_s=14.0, tail_s=14.0
    )
    offset = seq.align(seq.click_times(pcm, 16000), slots, len(pcm) / 32000, expected=(13.0, 15.0))
    assert offset == pytest.approx(14.0, abs=0.6)


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


def test_click_detection_stays_small_in_memory_for_the_longest_recording_allowed():
    """Review I3: the worst case (16 members from the listening position, about 303 s at 16 kHz) must
    not build per-sample float lists (+376 MB measured before)."""
    import random
    import struct
    import tracemalloc

    rate = 16000
    seconds = seq.max_duration_s(seq.MAX_MEMBERS, "position")
    n = int(seconds * rate)
    rnd = random.Random(2)
    pcm = struct.pack(f"<{n}h", *(rnd.randint(-300, 300) for _ in range(n)))
    tracemalloc.start()
    try:
        seq.click_times(pcm, rate)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"


# -- a noisy room: no click stands out on its own -------------------------------------------------


@pytest.mark.parametrize("noise", [0.05, 0.1, 0.2])
def test_a_noisy_room_is_aligned_from_the_chirps_envelope(noise):
    """The estimator measures these rooms; alignment needed a click 15 dB above them and gave up, so
    the phone and bridge-input paths reported every speaker not heard."""
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012}, noise=noise)
    duration = len(pcm) / 32000
    assert seq.align(seq.click_times(pcm, 16000), slots, duration) is None  # the old path: nothing

    placed = seq.cut(pcm, 16000, slots, "device")

    assert placed is not None and placed[0] == pytest.approx(1.7, abs=1.0)
    m = {r.member: estimator.estimate(r) for r in placed[1]}
    assert all(x.status == "ok" for x in m.values())
    assert planner.unwrap_lag_ms(m["b"].phase_ms, m["a"].phase_ms) == pytest.approx(30.0, abs=2.0)


def test_a_noisy_room_with_a_silent_last_member_still_measures_the_right_speakers():
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012}, noise=0.2, silent=frozenset({"c"}))

    placed = seq.cut(pcm, 16000, slots, "device")

    assert placed is not None and placed[0] == pytest.approx(1.7, abs=1.0)
    m = {r.member: estimator.estimate(r) for r in placed[1]}
    assert (m["a"].status, m["b"].status, m["c"].status) == ("ok", "ok", "not_heard")


def test_noise_alone_is_never_aligned():
    import random
    import struct

    _pcm, slots = sequence_recording({"a": 0.0, "b": 0.0})
    for seed in range(4):
        rnd = random.Random(seed)
        n = 16000 * 40
        pcm = struct.pack(f"<{n}h", *(int(rnd.gauss(0.0, 0.1) * 32767) for _ in range(n)))
        assert seq.cut(pcm, 16000, slots, "device") is None


def test_a_noisy_alignment_the_clicks_cannot_decide_is_refused():
    pcm, slots = sequence_recording(
        {"a": 0.0, "b": 0.030, "c": 0.012}, silent=frozenset({"c"}), started_at_s=14.0, tail_s=14.0, noise=0.1
    )
    assert seq.cut(pcm, 16000, slots, "device") is None


def test_clicks_before_the_sequence_began_do_not_move_a_noisy_alignment():
    """A recorder started before the sequence hears every member before the lead-in mutes them."""
    import struct

    from tests.support.sync_synth import synth

    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012}, noise=0.2)
    n = len(pcm) // 2
    x = list(struct.unpack(f"<{n}h", pcm))
    before = synth("all", lag_s=0.0, start_time_s=20.0 - 1.7, duration_s=1.6, noise=0.0, gain=0.3)
    y = struct.unpack(f"<{len(before.pcm) // 2}h", before.pcm)
    for i, v in enumerate(y):
        x[i] = max(-32767, min(32767, x[i] + v))
    pcm = struct.pack(f"<{n}h", *x)

    placed = seq.cut(pcm, 16000, slots, "device")

    assert placed is not None and placed[0] == pytest.approx(1.7, abs=1.0)
    assert all(estimator.estimate(r).status == "ok" for r in placed[1])


# -- the recorder's clock -------------------------------------------------------------------------


@pytest.mark.parametrize("ppm", [-150.0, 100.0])
def test_a_recorder_clock_that_runs_fast_or_slow_does_not_skew_later_members(ppm):
    """Members play 12 s apart: 100 ppm off is 1.2 ms for b and 2.4 ms for c, against a 3 ms
    target; the click period (exactly 2 s) measures the recorder's rate and takes it out."""
    from sendspin_sync import session as session_mod

    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012}, drift_ppm=ppm)
    placed = seq.cut(pcm, 16000, slots, "device")
    assert placed is not None

    m = session_mod.measure_windows(placed[1])

    assert planner.unwrap_lag_ms(m["b"].phase_ms, m["a"].phase_ms) == pytest.approx(30.0, abs=0.5)
    assert planner.unwrap_lag_ms(m["c"].phase_ms, m["a"].phase_ms) == pytest.approx(12.0, abs=0.5)


def test_the_uncorrected_estimate_is_skewed_by_the_recorder_clock():
    """Pins why the correction exists: without it, c reads well over a millisecond off."""
    pcm, slots = sequence_recording({"a": 0.0, "b": 0.030, "c": 0.012}, drift_ppm=100.0)
    placed = seq.cut(pcm, 16000, slots, "device")
    assert placed is not None
    m = {r.member: estimator.estimate(r) for r in placed[1]}
    assert abs(planner.unwrap_lag_ms(m["c"].phase_ms, m["a"].phase_ms) - 12.0) > 1.0


def test_a_speakers_own_drift_is_not_taken_for_the_recorders_clock():
    steady = [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)]
    moving = [(12.0, 30.0), (14.0, 30.6), (16.0, 31.2)]
    assert seq.rate_skew([steady, moving]) == 0.0
    assert seq.rate_skew([steady, steady, moving]) == 0.0


def test_recorder_drift_across_phase_wrap_is_measured():
    first = [(0.0, 9999.9), (2.0, 0.1), (4.0, 0.3)]
    second = [(12.0, 29.0), (14.0, 29.2), (16.0, 29.4)]
    assert seq.rate_skew([first, second]) == pytest.approx(0.1, abs=0.001)
