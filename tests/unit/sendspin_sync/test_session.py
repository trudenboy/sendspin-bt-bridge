import json

import pytest

from sendspin_sync.ports import ControlError
from sendspin_sync.session import SyncSession
from tests.support.sync_fakes import FakeControl, FakeHost
from tests.support.sync_synth import synth


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


URL = "http://bridge/api/v1/sync/signal/s1.wav"


def make(mode="semi", **kw):
    ctl = FakeControl()
    events = []
    s = SyncSession(
        session_id="s1",
        group_id="g",
        mode=mode,
        point="device",
        control=ctl,
        host=FakeHost(),
        clock=kw.pop("clock", None) or Clock(),
        on_change=lambda sess: events.append(sess.state),
    )
    return s, ctl, events


def started(mode="semi", **kw):
    """A started session whose group was stopped and whose clicker plays (the wizard's first steps)."""
    s, ctl, events = make(mode=mode, **kw)
    s.start()
    s.stop_group()
    s.start_clicker()
    return s, ctl, events


def test_semi_session_runs_measure_propose_apply_restore():
    s, ctl, _events = make()
    s.start()
    assert s.state == "measuring" and ctl.calls == []  # starting a session touches nothing
    assert [m.ma_player_id for m in s.members] == ["a", "b", "cast"]
    s.stop_group()
    s.start_clicker()
    assert ctl.calls == [("stop_group",), ("clicker", URL)]
    s.solo("a")
    m = s.record(synth("a", lag_s=0.0))
    assert m.status == "ok"
    s.solo("b")
    s.record(synth("b", lag_s=0.030))
    p = s.propose()
    assert s.state == "proposed" and {r.member: r.after_ms for r in p.rows} == {"a": 0, "b": 30}
    assert p.unverified == ("cast",)
    s.apply()
    assert ("set", "b", 30) in ctl.calls and ("set", "a", 0) not in ctl.calls  # unchanged rows are not written
    # the measured delays are applied and the session goes on in the manual editor, clicks still playing
    assert s.state == "tuning" and s.clicker_playing
    s.finish()
    assert s.state == "restoring"
    assert s.restore_tick() is True and s.state == "done"
    # every member muted for the solo is unmuted, then the clicker is stopped
    assert ctl.calls[-3:] == [("mute", "a", False), ("mute", "cast", False), ("stop_group",)]
    assert ctl.muted_now == {"a": False, "b": False, "cast": False}


def test_restore_retries_until_it_succeeds_and_cancel_goes_through_restoring():
    s, ctl, _ = started()
    s.solo("a")
    ctl.fail_unmute = 2
    s.cancel()
    assert s.state == "restoring"
    assert s.restore_tick() is False and s.restore_tick() is False and s.restore_tick() is True
    assert s.state == "cancelled" and ctl.muted_now == {"a": False, "b": False, "cast": False}
    assert ctl.calls[-1] == ("stop_group",)


def test_manual_mode_writes_live_and_resets():
    s, ctl, _ = make(mode="manual")
    s.start()
    assert s.state == "tuning" and not s.clicker_playing
    s.write_delay("b", 250)
    assert ctl.delays_now["b"] == 250
    with pytest.raises(ControlError) as exc:
        s.write_delay("cast", 10)
    assert exc.value.code == "not_adjustable"
    s.stop_group()
    s.start_clicker()
    s.ab("a", "b")
    assert ("mute", "b", True) in ctl.calls and s.soloed == "a"
    s.reset()
    assert ctl.delays_now["b"] == 0


def test_apply_failure_keeps_the_proposal_and_moves_nothing_else():
    s, ctl, _ = started()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()

    def boom(member, ms):
        raise ControlError("ma_forbidden")

    ctl.set_delay = boom
    with pytest.raises(ControlError):
        s.apply()
    assert s.state == "proposed" and s.proposal is not None


def test_inactivity_expiry_and_member_gone():
    clock = {"t": 0.0}
    s, _ctl, _ = started(clock=lambda: clock["t"])
    clock["t"] = 901.0
    assert s.expired()
    with pytest.raises(ControlError) as exc:
        s.record(synth("zz", lag_s=0.0))
    assert exc.value.code == "member_gone"


def test_to_dict_is_json_shaped():
    s, _, _ = started()
    s.record(synth("a", lag_s=0.0))
    d = json.loads(json.dumps(s.to_dict()))
    assert d["id"] == "s1" and d["state"] == "measuring" and d["measurements"]["a"]["status"] == "ok"
    assert "pcm" not in json.dumps(d)


