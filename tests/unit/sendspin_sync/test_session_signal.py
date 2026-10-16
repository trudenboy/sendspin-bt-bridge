"""The announcement signal (stage 0, option A): one clip per round that cannot be stopped early.

Music Assistant plays the click track once as an announcement and restores music and volumes
when it ends; ``players/cmd/stop`` does not end it. So "stop" silences every member (volume 0)
until the clip ends, a new round waits for the previous clip to end, and the session's restore
waits for the clip too before it checks the volumes.
"""

import pytest

from sendspin_sync.ports import ControlError
from sendspin_sync.session import SIGNAL_CLIP_S, SIGNAL_SETTLE_S, SIGNAL_TAIL_S, SyncSession
from tests.support.sync_fakes import FakeControl, FakeHost
from tests.support.sync_synth import synth

CLIP_END = SIGNAL_CLIP_S + SIGNAL_TAIL_S


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(mode="semi"):
    ctl, clock = FakeControl(), Clock()
    s = SyncSession(
        session_id="s1",
        group_id="g",
        mode=mode,
        point="device",
        control=ctl,
        host=FakeHost(),
        clock=clock,
    )
    return s, ctl, clock


def starts(ctl):
    return [c for c in ctl.calls if c[0] == "start"]


def test_the_clip_covers_a_whole_round_and_the_session_says_when_music_returns():
    s, _ctl, clock = make()
    s.start()
    assert SIGNAL_CLIP_S == 180.0  # the click track's length: Music Assistant plays it once
    assert s.to_dict()["music_returns_in_s"] == pytest.approx(CLIP_END)
    clock.t = 100.0
    assert s.to_dict()["music_returns_in_s"] == pytest.approx(CLIP_END - 100.0)
    clock.t = CLIP_END + 1
    assert s.to_dict()["music_returns_in_s"] is None and not s.signal_running


def test_signal_reports_running_only_after_the_announcement_settled():
    s, _ctl, clock = make()
    s.start()
    assert not s.signal_running and s.clip_playing  # Music Assistant is still starting the clip
    with pytest.raises(ControlError) as exc:
        s.solo("a")
    assert exc.value.code == "signal_starting"
    clock.t = SIGNAL_SETTLE_S
    assert s.signal_running
    s.solo("a")
    assert s.soloed == "a"


def test_stop_silences_every_member_and_never_stops_the_announcement():
    s, ctl, clock = make(mode="manual")
    s.start()
    s.start_signal()
    clock.t = 10.0
    s.stop_signal()
    assert ctl.calls[-1] == ("stop",)  # the adapter's stop: every member to volume 0
    assert not s.signal_running and s.clip_playing and s.silenced
    assert s.to_dict()["music_returns_in_s"] == pytest.approx(CLIP_END - 10.0)  # music returns at clip end
    s.stop_signal()
    assert ctl.calls.count(("stop",)) == 1  # already silent


def test_play_while_the_silenced_clip_still_runs_unsilences_it_without_a_new_announcement():
    s, ctl, clock = make(mode="manual")
    s.start()
    s.start_signal()
    clock.t = SIGNAL_SETTLE_S
    s.solo("b")
    s.stop_signal()
    s.start_signal()
    assert len(starts(ctl)) == 1 and ctl.calls[-1] == ("solo", "b")  # back to what was audible
    assert s.signal_running and not s.silenced
    s.start_signal()  # already playing: nothing to do
    assert len(starts(ctl)) == 1


def test_play_after_the_clip_ended_starts_a_new_announcement():
    s, ctl, clock = make(mode="manual")
    s.start()
    s.start_signal()
    clock.t = CLIP_END + 1
    s.start_signal()
    assert len(starts(ctl)) == 2 and s.to_dict()["signal_clip"] == 2


def test_solo_needs_audible_clicks():
    s, _ctl, _clock = make(mode="manual")
    s.start()
    with pytest.raises(ControlError) as exc:
        s.solo("a")
    assert exc.value.code == "signal_stopped"
    s.solo(None)  # clearing is always fine: it changes no volume while nothing plays
    assert s.soloed is None


def test_the_restore_waits_for_the_clip_and_keeps_every_member_silent_until_then():
    s, ctl, clock = make()
    s.start()
    clock.t = SIGNAL_SETTLE_S
    s.solo("a")
    s.cancel()
    assert s.restore_tick() is False and s.state == "restoring" and s.restore_error is None
    assert ctl.calls[-1] == ("stop",) and ("restore",) not in ctl.calls
    clock.t = 60.0
    assert s.restore_tick() is False and ctl.calls.count(("stop",)) == 1  # silenced once, then it waits
    assert s.to_dict()["music_returns_in_s"] == pytest.approx(CLIP_END - 60.0)
    clock.t = CLIP_END
    assert s.restore_tick() is True and s.state == "cancelled"
    assert ctl.calls[-1] == ("restore",)  # after Music Assistant's own restore: check the volumes
    assert ("solo", None) not in ctl.calls  # the volume restore covers the solo


def test_a_session_whose_clip_already_ended_restores_at_once():
    s, ctl, clock = make()
    s.start()
    clock.t = CLIP_END
    s.cancel()
    assert s.restore_tick() is True and ("stop",) not in ctl.calls


