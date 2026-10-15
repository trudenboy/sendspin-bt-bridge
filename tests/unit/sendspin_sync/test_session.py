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
