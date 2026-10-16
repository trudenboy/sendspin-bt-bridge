"""The signal path (option C): the operator stops the group, starts the clicker on it, sets the volumes;
solo mutes the other members; the end unmutes what the session muted and stops the clicker."""

import pytest

from sendspin_sync.ports import ControlError
from sendspin_sync.session import PLAYBACK_POLL_S, SyncSession
from tests.support.sync_fakes import FakeControl, FakeHost
from tests.support.sync_synth import synth

URL = "http://bridge/api/v1/sync/signal/s1.wav"


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(mode="semi"):
    ctl = FakeControl()
    published = []
    s = SyncSession(
        session_id="s1",
        group_id="g",
        mode=mode,
        point="device",
        control=ctl,
        host=FakeHost(),
        clock=Clock(),
        on_change=lambda sess: published.append(sess.to_dict()),
    )
    s.start()
    published.clear()
    return s, ctl, published


def code_of(call, *args):
    with pytest.raises(ControlError) as exc:
        call(*args)
    return exc.value


# -- preparation -----------------------------------------------------------------------------


def test_start_reads_the_group_state_and_writes_nothing():
    s, ctl, _ = make()
    d = s.to_dict()
    assert d["group_state"] == "playing" and d["clicker_playing"] is False and d["clicker_starts"] == 0
    assert d["volumes"] == {"a": 40, "b": 40, "cast": 20} and d["muted"] == []
    assert ctl.calls == []
    assert "music_returns_in_s" not in d and "signal_waiting" not in d


def test_the_clicker_waits_until_the_group_stopped_playing():
    s, ctl, _ = make()
    err = code_of(s.start_clicker)
    assert err.code == "group_playing" and ctl.calls == []
    s.stop_group()
    assert s.group_state == "idle"
    s.start_clicker()
    assert ctl.calls == [("stop_group",), ("clicker", URL)]
    assert s.clicker_playing and s.clicker_starts == 1 and s.group_state == "playing"


def test_the_clicker_can_be_started_again_while_it_plays_or_after_it_ended():
    s, ctl, _ = make(mode="manual")
    s.stop_group()
    s.start_clicker()
    s.start_clicker()  # manual: just play it again from the start
    ctl.clicker_ends()
    s.start_clicker()
    assert [c for c in ctl.calls if c[0] == "clicker"] == [("clicker", URL)] * 3 and s.clicker_starts == 3


def test_music_started_meanwhile_blocks_the_clicker_again():
    s, ctl, _ = make()
    s.stop_group()
    ctl.group_state, ctl.media_uri = "playing", "library://track/9"  # someone pressed play in MA
    assert code_of(s.start_clicker).code == "group_playing"


def test_solo_and_record_need_the_clicker():
    s, ctl, _ = make()
    assert code_of(s.solo, "a").code == "clicker_stopped"
    assert code_of(s.record, synth("a", lag_s=0.0)).code == "clicker_stopped"
    s.stop_group()
    s.start_clicker()
    s.solo("a")
    assert s.record(synth("a", lag_s=0.0)).status == "ok"
    ctl.clicker_ends()  # the file played out, or the operator stopped it in Music Assistant
    assert code_of(s.solo, "b").code == "clicker_stopped"
    assert code_of(s.record, synth("b", lag_s=0.0)).code == "clicker_stopped"
    assert s.clicker_playing is False and s.to_dict()["clicker_playing"] is False
    s.start_clicker()  # "Start the clicker again"
    s.solo("b")
    assert s.soloed == "b"


def test_a_restarted_clicker_starts_the_round_again_outside_manual_mode():
    s, _ctl, _ = make()
    s.stop_group()
    s.start_clicker()
    s.record(synth("a", lag_s=0.0))
    s.start_clicker()  # a new playback: phases from the old one do not compare
    assert s.measurements == {}


def test_set_volume_writes_the_member_and_shows_it():
    s, ctl, published = make()
    s.set_volume("b", 65)
    assert ctl.calls == [("volume", "b", 65)] and s.volumes["b"] == 65
    assert published[-1]["volumes"]["b"] == 65
    assert code_of(s.set_volume, "zz", 10).code == "member_gone"


def test_tick_polls_the_group_and_publishes_only_changes():
    s, ctl, published = make()
    s._clock.t += PLAYBACK_POLL_S
    s.tick()
    assert published == []  # nothing changed
    ctl.group_state = "idle"  # stopped from Music Assistant
    s._clock.t += PLAYBACK_POLL_S / 2
    s.tick()
    assert published == []  # not due yet
    s._clock.t += PLAYBACK_POLL_S
    s.tick()
    assert published[-1]["group_state"] == "idle"


