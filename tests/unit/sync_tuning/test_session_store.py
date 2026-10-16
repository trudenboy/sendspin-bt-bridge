import json
import threading

import pytest

from sendspin_bridge.sync_tuning import session_store as store_mod
from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_bridge.sync_tuning.session_store import SNAPSHOT_FILE, SessionStore
from sendspin_sync.ports import ControlError
from sendspin_sync.session import SIGNAL_CLIP_S, SIGNAL_SETTLE_S, SIGNAL_TAIL_S
from tests.support.sync_fakes import FakeControl, FakeHost
from tests.support.sync_synth import synth


class ClosableControl(FakeControl):
    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


def make(tmp_path, *, controls=None):
    """controls: list the factory appends each new control to (one per session)."""
    made = controls if controls is not None else []
    events = []
    t = {"now": 0.0}
    host = FakeHost()

    def factory():
        made.append(ClosableControl())
        return made[-1]

    store = SessionStore(
        control_factory=factory,
        host=host,
        publish=events.append,
        snapshot_dir=lambda: tmp_path,
        clock=lambda: t["now"],
        wall_clock=lambda: WALL0 + t["now"],
    )
    return store, made, events, t, host


WALL0 = 1_700_000_000.0  # the wall clock when the store's monotonic clock reads 0
CLIP_END = SIGNAL_CLIP_S + SIGNAL_TAIL_S


def _file(tmp_path):
    return tmp_path / SNAPSHOT_FILE


def _persisted(tmp_path):
    return json.loads(_file(tmp_path).read_text())["sessions"]


def _old_snapshot(**kw):
    entry = {
        "session_id": "old",
        "group_id": "g",
        "signal_running": True,
        "soloed": "a",
        "snapshot": {
            "group_id": "g",
            "delays": {"a": 0},
            "volumes": {"a": 40},
            "muted": {"a": False, "b": False},
            "playing": True,
            "restore_token": {},
        },
    }
    entry.update(kw)
    return {"sessions": [entry]}


def test_one_session_per_group_and_events_are_published(tmp_path):
    store, made, events, _, _ = make(tmp_path)
    s = store.create("g", "semi", "device")
    assert s.state == "measuring" and store.get(s.id) is s and store.active_for("g") is s
    assert events[-1]["id"] == s.id and events[-1]["state"] == "measuring"  # session data, not an envelope
    with pytest.raises(ControlError) as exc:
        store.create("g", "manual", "device")
    assert exc.value.code == "session_active"
    assert len(made) == 1  # a refused create builds no control
    [entry] = _persisted(tmp_path)
    assert (entry["session_id"], entry["group_id"]) == (s.id, "g") and entry["signal_ends_at"] is not None
    assert entry["snapshot"]["volumes"] == {"a": 40, "b": 40}


def test_each_session_gets_its_own_control(tmp_path):
    store, made, _, _, _ = make(tmp_path)
    a = store.create("g1", "manual", "device")
    b = store.create("g2", "manual", "device")
    assert len(made) == 2 and a._control is made[0] and b._control is made[1]
    assert {e["group_id"] for e in _persisted(tmp_path)} == {"g1", "g2"}


def test_end_restores_with_backoff_and_removes_the_snapshot(tmp_path):
    store, made, _, t, host = make(tmp_path)
    s = store.create("g", "semi", "device")
    ctl = made[0]
    ctl.fail_restore = 2
    b = CLIP_END  # the clip has played out: the restore itself is due
    t["now"] = b
    store.end(s.id)
    assert s.state == "restoring" and _file(tmp_path).exists()
    store.tick()
    assert s.state == "restoring" and s.restore_error == "ma_unavailable"  # attempt 1 failed, next after 1 s
    t["now"] = b + 0.5
    store.tick()
    assert ("restore",) not in ctl.calls
    t["now"] = b + 1.1
    store.tick()  # attempt 2 fails, next after 2 s
    t["now"] = b + 2.9
    store.tick()
    assert s.state == "restoring"
    t["now"] = b + 3.2
    store.tick()
    assert s.state == "cancelled" and not _file(tmp_path).exists()
    assert host.released == [s.id] and ctl.closed == 1
    t["now"] = b + 60.0
    store.tick()
    assert store.get(s.id) is s  # finished sessions stay readable for a while
    t["now"] = b + 70.0
    store.tick()
    with pytest.raises(ControlError) as exc:
        store.get(s.id)
    assert exc.value.code == "session_expired"
    assert store.active_for("g") is None and ctl.closed == 1


