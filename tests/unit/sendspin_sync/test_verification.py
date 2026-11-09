"""Verification measures the residual, never re-applies or plans a new delay."""

import pytest

from sendspin_sync.ports import ControlError
from tests.support.sync_synth import synth
from tests.unit.sendspin_sync.test_session import _measured_and_proposed


def applied():
    session, control, _ = _measured_and_proposed()
    session.apply()
    return session, control


def test_verify_reports_measured_spread_and_keeps_the_applied_compensation():
    session, control = applied()
    original = session.proposal
    session.start_verification()
    session.record(synth("a", lag_s=0.0))
    session.record(synth("b", lag_s=0.001))
    calls = list(control.calls)
    result = session.finish_verification()
    assert result["spread_ms"] == pytest.approx(1.0, abs=0.5)
    assert result["passed"] is True
    assert session.state == "tuning" and session.proposal is original
    assert control.delays_now["b"] == 30
    assert not [call for call in control.calls[len(calls) :] if call[0] == "set"]
    assert session.measurements["b"].phase_ms == pytest.approx(30.0, abs=0.5)


def test_a_wide_residual_is_not_reported_as_success():
    session, _ = applied()
    session.start_verification()
    session.record(synth("a", lag_s=0.0))
    session.record(synth("b", lag_s=0.020))
    assert session.finish_verification()["passed"] is False


def test_only_one_heard_speaker_cannot_verify_a_group():
    session, _ = applied()
    session.start_verification()
    session.record(synth("a", lag_s=0.0))
    result = session.finish_verification()
    assert result["passed"] is False and result["spread_ms"] is None
    assert "b" in result["unverified"]


def test_cancelling_verification_keeps_the_applied_values():
    session, control = applied()
    session.start_verification()
    session.finish_verification(cancel=True)
    assert session.state == "tuning"
    assert control.delays_now["b"] == 30
    assert session.verification["state"] == "cancelled"


def test_a_delay_change_during_verification_invalidates_the_result():
    session, control = applied()
    session.start_verification()
    session.record(synth("a", lag_s=0.0))
    session.record(synth("b", lag_s=0.001))
    control.delays_now["b"] = 80
    result = session.finish_verification()
    assert result["state"] == "invalidated" and result["passed"] is False
    assert control.delays_now["b"] == 80


def test_verification_cannot_be_used_to_compute_another_proposal():
    session, _ = applied()
    session.start_verification()
    with pytest.raises(ControlError):
        session.propose()


def test_a_manual_change_after_verification_clears_the_result():
    session, _ = applied()
    session.start_verification()
    session.record(synth("a", lag_s=0.0))
    session.record(synth("b", lag_s=0.001))
    session.finish_verification()
    session.write_delay("b", 35)
    assert session.verification is None


def test_skipping_a_newly_missing_member_cannot_turn_a_partial_check_green():
    from sendspin_sync.models import Member

    session, control = applied()
    third = Member("c", "Third", True, "sendspin", delay_ms=0)
    session.members.append(third)
    control.member_list.append(third)
    control.delays_now["c"] = 0
    session.start_verification()
    session.record(synth("a", lag_s=0.0))
    session.record(synth("b", lag_s=0.001))
    session.skip("c")
    result = session.finish_verification()
    assert result["passed"] is False and "c" in result["unverified"]


def test_a_later_server_edit_marks_a_completed_verification_stale():
    session, _ = applied()
    session.start_verification()
    session.record(synth("a", lag_s=0.0))
    session.record(synth("b", lag_s=0.001))
    session.finish_verification()
    session.observe_compensation({"b"}, 30)  # same delay, just an echo
    assert session.verification["passed"] is True
    session.observe_compensation({"b"}, 80)
    assert session.verification["state"] == "invalidated" and session.verification["passed"] is False


def test_cancelling_verification_can_exit_when_music_assistant_is_unavailable():
    session, control = applied()
    session.start_verification()

    def unavailable(_members):
        raise ControlError("ma_unavailable", "offline")

    control.delays = unavailable
    session.finish_verification(cancel=True)
    assert session.state == "tuning" and control.delays_now["b"] == 30