# -- controller rulings (preflight A13/C6, A14/C7, A10) -------------------------------------------


def _measured_and_proposed(mode="semi"):
    s, ctl, events = started(mode=mode)
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    return s, ctl, events


def test_cancel_after_a_finish_keeps_the_outcome_done():
    s, _ctl, _ = _measured_and_proposed()
    s.apply()
    s.finish()
    assert s.state == "restoring"
    s.cancel()  # a DELETE or Retry while restoring is a no-op
    assert s.restore_tick() is True and s.state == "done"
    s.cancel()  # and after the restore too
    assert s.state == "done"


def test_operations_while_restoring_answer_restore_pending():
    s, _ctl, _ = started()
    s.cancel()
    with pytest.raises(ControlError) as exc:
        s.record(synth("a", lag_s=0.0))
    assert exc.value.code == "restore_pending"
    with pytest.raises(ControlError) as exc:
        s.propose()
    assert exc.value.code == "restore_pending"


def test_write_delay_clamps_to_the_allowed_range():
    s, ctl, _ = make(mode="manual")
    s.start()
    s.write_delay("b", 9000)
    assert ctl.delays_now["b"] == 5000
    s.write_delay("a", -20)
    assert ctl.delays_now["a"] == 0


# -- review fix round 1 --------------------------------------------------------------------------


def _delay_of(s, mid):
    return next(m.delay_ms for m in s.members if m.ma_player_id == mid)


SIDE_METHODS = [
    ("start_clicker", ()),
    ("stop_group", ()),
    ("set_volume", ("a", 30)),
    ("solo", ("a",)),
    ("ab", ("a", "b")),
    ("set_reference", ("a",)),
    ("write_delay", ("b", 10)),
    ("reset", ()),
]


@pytest.mark.parametrize(("name", "args"), SIDE_METHODS)
def test_side_operations_answer_restore_pending_while_restoring(name, args):
    s, _ctl, _ = make(mode="manual")
    s.start()
    s.cancel()
    with pytest.raises(ControlError) as exc:
        getattr(s, name)(*args)
    assert exc.value.code == "restore_pending"


@pytest.mark.parametrize(("name", "args"), SIDE_METHODS)
def test_side_operations_are_refused_once_the_session_ended(name, args):
    s, ctl, _ = started()
    s.cancel()
    assert s.restore_tick() is True and s.state == "cancelled"
    calls = list(ctl.calls)
    with pytest.raises(ControlError) as exc:
        getattr(s, name)(*args)
    assert exc.value.code == "session_state"
    assert ctl.calls == calls and not s.clicker_playing and s.soloed is None


def test_fail_detail_survives_restore_retries():
    s, ctl, _ = started()
    s.solo("a")
    s.fail("boom")
    ctl.fail_unmute = 1
    assert s.restore_tick() is False
    assert s.error == "boom" and s.restore_error == "ma_unavailable"
    assert s.to_dict()["restore_error"] == "ma_unavailable"
    assert s.restore_tick() is True
    assert s.state == "failed" and s.error == "boom" and s.restore_error is None
    assert s.to_dict()["error"] == "boom" and s.to_dict()["restore_error"] is None


def test_successful_retry_clears_the_transient_restore_error():
    s, ctl, _ = started()
    s.solo("b")
    s.cancel()
    ctl.fail_unmute = 1
    assert s.restore_tick() is False and s.restore_error == "ma_unavailable"
    assert s.restore_tick() is True
    assert s.state == "cancelled" and s.error is None and s.restore_error is None


def test_concurrent_apply_and_cancel_leave_a_consistent_outcome():
    import threading
    import time

    s, ctl, _ = started()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    entered = threading.Event()
    real_set = ctl.set_delay

    def slow_set(member, ms):
        entered.set()
        time.sleep(0.05)  # a cancel arriving now must wait for the apply to finish
        real_set(member, ms)

    ctl.set_delay = slow_set
    applier = threading.Thread(target=s.apply)
    applier.start()
    assert entered.wait(2.0)
    canceller = threading.Thread(target=s.cancel)
    canceller.start()
    canceller.join(2.0)  # with the lock this waits for the apply; without it, it lands mid-apply
    ticker = threading.Thread(target=s.restore_tick)
    ticker.start()
    ticker.join(2.0)
    applier.join(2.0)
    # apply finished first (the session tunes on), then the cancel ended it, the tick restored: one outcome
    assert s.state == "cancelled" and ctl.delays_now["b"] == 30 and ctl.calls[-1] == ("stop_group",)


