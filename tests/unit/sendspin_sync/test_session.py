import json

import pytest

from sendspin_sync.ports import ControlError
from sendspin_sync.session import SyncSession
from tests.support.sync_fakes import FakeControl, FakeHost
from tests.support.sync_synth import synth


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
        clock=kw.pop("clock", None) or (lambda: 0.0),
        on_change=lambda sess: events.append(sess.state),
    )
    return s, ctl, events


def test_semi_session_runs_measure_propose_apply_restore():
    s, ctl, _events = make()
    s.start()
    assert s.state == "measuring" and ("start", "http://bridge/api/v1/sync/signal/s1.wav") in ctl.calls
    assert [m.ma_player_id for m in s.members] == ["a", "b", "cast"]
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
    assert s.state == "restoring"
    assert s.restore_tick() is True and s.state == "done"
    assert ctl.calls[-3:] == [("solo", None), ("stop",), ("restore",)]


def test_auto_session_verifies_and_keeps_undo():
    clock = {"t": 100.0}
    s, ctl, _ = make(mode="auto", clock=lambda: clock["t"])
    s.start()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    s.apply()
    assert s.state == "verifying" and s.round == 2 and s.undo_until == 130.0
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.0))  # now aligned
    s.propose()
    assert s.state == "restoring" and s.history[-1]["spread_after_ms"] == pytest.approx(0.0, abs=1.0)
    clock["t"] = 125.0
    s.undo()
    assert ctl.delays_now == {"a": 0, "b": 0}
    clock["t"] = 131.0
    with pytest.raises(ControlError):
        s.undo()


def test_restore_retries_until_it_succeeds_and_cancel_goes_through_restoring():
    s, ctl, _ = make()
    s.start()
    ctl.fail_restore = 2
    s.cancel()
    assert s.state == "restoring"
    assert s.restore_tick() is False and s.restore_tick() is False and s.restore_tick() is True
    assert s.state == "cancelled" and ("restore",) in ctl.calls


def test_manual_mode_writes_live_and_resets():
    s, ctl, _ = make(mode="manual")
    s.start()
    assert s.state == "measuring" and not s.signal_running
    s.write_delay("b", 250)
    assert ctl.delays_now["b"] == 250
    with pytest.raises(ControlError) as exc:
        s.write_delay("cast", 10)
    assert exc.value.code == "not_adjustable"
    s.start_signal()
    s.ab("a", "b")
    assert ("solo", "a") in ctl.calls
    s.reset()
    assert ctl.delays_now["b"] == 0


def test_apply_failure_keeps_the_proposal_and_moves_nothing_else():
    s, ctl, _ = make()
    s.start()
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
    s, _ctl, _ = make(clock=lambda: clock["t"])
    s.start()
    clock["t"] = 901.0
    assert s.expired()
    with pytest.raises(ControlError) as exc:
        s.record(synth("zz", lag_s=0.0))
    assert exc.value.code == "member_gone"


def test_to_dict_is_json_shaped():
    s, _, _ = make()
    s.start()
    s.record(synth("a", lag_s=0.0))
    d = json.loads(json.dumps(s.to_dict()))
    assert d["id"] == "s1" and d["state"] == "measuring" and d["measurements"]["a"]["status"] == "ok"
    assert "pcm" not in json.dumps(d)


# -- controller rulings (preflight A13/C6, A14/C7, A10) -------------------------------------------


def _measured_and_proposed(mode="semi"):
    s, ctl, events = make(mode=mode)
    s.start()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    return s, ctl, events


def test_cancel_after_a_successful_apply_keeps_the_outcome_done():
    s, _ctl, _ = _measured_and_proposed()
    s.apply()
    assert s.state == "restoring"
    s.cancel()  # a DELETE or Retry while restoring is a no-op
    assert s.restore_tick() is True and s.state == "done"
    s.cancel()  # and after the restore too
    assert s.state == "done"