def test_a_session_that_restores_by_itself_is_driven_by_the_store(tmp_path):
    store, _made, _, t, host = make(tmp_path)
    s = store.create("g", "semi", "device")
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    s.apply()  # semi without verify: restores and ends
    assert s.state == "restoring"
    store.tick()
    assert s.state == "restoring"  # members silent until the clip ends
    t["now"] = CLIP_END
    store.tick()
    assert s.state == "done" and host.released == [s.id] and not _file(tmp_path).exists()


def test_any_exception_from_a_restore_is_a_failed_attempt(tmp_path):
    store, made, _, t, _ = make(tmp_path)
    s = store.create("g", "manual", "device")
    calls = {"n": 0}

    def flaky(snapshot):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("socket exploded")
        made[0].calls.append(("restore",))

    made[0].restore = flaky
    store.end(s.id)
    store.tick()  # must not raise
    assert s.state == "restoring" and _file(tmp_path).exists()
    t["now"] = 1.0
    store.tick()
    assert s.state == "cancelled" and not _file(tmp_path).exists()


def test_inactivity_ends_the_session(tmp_path):
    store, made, _, t, _ = make(tmp_path)
    s = store.create("g", "manual", "device")
    t["now"] = 901.0
    store.tick()
    store.tick()
    assert s.state == "cancelled" and ("restore",) in made[0].calls


def test_an_auto_session_keeps_its_undo_while_the_check_rounds_clip_plays_out(tmp_path):
    # The check round's clip outlasts the 30 s undo window, so the session is still restoring
    # (members silent until the clip ends) while the undo is possible; the control stays open
    # until the session finished, then the finished session is evicted after a while.
    store, made, _, t, _ = make(tmp_path)
    s = store.create("g", "auto", "device")
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    b = CLIP_END
    t["now"] = b  # the first clip played out: the check round starts its own at once
    store.tick()
    s.apply()  # undo until b + 30
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.0))
    s.propose()
    store.tick()
    assert s.state == "restoring" and made[0].closed == 0
    t["now"] = b + 25.0
    store.tick()
    s.undo()
    assert made[0].delays_now["b"] == 0
    t["now"] = b + CLIP_END  # the check round's clip ends: restored and finished
    store.tick()
    assert s.state == "done" and made[0].closed == 1
    t["now"] = b + CLIP_END + 59.0
    store.tick()
    assert store.get(s.id) is s
    t["now"] = b + CLIP_END + 61.0
    store.tick()
    with pytest.raises(ControlError):
        store.get(s.id)


def test_a_failed_start_frees_the_group_releases_the_token_and_removes_the_snapshot(tmp_path):
    store, made, _, _, host = make(tmp_path, controls=[])

    def refuse(group_id, url):
        raise ControlError("ma_forbidden", "no")

    original = store._factory

    def factory():
        ctl = original()
        ctl.start_signal = refuse
        return ctl

    store._factory = factory
    with pytest.raises(ControlError) as exc:
        store.create("g", "semi", "device")
    assert exc.value.code == "ma_forbidden"
    assert store.active_for("g") is None and len(host.released) == 1 and made[0].closed == 1
    assert not _file(tmp_path).exists()
    store._factory = original
    assert store.create("g", "semi", "device").state == "measuring"


def test_music_assistant_not_connected_is_a_control_error(tmp_path):
    def factory():
        raise MaApiError("ma_unavailable", "Music Assistant is not connected")

    store = SessionStore(
        control_factory=factory, host=FakeHost(), publish=lambda e: None, snapshot_dir=lambda: tmp_path
    )
    with pytest.raises(ControlError) as exc:
        store.create("g", "manual", "device")
    assert exc.value.code == "ma_unavailable" and store.active_for("g") is None


def test_ending_one_group_keeps_the_other_persisted(tmp_path):
    store, _, _, _, _ = make(tmp_path)
    a = store.create("g1", "manual", "device")
    store.create("g2", "manual", "device")
    store.end(a.id)
    store.tick()
    assert [e["group_id"] for e in _persisted(tmp_path)] == ["g2"]