def test_start_failure_leaves_the_session_created_and_touches_nothing():
    s, ctl, _ = make()
    real = ctl.playback

    def boom(group_id):
        raise ControlError("ma_unavailable")

    ctl.playback = boom
    with pytest.raises(ControlError):
        s.start()
    assert s.state == "created" and ctl.calls == []
    ctl.playback = real
    s.start()
    assert s.state == "measuring" and s.group_state == "playing" and not s.clicker_playing


def test_apply_clamps_operator_rows():
    s, ctl, _ = _measured_and_proposed()
    s.apply(rows={"b": 9000})
    assert ctl.delays_now["b"] == 5000 and _delay_of(s, "b") == 5000


def test_manual_writes_and_reset_keep_members_current():
    s, _ctl, _ = make(mode="manual")
    s.start()
    s.write_delay("b", 250)
    assert _delay_of(s, "b") == 250
    assert next(m for m in s.to_dict()["members"] if m["ma_player_id"] == "b")["delay_ms"] == 250
    s.reset()
    assert _delay_of(s, "b") == 0


def test_fail_after_the_session_ended_is_a_no_op():
    s, _ctl, _ = make()
    s.start()
    s.cancel()
    assert s.restore_tick() is True
    s.fail("late")
    assert s.state == "cancelled" and s.error is None


def test_propose_counts_as_activity():
    clock = {"t": 0.0}
    s, _ctl, _ = started(clock=lambda: clock["t"])
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    clock["t"] = 500.0
    s.propose()
    clock["t"] = 1000.0
    assert not s.expired()


# -- review fix round 2 --------------------------------------------------------------------------


def test_a_clicker_started_when_on_change_fails_is_still_stopped_by_the_restore():
    ctl = FakeControl()
    fail = {"on": False}

    def on_change(sess):
        if fail["on"] and sess.clicker_playing:  # the publish after play_media answered
            raise RuntimeError("listener broke")

    s = SyncSession(
        session_id="s1",
        group_id="g",
        mode="semi",
        point="device",
        control=ctl,
        host=FakeHost(),
        clock=Clock(),
        on_change=on_change,
    )
    s.start()
    s.stop_group()
    fail["on"] = True
    with pytest.raises(RuntimeError):
        s.start_clicker()
    assert ("clicker", URL) in ctl.calls
    # the click track is playing: the session must not pretend it never started
    assert s.clicker_playing and s.clicker_url == URL
    fail["on"] = False
    s.cancel()
    assert s.restore_tick() is True and ctl.calls[-1] == ("stop_group",)


def test_a_member_that_leaves_the_group_is_dropped_and_planning_continues_without_it():
    s, ctl, _ = started()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "b"]  # "b" leaves the group
    p = s.propose()
    assert [r.member for r in p.rows] == ["a"] and "b" not in s.measurements
    assert s.to_dict()["departed"] == ["b"] and "b" not in [m["ma_player_id"] for m in s.to_dict()["members"]]


def test_a_departed_member_is_refused_when_its_recording_arrives():
    s, ctl, _ = started()
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "b"]
    with pytest.raises(ControlError) as exc:
        s.record(synth("b", lag_s=0.0))
    assert exc.value.code == "member_gone" and s.to_dict()["departed"] == ["b"]


def test_membership_refresh_is_best_effort_when_music_assistant_is_down():
    s, ctl, _ = started()

    def down(group_id):
        raise ControlError("ma_unavailable")

    ctl.members = down
    s.record(synth("a", lag_s=0.0))
    assert [m.ma_player_id for m in s.members] == ["a", "b", "cast"] and s.to_dict()["departed"] == []


@pytest.mark.parametrize(
    ("rows", "code"),
    [({"b": 25, "cast": 10}, "not_adjustable"), ({"b": 25, "zz": 10}, "member_gone")],
)
def test_apply_validates_every_row_before_writing_any(rows, code):
    s, ctl, _ = _measured_and_proposed()
    proposal = s.proposal
    with pytest.raises(ControlError) as err:
        s.apply(rows)
    assert err.value.code == code
    assert not [c for c in ctl.calls if c[0] == "set"] and ctl.delays_now == {"a": 0, "b": 0}
    assert s.state == "proposed" and s.proposal == proposal
    assert _delay_of(s, "b") == 0