def test_semi_mode_can_verify_after_apply():
    s, ctl, _ = _measured_and_proposed()
    s.apply(verify=True)
    assert s.state == "verifying" and s.round == 2 and s.undo_until is None
    assert ctl.delays_now["b"] == 30
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.0))
    s.propose()
    assert s.state == "restoring" and s.history[-1]["spread_after_ms"] == pytest.approx(0.0, abs=1.0)
    assert s.restore_tick() is True and s.state == "done"


def test_operations_while_restoring_answer_restore_pending():
    s, _ctl, _ = make()
    s.start()
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


def _auto_applied(clock=None):
    s, ctl, events = make(mode="auto", clock=clock)
    s.start()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    s.apply()
    return s, ctl, events


def test_undo_during_verification_restores_members_and_ends_without_refining():
    s, ctl, _ = _auto_applied()
    assert s.state == "verifying" and _delay_of(s, "b") == 30
    s.undo()
    assert ctl.delays_now["b"] == 0 and _delay_of(s, "b") == 0
    assert s.state == "restoring"
    with pytest.raises(ControlError) as exc:  # no refinement on top of an undone apply
        s.record(synth("b", lag_s=0.030))
    assert exc.value.code == "restore_pending"
    assert s.restore_tick() is True and s.state == "done"
    assert [c for c in ctl.calls if c[0] == "set"] == [("set", "b", 30), ("set", "b", 0)]


SIDE_METHODS = [
    ("start_signal", ()),
    ("stop_signal", ()),
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
    s, ctl, _ = make()
    s.start()
    s.cancel()
    assert s.restore_tick() is True and s.state == "cancelled"
    calls = list(ctl.calls)
    with pytest.raises(ControlError) as exc:
        getattr(s, name)(*args)
    assert exc.value.code == "session_state"
    assert ctl.calls == calls and not s.signal_running and s.soloed is None


def test_fail_detail_survives_restore_retries():
    s, ctl, _ = make()
    s.start()
    s.fail("boom")
    ctl.fail_restore = 1
    assert s.restore_tick() is False
    assert s.error == "boom" and s.restore_error == "ma_unavailable"
    assert s.to_dict()["restore_error"] == "ma_unavailable"
    assert s.restore_tick() is True
    assert s.state == "failed" and s.error == "boom" and s.restore_error is None
    assert s.to_dict()["error"] == "boom" and s.to_dict()["restore_error"] is None


def test_successful_retry_clears_the_transient_restore_error():
    s, ctl, _ = make()
    s.start()
    s.cancel()
    ctl.fail_restore = 1
    assert s.restore_tick() is False and s.restore_error == "ma_unavailable"
    assert s.restore_tick() is True
    assert s.state == "cancelled" and s.error is None and s.restore_error is None


def test_concurrent_apply_and_cancel_leave_a_consistent_outcome():
    import threading
    import time

    s, ctl, _ = make()
    s.start()
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
    # apply finished first, cancel was a no-op on the restoring session, the tick restored: one outcome
    assert s.state == "done" and ctl.delays_now["b"] == 30 and ctl.calls[-1] == ("restore",)


def test_auto_refines_once_when_the_residual_is_above_5_ms_then_restores():
    clock = {"t": 100.0}
    s, ctl, _ = _auto_applied(clock=lambda: clock["t"])
    assert s.round == 2 and s.undo_until == 130.0
    clock["t"] = 110.0
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.012))  # 12 ms residual
    s.propose()
    assert s.state == "verifying" and s.round == 3
    assert s.undo_until == 140.0  # undo window restarts with the refinement
    assert s.history[0]["spread_after_ms"] == pytest.approx(12.0, abs=1.0)
    assert s.history[-1]["round"] == 2 and s.history[-1]["spread_before_ms"] == pytest.approx(12.0, abs=1.0)
    assert ctl.delays_now["b"] == pytest.approx(42, abs=1)
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.012))  # still off: no third apply
    s.propose()
    assert s.state == "restoring"
    assert s.history[-1]["spread_after_ms"] == pytest.approx(12.0, abs=1.0)
    assert len([c for c in ctl.calls if c[0] == "set"]) == 2