def test_tick_survives_music_assistant_being_down():
    s, ctl, published = make()

    def down(group_id):
        raise ControlError("ma_unavailable")

    ctl.playback = down
    s._clock.t += PLAYBACK_POLL_S
    s.tick()
    assert s.group_state == "playing" and published == []


# -- solo by mute ----------------------------------------------------------------------------


def ready(mode="semi"):
    s, ctl, published = make(mode)
    s.stop_group()
    s.start_clicker()
    ctl.calls.clear()
    return s, ctl, published


def test_solo_mutes_every_other_member_and_unmutes_the_soloed_one():
    s, ctl, _ = ready()
    s.solo("a")
    assert ctl.calls == [("mute", "b", True), ("mute", "cast", True)]
    assert s.to_dict()["muted"] == ["b", "cast"] and s.soloed == "a"
    s.solo("b")
    assert ctl.calls[2:] == [("mute", "a", True), ("mute", "b", False)]
    assert s.to_dict()["muted"] == ["a", "cast"]
    s.solo(None)
    assert ctl.muted_now == {"a": False, "b": False, "cast": False} and s.to_dict()["muted"] == []
    assert s.soloed is None


def test_a_member_muted_before_the_session_is_left_alone():
    ctl = FakeControl()
    ctl.muted_now["cast"] = True  # the operator muted the TV
    s = SyncSession(session_id="s1", group_id="g", mode="semi", point="device", control=ctl, host=FakeHost())
    s.start()
    s.stop_group()
    s.start_clicker()
    s.solo("a")
    s.solo(None)
    assert ("mute", "cast", True) not in ctl.calls and ("mute", "cast", False) not in ctl.calls
    assert ctl.muted_now["cast"] is True


def test_a_member_without_mute_is_named_and_nothing_stays_muted():
    s, ctl, _ = ready()
    ctl.no_mute = {"cast"}
    err = code_of(s.solo, "a")
    assert err.code == "sync_mute_unsupported" and err.member == "cast"
    # "b" was muted on the way: put back, so a refused solo leaves the group as it was
    assert ctl.muted_now == {"a": False, "b": False, "cast": False}
    assert s.to_dict()["muted"] == [] and s.soloed is None
    s.solo("cast")  # the member without mute can still be heard alone: it is not muted itself
    assert s.soloed == "cast"


def test_the_member_list_marks_who_the_session_muted_before_the_mute_is_sent():
    s, ctl, published = ready()
    seen = []
    real = ctl.mute

    def spy(member, muted):
        seen.append(list(published[-1]["muted"]) if published else [])
        real(member, muted)

    ctl.mute = spy
    s.solo("a")
    # persisted first: a crash between the two never leaves a mute nobody undoes
    assert "b" in seen[0]


# -- the end -----------------------------------------------------------------------------------


def test_the_end_unmutes_then_stops_our_clicker():
    s, ctl, _ = ready()
    s.solo("a")
    s.cancel()
    assert s.restore_tick() is True and s.state == "cancelled"
    assert ctl.calls[-3:] == [("mute", "b", False), ("mute", "cast", False), ("stop_group",)]


def test_the_end_never_stops_music_the_operator_started_meanwhile():
    s, ctl, _ = ready()
    ctl.group_state, ctl.media_uri = "playing", "library://track/9"
    s.cancel()
    assert s.restore_tick() is True and ("stop_group",) not in ctl.calls


def test_the_end_without_a_clicker_touches_nothing():
    s, ctl, _ = make()
    s.cancel()
    assert s.restore_tick() is True and ctl.calls == []


def test_the_end_unmutes_a_member_that_left_the_group():
    s, ctl, _ = ready()
    s.solo("a")
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "cast"]
    s.skip("a")  # notices that "cast" left
    assert "cast" in s.departed
    s.cancel()
    assert s.restore_tick() is True and ctl.muted_now["cast"] is False


def test_the_end_goes_through_when_the_group_is_gone():
    s, ctl, _ = ready()

    def gone(group_id):
        raise ControlError("member_gone", "group g is not known")

    ctl.playback = gone
    s.cancel()
    assert s.restore_tick() is True and s.state == "cancelled"


def test_a_failed_unmute_is_retried_and_keeps_the_rest_for_later():
    s, ctl, _ = ready()
    s.solo("a")
    ctl.fail_unmute = 1
    s.cancel()
    assert s.restore_tick() is False and s.restore_error == "ma_unavailable"
    assert s.to_dict()["muted"] == ["b", "cast"]
    assert s.restore_tick() is True and s.to_dict()["muted"] == []
