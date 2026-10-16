"""One recording for every member (the phone capture page): the bridge plays the sequence, the phone
records it once, the upload is cut into one stretch per member on one clock."""

import pytest

from sendspin_sync import planner
from sendspin_sync import sequence as seq
from sendspin_sync.ports import ControlError
from sendspin_sync.session import SyncSession
from tests.support.sync_fakes import FakeControl, FakeHost
from tests.support.sync_sequence import sequence_recording


class Clock:
    def __init__(self, t: float = 100.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def ready(**kw):
    ctl = FakeControl()
    clock = Clock()
    published = []
    s = SyncSession(
        session_id="s1",
        group_id="g",
        mode="semi",
        point="device",
        control=ctl,
        host=FakeHost(),
        clock=clock,
        on_change=lambda sess: published.append(sess.to_dict()),
    )
    s.start()
    s.stop_group()
    s.start_clicker()
    ctl.calls.clear()
    return s, ctl, clock, published


def run(s, clock, latency=0.25):
    """Run the sequence with a sleep that moves the clock (each solo is heard ``latency`` later)."""

    def sleep(dt):
        clock.t += dt

    s.run_sequence(sleep=sleep)


def test_the_sequence_mutes_everyone_then_plays_each_member_alone_with_gaps():
    s, ctl, clock, published = ready()
    s.start_sequence(["a", "b"])
    assert s.to_dict()["sequence"]["phase"] == "starting"
    run(s, clock)
    mutes = [c for c in ctl.calls if c[0] == "mute"]
    assert mutes == [
        ("mute", "a", True),
        ("mute", "b", True),
        ("mute", "cast", True),  # lead-in: everyone muted
        ("mute", "a", False),  # a alone
        ("mute", "a", True),  # gap
        ("mute", "b", False),  # b alone
        ("mute", "b", True),  # gap
        ("mute", "a", False),
        ("mute", "b", False),
        ("mute", "cast", False),  # done: everyone back
    ]
    d = s.to_dict()["sequence"]
    assert d["phase"] == "done" and d["members"] == ["a", "b"] and d["error"] is None
    phases = [p["sequence"]["phase"] for p in published if p["sequence"]]
    changes = [x for i, x in enumerate(phases) if i == 0 or x != phases[i - 1]]
    assert changes == ["starting", "lead", "solo", "gap", "solo", "gap", "done"]
    soloed = [p["sequence"]["member"] for p in published if p["sequence"] and p["sequence"]["phase"] == "solo"]
    assert sorted(set(soloed)) == ["a", "b"]


def test_an_uploaded_recording_of_the_sequence_measures_every_member_on_one_clock():
    s, _ctl, clock, _ = ready()
    s.start_sequence(["a", "b"])
    run(s, clock)
    pcm, _slots = sequence_recording({"a": 0.0, "b": 0.030})
    result = s.record_sequence(pcm, 16000)
    assert {k: v.status for k, v in result.items()} == {"a": "ok", "b": "ok"}
    assert planner.unwrap_lag_ms(s.measurements["b"].phase_ms, s.measurements["a"].phase_ms) == pytest.approx(30, abs=1)
    assert s.to_dict()["sequence"]["uploaded"] is True
    p = s.propose()
    assert {r.member: r.after_ms for r in p.rows} == {"a": 0, "b": 30}


def test_an_upload_before_the_sequence_ended_or_without_one_is_refused():
    s, _ctl, _clock, _ = ready()
    pcm, _ = sequence_recording({"a": 0.0, "b": 0.0})
    with pytest.raises(ControlError) as exc:
        s.record_sequence(pcm, 16000)
    assert exc.value.code == "no_sequence"
    s.start_sequence(["a", "b"])
    with pytest.raises(ControlError) as exc:
        s.record_sequence(pcm, 16000)
    assert exc.value.code == "sequence_running"
    with pytest.raises(ControlError) as exc:
        s.start_sequence(["a"])
    assert exc.value.code == "sequence_running"


def test_a_sequence_needs_the_clicks_and_adjustable_members():
    s, ctl, _clock, _ = ready()
    with pytest.raises(ControlError) as exc:
        s.start_sequence(["cast"])
    assert exc.value.code == "not_adjustable"
    with pytest.raises(ControlError) as exc:
        s.start_sequence([])
    assert exc.value.code == "member_gone"
    ctl.clicker_ends()
    with pytest.raises(ControlError) as exc:
        s.start_sequence(["a"])
    assert exc.value.code == "clicker_stopped"


def test_clicks_started_again_after_the_sequence_make_its_recording_useless():
    s, _ctl, clock, _ = ready()
    s.start_sequence(["a", "b"])
    run(s, clock)
    s.start_clicker()
    pcm, _ = sequence_recording({"a": 0.0, "b": 0.03})
    with pytest.raises(ControlError) as exc:
        s.record_sequence(pcm, 16000)
    assert exc.value.code == "session_state"


def test_a_cancel_mid_sequence_stops_it_and_the_restore_unmutes():
    s, ctl, clock, _ = ready()
    s.start_sequence(["a", "b"])

    def sleep(dt):
        clock.t += dt
        if s.to_dict()["sequence"]["phase"] == "solo":
            s.cancel()

    s.run_sequence(sleep=sleep)
    assert ("mute", "b", False) not in ctl.calls  # b never played alone
    assert s.restore_tick() is True and not any(ctl.muted_now.values())


def test_a_member_that_cannot_be_muted_fails_the_sequence_and_names_it():
    s, ctl, clock, _ = ready()
    ctl.no_mute = {"cast"}
    s.start_sequence(["a", "b"])
    run(s, clock)
    d = s.to_dict()["sequence"]
    assert d["phase"] == "failed" and d["error"] == "sync_mute_unsupported" and d["error_member"] == "cast"
    assert not any(ctl.muted_now.values())


def test_a_recording_without_clicks_measures_nobody():
    s, _ctl, clock, _ = ready()
    s.start_sequence(["a", "b"])
    run(s, clock)
    pcm, _ = sequence_recording({"a": 0.0, "b": 0.0}, silent=frozenset({"a", "b"}))
    result = s.record_sequence(pcm, 16000)
    assert {m.status for m in result.values()} == {"not_heard"}


def test_sequence_timing_follows_the_measuring_point():
    assert seq.solo_seconds("position") > seq.solo_seconds("device")


def test_a_recording_longer_than_the_sequence_allows_or_at_an_odd_rate_is_refused_before_any_work():
    s, _ctl, clock, _ = ready()
    s.start_sequence(["a", "b"])
    run(s, clock)
    too_long = bytes(2 * 16000 * int(seq.max_duration_s(2, "device") + 5))
    for pcm, rate in ((too_long, 16000), (bytes(32000), 1_000_000), (bytes(32000), 100)):
        with pytest.raises(ControlError) as exc:
            s.record_sequence(pcm, rate)
        assert exc.value.code == "recording_invalid"


def test_a_sequence_of_too_many_members_is_refused():
    s, ctl, _clock, _ = ready()
    from sendspin_sync.models import Member

    ctl.member_list = [Member(f"m{i}", f"M{i}", True, "sendspin", delay_ms=0) for i in range(seq.MAX_MEMBERS + 1)]
    s.refresh_members()
    s.members = list(ctl.member_list)
    with pytest.raises(ControlError) as exc:
        s.start_sequence([m.ma_player_id for m in ctl.member_list])
    assert exc.value.code == "recording_invalid"


# -- review fix round: a longer lead-in for the phone's recorder; one clock per round -----------------


def test_the_lead_in_can_be_longer_for_a_recorder_started_by_hand_and_is_bounded():
    s, _ctl, clock, _ = ready()
    s.start_sequence(["a", "b"], lead_s=10.0)
    assert s.to_dict()["sequence"]["lead_s"] == 10.0
    t0 = clock.t
    waits = []

    def sleep(dt):
        waits.append(dt)
        clock.t += dt

    s.run_sequence(sleep=sleep)
    assert waits[0] == 10.0 and clock.t > t0
    s2, _c2, _k2, _ = ready()
    s2.start_sequence(["a"], lead_s=999)
    assert s2.to_dict()["sequence"]["lead_s"] == seq.MAX_LEAD_S


def test_a_sequence_recording_replaces_measurements_from_another_clock():
    s, _ctl, clock, _ = ready()
    from tests.support.sync_synth import synth

    s.record(synth("b", lag_s=0.5))  # a microphone recording: another clock
    s.start_sequence(["a"])
    run(s, clock)
    pcm, _ = sequence_recording({"a": 0.0})
    s.record_sequence(pcm, 16000)
    assert "b" not in s.measurements  # its phase was on the microphone's clock, not this file's