def test_restore_persisted_without_a_known_clip_end_puts_the_volumes_back_at_once(tmp_path):
    # an entry from before the clip end was persisted: the volume restore covers the solo, and
    # there is no stop to send (an announcement cannot be stopped)
    _file(tmp_path).write_text(json.dumps(_old_snapshot()))
    store, made, _, _, _ = make(tmp_path)
    store.restore_persisted()
    assert made[0].calls == [("restore",)] and not _file(tmp_path).exists()
    assert made[0].closed == 1
    with pytest.raises(ControlError):
        store.get("old")


def test_restore_persisted_keeps_the_file_when_ma_is_down(tmp_path):
    _file(tmp_path).write_text(json.dumps(_old_snapshot(signal_running=False, soloed=None)))
    store, made, _, t, _ = make(tmp_path)
    attempts = {"n": 0}
    original = store._factory

    def factory():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise MaApiError("ma_unavailable", "Music Assistant is not connected")
        return original()

    store._factory = factory
    store.restore_persisted()
    assert _file(tmp_path).exists()
    t["now"] = 0.5
    store.tick()
    assert attempts["n"] == 1
    t["now"] = 2.0
    store.tick()
    assert not _file(tmp_path).exists() and made[0].calls == [("restore",)]


def test_a_persisted_group_that_is_gone_still_gets_its_volumes_restored(tmp_path):
    _file(tmp_path).write_text(json.dumps(_old_snapshot()))
    store, made, _, _, _ = make(tmp_path)
    original = store._factory

    def factory():
        ctl = original()

        def gone(group_id, member):
            raise ControlError("member_gone", "group g is not known")

        ctl.solo = gone
        return ctl

    store._factory = factory
    store.restore_persisted()
    assert made[0].calls == [("restore",)] and not _file(tmp_path).exists()


def test_an_unreadable_snapshot_file_is_removed(tmp_path):
    _file(tmp_path).write_text("{not json")
    store, made, _, _, _ = make(tmp_path)
    store.restore_persisted()
    assert not _file(tmp_path).exists() and made == []


def test_a_new_session_does_not_drop_a_snapshot_still_waiting_from_before_the_restart(tmp_path):
    _file(tmp_path).write_text(json.dumps(_old_snapshot(group_id="old-g")))
    store, _made, _, _, _ = make(tmp_path)
    original = store._factory
    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise MaApiError("ma_unavailable")
        return original()

    store._factory = factory
    store.restore_persisted()
    store.create("g", "manual", "device")
    assert {e["session_id"] for e in _persisted(tmp_path)} >= {"old"}


def test_shutdown_restores_live_sessions_and_releases_everything(tmp_path):
    store, made, _, _, host = make(tmp_path)
    s = store.create("g", "manual", "device")  # no clip playing
    store.shutdown()
    assert s.state == "cancelled" and ("restore",) in made[0].calls
    assert host.released == [s.id] and made[0].closed == 1 and not _file(tmp_path).exists()


def test_shutdown_while_the_clip_plays_silences_it_and_leaves_the_restore_to_the_next_boot(tmp_path):
    store, made, _, _, host = make(tmp_path)
    s = store.create("g", "semi", "device")
    store.shutdown()
    assert s.state == "restoring" and made[0].calls[-1] == ("stop",) and ("restore",) not in made[0].calls
    [entry] = _persisted(tmp_path)
    assert entry["silenced"] is True and entry["signal_ends_at"] == pytest.approx(WALL0 + CLIP_END)
    assert host.released == [s.id] and made[0].closed == 1


def test_shutdown_keeps_the_snapshot_when_the_restore_fails(tmp_path):
    store, made, _, _, host = make(tmp_path)
    s = store.create("g", "semi", "device")
    made[0].fail_restore = 99
    store.shutdown()
    assert s.state == "restoring" and _file(tmp_path).exists()  # the next boot restores it
    assert host.released == [s.id] and made[0].closed == 1


