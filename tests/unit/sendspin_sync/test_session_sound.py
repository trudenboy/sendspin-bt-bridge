"""Manual tuning's listening sounds: the operator picks clicks, clicks with an accent or noise; the
semi-automatic measurement (and the phone's sequence) always plays the measurement's click track."""

import pytest

from sendspin_sync.ports import ControlError
from sendspin_sync.session import SyncSession
from tests.support.sync_fakes import FakeControl, FakeHost

MEASURE = "http://bridge/api/v1/sync/signal/s1.wav"


def sound_url(kind: str) -> str:
    return f"http://bridge/api/v1/sync/signal/s1/{kind}.wav"


def make(mode: str = "manual"):
    ctl = FakeControl()
    published: list[dict] = []
    s = SyncSession(
        session_id="s1",
        group_id="g",
        mode=mode,  # type: ignore[arg-type]
        point="device",
        control=ctl,
        host=FakeHost(),
        on_change=lambda sess: published.append(sess.to_dict()),
    )
    s.start()
    s.stop_group()
    ctl.calls.clear()
    published.clear()
    return s, ctl, published


def clicker_calls(ctl: FakeControl) -> list[str]:
    return [c[1] for c in ctl.calls if c[0] == "clicker"]


def test_manual_tuning_plays_the_clicks_by_default():
    s, ctl, _ = make()
    assert s.to_dict()["sound"] == "clicks"
    s.start_clicker()
    assert clicker_calls(ctl) == [sound_url("clicks")] and s.clicker_playing


def test_the_measurement_always_plays_the_measurement_track():
    s, ctl, _ = make(mode="semi")
    assert s.to_dict()["sound"] is None
    with pytest.raises(ControlError) as exc:
        s.set_sound("noise")
    assert exc.value.code == "session_state"
    s.start_clicker()
    assert clicker_calls(ctl) == [MEASURE]


def test_a_sound_picked_while_nothing_plays_is_kept_for_the_next_start():
    s, ctl, published = make()
    s.set_sound("accent")
    assert ctl.calls == [] and s.to_dict()["sound"] == "accent" and published[-1]["sound"] == "accent"
    s.start_clicker()
    assert clicker_calls(ctl) == [sound_url("accent")]


def test_switching_the_sound_while_it_plays_restarts_the_clicker_and_keeps_the_solo():
    s, ctl, published = make()
    s.start_clicker()
    s.solo("a")
    muted, starts = set(s.muted), s.clicker_starts
    assert muted == {"b", "cast"}
    ctl.calls.clear()
    s.set_sound("noise")
    assert ctl.calls == [("clicker", sound_url("noise"))]  # no unmute, no stop: the queue is replaced
    assert s.soloed == "a" and s.muted == muted and s.clicker_playing and s.clicker_starts == starts + 1
    assert published[-1]["sound"] == "noise" and published[-1]["clicker_playing"] is True


def test_the_same_sound_again_does_not_restart_the_clicks():
    s, ctl, _ = make()
    s.start_clicker()
    ctl.calls.clear()
    s.set_sound("clicks")
    assert ctl.calls == []


def test_a_sound_picked_while_someone_elses_music_plays_never_replaces_it():
    s, ctl, _ = make()
    ctl.group_state, ctl.media_uri = "playing", "library://track/9"
    s.set_sound("noise")
    assert clicker_calls(ctl) == [] and s.to_dict()["sound"] == "noise"


def test_the_end_stops_the_sound_that_plays_and_unmutes():
    s, ctl, _ = make()
    s.start_clicker()
    s.solo("a")
    s.set_sound("accent")
    ctl.calls.clear()
    s.finish()
    assert s.restore_tick()
    assert ("stop_group",) in ctl.calls and s.muted == set() and s.state == "done"


def test_the_sound_is_refused_once_the_session_ended():
    s, _ctl, _ = make()
    s.finish()
    with pytest.raises(ControlError) as exc:
        s.set_sound("noise")
    assert exc.value.code == "restore_pending"