def test_a_failed_silencing_is_a_failed_restore_attempt():
    s, ctl, _clock = make()
    s.start()

    def down(group_id):
        raise ControlError("ma_unavailable")

    ctl.stop_signal = down
    s.cancel()
    assert s.restore_tick() is False and s.restore_error == "ma_unavailable"


def _measure(s, clock):
    clock.t = SIGNAL_SETTLE_S
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()


def test_a_check_round_waits_for_the_previous_clip_to_end():
    s, ctl, clock = make()
    s.start()
    _measure(s, clock)
    clock.t = 40.0
    s.apply(verify=True)
    assert s.state == "verifying" and s.signal_waiting and s.silenced
    assert ctl.calls[-1] == ("stop",) and len(starts(ctl)) == 1  # no second announcement over the first
    d = s.to_dict()
    assert d["signal_waiting"] and d["music_returns_in_s"] == pytest.approx(CLIP_END - 40.0)
    for refused in (lambda: s.solo("a"), lambda: s.record(synth("a", lag_s=0.0))):
        with pytest.raises(ControlError) as exc:
            refused()
        assert exc.value.code == "signal_waiting"
    s.tick()
    assert len(starts(ctl)) == 1
    clock.t = CLIP_END
    s.tick()
    assert len(starts(ctl)) == 2 and not s.signal_waiting and s.to_dict()["signal_clip"] == 2
    clock.t = CLIP_END + SIGNAL_SETTLE_S
    s.solo("a")
    assert s.record(synth("a", lag_s=0.0)).status == "ok"


def test_a_check_round_after_the_clip_ended_starts_at_once():
    s, ctl, clock = make()
    s.start()
    _measure(s, clock)
    clock.t = CLIP_END + 5
    s.apply(verify=True)
    assert len(starts(ctl)) == 2 and not s.signal_waiting


def test_a_new_clip_in_the_middle_of_a_round_starts_the_round_again():
    # phases from two playbacks of the clip cannot be compared
    s, ctl, clock = make()
    s.start()
    clock.t = SIGNAL_SETTLE_S
    s.record(synth("a", lag_s=0.0))
    clock.t = CLIP_END + 1
    s.start_signal()
    assert s.measurements == {} and len(starts(ctl)) == 2


def test_stop_while_a_round_waits_drops_the_waiting_start():
    s, ctl, clock = make()
    s.start()
    _measure(s, clock)
    s.apply(verify=True)
    s.stop_signal()
    clock.t = CLIP_END
    s.tick()
    assert not s.signal_waiting and len(starts(ctl)) == 1


def test_a_start_that_fails_in_the_tick_is_retried_on_the_next_tick():
    s, ctl, clock = make()
    s.start()
    _measure(s, clock)
    s.apply(verify=True)
    real = ctl.start_signal

    def down(group_id, url):
        raise ControlError("ma_unavailable")

    ctl.start_signal = down
    clock.t = CLIP_END
    s.tick()
    assert s.signal_waiting
    ctl.start_signal = real
    s.tick()
    assert not s.signal_waiting and len(starts(ctl)) == 2


def test_a_check_round_still_waits_when_silencing_the_old_clip_fails():
    s, ctl, clock = make()
    s.start()
    _measure(s, clock)

    def down(group_id):
        raise ControlError("ma_unavailable")

    ctl.stop_signal = down
    s.apply(verify=True)  # the delays are written: the apply must not fail over the silencing
    assert s.state == "verifying" and s.signal_waiting and ctl.delays_now["b"] == 30


def test_the_tick_publishes_when_the_clicks_become_audible_and_when_the_clip_ends():
    events = []
    s, _ctl, clock = make(mode="manual")
    s._on_change = lambda sess: events.append(sess.signal_running)
    s.start()
    s.start_signal()
    events.clear()
    s.tick()
    assert events == []  # still settling: nothing new to say
    clock.t = SIGNAL_SETTLE_S
    s.tick()
    s.tick()
    assert events == [True]  # once, so the page can enable solo without polling
    clock.t = CLIP_END
    s.tick()
    assert events == [True, False]


def test_the_auto_undo_window_runs_30_s_from_when_the_check_rounds_clicks_are_audible():
    from sendspin_sync.session import UNDO_WINDOW_S

    s, ctl, clock = make(mode="auto")
    s.start()
    _measure_auto = (s.record(synth("a", lag_s=0.0)), s.record(synth("b", lag_s=0.030)))
    assert _measure_auto[0].status == "ok"
    s.propose()
    clock.t = 40.0
    s.apply()  # the first clip still plays: the check round waits until CLIP_END
    assert s.signal_waiting
    assert s.undo_until == pytest.approx(CLIP_END + SIGNAL_SETTLE_S + UNDO_WINDOW_S)
    clock.t = CLIP_END - 1  # minutes after the apply, still waiting: undo still possible
    s.undo()
    assert ctl.delays_now["b"] == 0


def test_without_a_wait_the_undo_window_still_counts_from_audible_clicks():
    from sendspin_sync.session import UNDO_WINDOW_S

    s, _ctl, clock = make(mode="auto")
    s.start()
    clock.t = SIGNAL_SETTLE_S
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    clock.t = CLIP_END + 10  # the first clip ended: the check round starts its clip at once
    s.apply()
    assert not s.signal_waiting
    assert s.undo_until == pytest.approx(CLIP_END + 10 + SIGNAL_SETTLE_S + UNDO_WINDOW_S)