def test_publishing_happens_on_the_calling_thread_without_the_store_lock(tmp_path):
    # on_change runs under the session lock; it must not wait for the store's lock, which tick() holds
    # while it walks the sessions — otherwise a worker and the ticker deadlock.
    store, _, events, _, _ = make(tmp_path)
    s = store.create("g", "manual", "device")
    with store._lock:
        done = threading.Event()

        def worker():
            s.write_delay("a", 10)
            done.set()

        th = threading.Thread(target=worker)
        th.start()
        assert done.wait(2.0)
        th.join()
    assert events[-1]["members"][0]["delay_ms"] == 10


def test_the_ticker_thread_ticks_until_shutdown(tmp_path):
    store, _, _, _, _ = make(tmp_path)
    ticked = threading.Event()
    store.tick = ticked.set
    store.start_ticker(interval=0.01)
    assert ticked.wait(2.0)
    store.shutdown()
    assert store._ticker is None or not store._ticker.is_alive()


def test_get_store_publishes_one_envelope_to_the_attached_hub(tmp_path, monkeypatch):
    sent = []

    class Hub:
        def publish_threadsafe(self, event):
            sent.append(event)

    monkeypatch.setattr(store_mod, "_hub", Hub())
    store_mod._publish_to_hub({"id": "s1", "state": "measuring"})
    [event] = sent
    assert event["type"] == "sync" and event["v"] == 1 and event["data"] == {"id": "s1", "state": "measuring"}
    monkeypatch.setattr(store_mod, "_hub", None)
    store_mod._publish_to_hub({"id": "s1"})  # no hub yet: dropped quietly


def test_the_api_attaches_its_event_hub_while_it_runs():
    from tests.support.api_client import make_client

    client = make_client()
    with client:
        assert store_mod._hub is client.app.state.event_hub
    assert store_mod._hub is None


def _pending_store(tmp_path, *, group_id="g"):
    """A store whose persisted restore of *group_id* failed once (MA down) and waits for its backoff."""
    _file(tmp_path).write_text(json.dumps(_old_snapshot(group_id=group_id)))
    store, made, _events, t, _host = make(tmp_path)
    original = store._factory
    attempts = {"n": 0}

    def factory():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise MaApiError("ma_unavailable", "Music Assistant is not connected")
        return original()

    store._factory = factory
    store.restore_persisted()
    return store, made, t


def test_a_new_session_waits_for_the_pending_restore_of_its_group(tmp_path):
    store, _, _ = _pending_store(tmp_path)
    with pytest.raises(ControlError) as exc:
        store.create("g", "semi", "device")
    assert exc.value.code == "restore_pending"
    assert store.create("other", "manual", "device").state == "measuring"  # other groups are free


def test_pending_restores_are_listed_with_their_last_error_and_can_be_retried_now(tmp_path):
    store, made, _t = _pending_store(tmp_path)
    assert store.pending_for("g") == {"session_id": "old", "group_id": "g", "error": "ma_unavailable"}
    assert store.pending_for("other") is None
    assert [p["group_id"] for p in store.pending()] == ["g"]
    store.tick()  # t=0: still inside the 1 s backoff
    assert made == []
    assert store.retry_pending("g") is True and store.retry_pending("other") is False
    store.tick()  # tried at once
    assert store.pending_for("g") is None and made[0].calls[-1] == ("restore",)
    assert store.create("g", "manual", "device").state == "measuring"


def test_shutdown_without_music_assistant_makes_no_calls_and_keeps_the_snapshot(tmp_path):
    store, made, _, _, host = make(tmp_path)
    s = store.create("g", "semi", "device")
    before = list(made[0].calls)
    store.shutdown(restore=False)
    assert made[0].calls == before and _file(tmp_path).exists()  # the next boot restores it
    assert host.released == [s.id] and made[0].closed == 1


def test_shutdown_store_restores_only_when_music_assistant_is_connected(monkeypatch):
    from sendspin_bridge.services.music_assistant import ma_runtime_state

    seen = []
    monkeypatch.setattr(store_mod, "_store", type("S", (), {"shutdown": lambda self, restore: seen.append(restore)})())
    monkeypatch.setattr(ma_runtime_state, "is_ma_connected", lambda: False)
    store_mod.shutdown_store()
    monkeypatch.setattr(ma_runtime_state, "is_ma_connected", lambda: True)
    store_mod.shutdown_store()
    assert seen == [False, True]