def test_start_failure_in_the_signal_rolls_back_to_created():
    s, ctl, _ = make()
    real = ctl.start_signal

    def boom(group_id, url):
        raise ControlError("ma_unavailable")

    ctl.start_signal = boom
    with pytest.raises(ControlError):
        s.start()
    assert s.state == "created" and not s.signal_running
    ctl.start_signal = real
    s.start()
    assert s.state == "measuring" and s.signal_running


def test_refine_with_an_unplannable_reference_answers_no_measurement():
    s, _ctl, _ = _auto_applied()
    s.set_reference("cast")  # not adjustable, so never measured "ok" for the planner
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.012))
    with pytest.raises(ControlError) as exc:
        s.propose()
    assert exc.value.code == "no_measurement"
    assert s.state == "verifying"


def test_undo_falls_back_to_the_snapshot_delay_when_the_member_has_none():
    from sendspin_sync.models import Member

    s, ctl, _ = make(mode="auto")
    ctl.member_list[1] = Member("b", "B", True, "sendspin", delay_ms=None)
    ctl.delays_now["b"] = 100
    s.start()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    s.apply(rows={"b": 130})
    s.undo()
    assert ctl.delays_now["b"] == 100 and _delay_of(s, "b") == 100


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
    s, _ctl, _ = make(clock=lambda: clock["t"])
    s.start()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    clock["t"] = 500.0
    s.propose()
    clock["t"] = 1000.0
    assert not s.expired()


# -- review fix round 2 --------------------------------------------------------------------------


def test_a_recording_from_before_an_apply_is_not_stored_in_the_next_round(monkeypatch):
    import threading

    from sendspin_sync import estimator

    s, _ctl, _ = make(mode="auto")
    s.start()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
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
            outcome["m"] = s.record(synth("b", lag_s=0.030))  # round-1 audio
        except ControlError as exc:
            outcome["code"] = exc.code

    t = threading.Thread(target=late_record)
    t.start()
    assert entered.wait(2.0)
    s.propose()
    s.apply()  # auto: round 2 starts with cleared measurements
    assert s.state == "verifying" and s.round == 2
    go.set()
    t.join(2.0)
    assert outcome == {"code": "session_state"}
    assert s.measurements == {}


def test_start_keeps_tracking_a_signal_that_started_when_on_change_fails():
    ctl = FakeControl()

    def on_change(sess):
        if sess.state == "measuring":
            raise RuntimeError("listener broke")

    s = SyncSession(
        session_id="s1",
        group_id="g",
        mode="semi",
        point="device",
        control=ctl,
        host=FakeHost(),
        clock=lambda: 0.0,
        on_change=on_change,
    )
    with pytest.raises(RuntimeError):
        s.start()
    assert ("start", "http://bridge/api/v1/sync/signal/s1.wav") in ctl.calls
    # the click track is playing: the session must not pretend it never started
    assert s.signal_running and s.state != "created" and s.snapshot is not None
    s.cancel()
    assert s.restore_tick() is True and ("stop",) in ctl.calls


def test_a_member_that_leaves_the_group_is_dropped_and_planning_continues_without_it():
    s, ctl, _ = make()
    s.start()
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "b"]  # "b" leaves the group
    p = s.propose()
    assert [r.member for r in p.rows] == ["a"] and "b" not in s.measurements
    assert s.to_dict()["departed"] == ["b"] and "b" not in [m["ma_player_id"] for m in s.to_dict()["members"]]


def test_a_departed_member_is_refused_when_its_recording_arrives():
    s, ctl, _ = make()
    s.start()
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "b"]
    with pytest.raises(ControlError) as exc:
        s.record(synth("b", lag_s=0.0))
    assert exc.value.code == "member_gone" and s.to_dict()["departed"] == ["b"]


def test_undo_still_reaches_a_member_that_left_after_the_apply():
    s, ctl, _ = _auto_applied()
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "b"]
    s.record(synth("a", lag_s=0.0))  # the next step notices "b" left
    s.undo()
    assert ctl.delays_now["b"] == 0


def test_membership_refresh_is_best_effort_when_music_assistant_is_down():
    s, ctl, _ = make()
    s.start()

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
    s.apply()  # a later apply still works and keeps an exact undo baseline
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