def test_a_write_failing_mid_apply_puts_back_what_this_apply_wrote():
    s, ctl, _ = _measured_and_proposed()
    real = ctl.set_delay

    def flaky(member, ms):
        if member.ma_player_id == "b":
            raise ControlError("ma_unavailable")
        real(member, ms)

    ctl.set_delay = flaky
    with pytest.raises(ControlError) as err:
        s.apply({"a": 10, "b": 20})
    assert err.value.code == "ma_unavailable"
    assert ctl.delays_now == {"a": 0, "b": 0}
    assert [c for c in ctl.calls if c[0] == "set"] == [("set", "a", 10), ("set", "a", 0)]
    assert s.state == "proposed" and s.proposal is not None
    assert _delay_of(s, "a") == 0
    ctl.set_delay = real
    s.apply()  # a later apply still works
    assert ctl.delays_now == {"a": 0, "b": 30}


def test_when_the_rollback_fails_too_members_show_what_music_assistant_holds():
    s, ctl, _ = _measured_and_proposed()
    real = ctl.set_delay
    writes = []

    def flaky(member, ms):
        writes.append((member.ma_player_id, ms))
        if len(writes) >= 2:
            raise ControlError("ma_unavailable")
        real(member, ms)

    ctl.set_delay = flaky
    with pytest.raises(ControlError):
        s.apply({"a": 10, "b": 20})
    assert ctl.delays_now["a"] == 10
    assert _delay_of(s, "a") == 10 and _delay_of(s, "b") == 0
    assert s.state == "proposed"


# -- UX round: no automatic mode; semi-automatic ends in the manual editor ----------------------------


def test_after_apply_the_operator_tunes_by_ear_and_reset_puts_back_the_delays_from_before():
    s, ctl, _ = _measured_and_proposed()
    s.apply()
    assert s.state == "tuning" and ctl.delays_now["b"] == 30
    s.solo("b")  # solo and A/B as in manual tuning
    assert s.soloed == "b"
    s.write_delay("b", 42)  # a hand adjustment on top of the measured value
    assert ctl.delays_now["b"] == 42 and _delay_of(s, "b") == 42
    s.reset()
    assert ctl.delays_now == {"a": 0, "b": 0}
    s.finish()
    assert s.restore_tick() is True and s.state == "done" and ctl.delays_now == {"a": 0, "b": 0}


def test_recordings_and_a_new_proposal_are_refused_while_tuning_by_ear():
    s, _ctl, _ = _measured_and_proposed()
    s.apply()
    for call in (lambda: s.record(synth("a", lag_s=0.0)), lambda: s.skip("a"), s.propose, s.apply):
        with pytest.raises(ControlError) as exc:
            call()
        assert exc.value.code == "session_state"


def test_a_member_can_be_measured_again_after_the_proposal_before_applying():
    s, ctl, _ = _measured_and_proposed()
    s.record(synth("b", lag_s=0.040))
    p = s.propose()
    assert {r.member: r.after_ms for r in p.rows}["b"] == pytest.approx(40, abs=1)
    s.apply()
    assert ctl.delays_now["b"] == pytest.approx(40, abs=1)


def test_finish_is_refused_before_the_session_started_and_while_restoring():
    s, _ctl, _ = make()
    with pytest.raises(ControlError) as exc:
        s.finish()
    assert exc.value.code == "session_state"
    s.start()
    s.cancel()
    with pytest.raises(ControlError) as exc:
        s.finish()
    assert exc.value.code == "restore_pending"


def test_a_manual_session_finishes_done():
    s, _ctl, _ = make(mode="manual")
    s.start()
    s.write_delay("b", 120)
    s.finish()
    assert s.restore_tick() is True and s.state == "done"


def test_the_session_no_longer_reports_rounds_or_an_undo_window():
    s, _ctl, _ = started()
    d = s.to_dict()
    assert "round" not in d and "undo_until_s" not in d
    assert not hasattr(s, "undo") and not hasattr(s, "verify")


def test_a_recording_from_before_a_clicker_restart_is_not_stored(monkeypatch):
    import threading

    from sendspin_sync import estimator

    s, _ctl, _ = started()
    s.record(synth("a", lag_s=0.0))
    real_estimate = estimator.estimate
    entered, go = threading.Event(), threading.Event()

    def slow_estimate(recording, **kw):
        entered.set()
        assert go.wait(2.0)
        return real_estimate(recording, **kw)

    monkeypatch.setattr(estimator, "estimate", slow_estimate)
    outcome = {}

    def late_record():
        try:
            outcome["m"] = s.record(synth("b", lag_s=0.030))  # audio of the earlier playback
        except ControlError as exc:
            outcome["code"] = exc.code

    t = threading.Thread(target=late_record)
    t.start()
    assert entered.wait(2.0)
    s.start_clicker()  # a new playback: the round starts again
    go.set()
    t.join(2.0)
    assert outcome == {"code": "session_state"}
    assert s.measurements == {}