def test_a_slow_membership_read_on_one_group_does_not_stall_another_groups_restore(tmp_path):
    store, made, _, _, _ = make(tmp_path)
    slow = store.create("g1", "manual", "device")
    other = store.create("g2", "manual", "device")
    release = threading.Event()
    reading = threading.Event()
    real_members = made[0].members

    def slow_members(group_id):
        reading.set()
        release.wait(5.0)
        return real_members(group_id)

    made[0].members = slow_members
    worker = threading.Thread(target=lambda: slow.skip("a"))
    worker.start()
    try:
        assert reading.wait(2.0)
        store.end(other.id)
        ticker = threading.Thread(target=store.tick)
        ticker.start()
        ticker.join(2.0)
        assert not ticker.is_alive() and other.state == "cancelled"
    finally:
        release.set()
        worker.join(5.0)


def test_the_snapshot_file_is_fsynced_before_it_replaces_the_old_one(tmp_path, monkeypatch):
    synced = []
    real_fsync = store_mod.os.fsync
    monkeypatch.setattr(store_mod.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    store, _, _, _, _ = make(tmp_path)
    store.create("g", "manual", "device")
    assert synced and not (tmp_path / "sync_snapshot.tmp").exists()


def test_an_unreadable_snapshot_is_kept_aside_and_a_leftover_temp_file_removed(tmp_path):
    _file(tmp_path).write_text("{not json")
    (tmp_path / "sync_snapshot.tmp").write_text("half")
    store, made, _, _, _ = make(tmp_path)
    store.restore_persisted()
    assert not _file(tmp_path).exists() and made == []
    assert (tmp_path / "sync_snapshot.json.corrupt").read_text() == "{not json"
    assert not (tmp_path / "sync_snapshot.tmp").exists()


def test_tests_never_point_the_store_singleton_at_the_real_config_dir():
    from pathlib import Path

    store = store_mod.get_store()
    assert store._dir() != Path("/config") and store._ticker is not None
    store_mod.reset_store()
    assert store_mod._store is None and not store._ticker.is_alive()


def test_a_create_before_the_boot_restore_is_refused_and_keeps_the_old_entry(tmp_path):
    # the API serves requests before Music Assistant is up and the restore thread has run
    _file(tmp_path).write_text(json.dumps(_old_snapshot()))
    store, _, _, _, _ = make(tmp_path)
    store.load_persisted()  # what the orchestrator does before the web server starts
    with pytest.raises(ControlError) as exc:
        store.create("g", "semi", "device")
    assert exc.value.code == "restore_pending"
    store.create("other", "manual", "device")
    assert {e["session_id"] for e in _persisted(tmp_path)} == {"old", s_id(store, "other")}


def test_a_store_that_was_never_told_to_load_still_keeps_the_entries_on_disk(tmp_path):
    _file(tmp_path).write_text(json.dumps(_old_snapshot()))
    store, _, _, _, _ = make(tmp_path)
    with pytest.raises(ControlError) as exc:
        store.create("g", "semi", "device")
    assert exc.value.code == "restore_pending"
    store.create("other", "manual", "device")
    assert "old" in {e["session_id"] for e in _persisted(tmp_path)}


def s_id(store, group_id):
    return store.active_for(group_id).id


def test_a_leftover_tmp_that_cannot_be_removed_does_not_break_every_create(tmp_path, caplog):
    stuck = tmp_path / "sync_snapshot.tmp"
    stuck.mkdir()  # unlink() on a directory raises an OSError
    (stuck / "x").write_text("x")
    _file(tmp_path).write_text(json.dumps(_old_snapshot()))
    store, *_ = make(tmp_path)
    store.load_persisted()
    assert [p["group_id"] for p in store.pending()] == ["g"]  # the snapshot file was still read
    s = store.create("other", "semi", "listening")
    assert s.group_id == "other"
    assert "sync_snapshot.tmp" in caplog.text


# -- stage 0, option A: the clip cannot be stopped, the restore waits for its end -------------------


def test_a_restore_waits_for_the_clip_to_end_without_an_error_or_backoff(tmp_path):
    store, made, _, t, host = make(tmp_path)
    s = store.create("g", "semi", "device")
    store.end(s.id)
    store.tick()
    ctl = made[0]
    assert s.state == "restoring" and s.restore_error is None and ctl.calls[-1] == ("stop",)  # silenced
    assert s.to_dict()["music_returns_in_s"] == pytest.approx(CLIP_END)
    t["now"] = CLIP_END - 1
    store.tick()
    assert s.state == "restoring" and ("restore",) not in ctl.calls and host.released == []
    t["now"] = CLIP_END
    store.tick()  # waiting is not a failed attempt: no backoff delays the restore after the clip
    assert s.state == "cancelled" and ctl.calls[-1] == ("restore",) and ctl.calls.count(("stop",)) == 1


def test_a_new_session_while_the_last_clip_plays_out_is_refused_with_signal_playing(tmp_path):
    store, _made, _, t, _ = make(tmp_path)
    s = store.create("g", "semi", "device")
    store.end(s.id)
    store.tick()
    with pytest.raises(ControlError) as exc:
        store.create("g", "manual", "device")
    assert exc.value.code == "signal_playing"
    t["now"] = CLIP_END
    store.tick()
    assert store.create("g", "manual", "device").state == "measuring"


def test_the_persisted_entry_says_when_the_clip_ends_and_whether_it_is_silenced(tmp_path):
    store, _made, _, t, _ = make(tmp_path)
    s = store.create("g", "semi", "device")
    [entry] = _persisted(tmp_path)
    assert entry["signal_ends_at"] == pytest.approx(WALL0 + CLIP_END) and entry["silenced"] is False
    t["now"] = 10.0
    store.end(s.id)
    store.tick()
    [entry] = _persisted(tmp_path)
    assert entry["signal_ends_at"] == pytest.approx(WALL0 + CLIP_END) and entry["silenced"] is True


def test_after_a_restart_the_restore_silences_waits_for_the_clip_end_then_restores(tmp_path):
    ends = WALL0 + 100.0
    _file(tmp_path).write_text(json.dumps(_old_snapshot(signal_ends_at=ends, silenced=False)))
    store, made, _, t, _ = make(tmp_path)
    store.restore_persisted()
    assert made[0].calls == [("stop",)] and _file(tmp_path).exists()  # never restored while the clip plays
    assert store.pending_for("g") == {"session_id": "old", "group_id": "g", "error": None}
    assert _persisted(tmp_path)[0]["silenced"] is True
    t["now"] = 99.0
    store.tick()
    assert len(made) == 1
    t["now"] = 100.0
    store.tick()
    assert made[1].calls == [("restore",)] and not _file(tmp_path).exists()


def test_after_a_restart_a_silenced_clip_is_not_silenced_again(tmp_path):
    _file(tmp_path).write_text(json.dumps(_old_snapshot(signal_ends_at=WALL0 + 50.0, silenced=True)))
    store, made, _, t, _ = make(tmp_path)
    store.restore_persisted()
    assert made == []  # nothing to do before the clip ends: no Music Assistant call at all
    t["now"] = 50.0
    store.tick()
    assert made[0].calls == [("restore",)]


def test_after_a_restart_a_clip_on_a_group_that_is_gone_is_still_waited_out(tmp_path):
    _file(tmp_path).write_text(json.dumps(_old_snapshot(signal_ends_at=WALL0 + 50.0, silenced=False)))
    store, made, _, t, _ = make(tmp_path)
    original = store._factory

    def factory():
        ctl = original()

        def gone(group_id):
            raise ControlError("member_gone", "group g is not known")

        ctl.stop_signal = gone
        return ctl

    store._factory = factory
    store.restore_persisted()
    assert store.pending_for("g")["error"] is None and _file(tmp_path).exists()
    t["now"] = 50.0
    store.tick()
    assert made[1].calls == [("restore",)] and not _file(tmp_path).exists()


def test_the_store_tick_starts_a_check_round_once_the_previous_clip_ended(tmp_path):
    store, made, _, t, _ = make(tmp_path)
    s = store.create("g", "semi", "device")
    t["now"] = SIGNAL_SETTLE_S
    s.record(synth("a", lag_s=0.0))
    s.record(synth("b", lag_s=0.030))
    s.propose()
    s.apply(verify=True)
    assert s.signal_waiting
    t["now"] = CLIP_END
    store.tick()
    assert not s.signal_waiting and [c for c in made[0].calls if c[0] == "start"][1:]
