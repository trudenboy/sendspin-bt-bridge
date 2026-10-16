"""``/api/v1/sync/*`` over HTTP, against an in-memory group control and session store."""

from __future__ import annotations

import json

import pytest

from sendspin_bridge.sync_tuning import session_store
from sendspin_bridge.sync_tuning.session_store import SNAPSHOT_FILE, SessionStore
from sendspin_sync.ports import ControlError
from tests.support.sync_fakes import FakeControl, FakeHost
from tests.support.sync_synth import synth

OCTET = {"content-type": "application/octet-stream"}
GROUP = {"id": "g", "name": "Downstairs", "members": [{"id": m, "name": m.upper()} for m in ("a", "b", "cast")]}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def sync_env(monkeypatch, tmp_config, tmp_path):
    ctl = FakeControl()
    events: list[dict] = []
    clock = Clock()
    store = SessionStore(
        control_factory=lambda: ctl,
        host=FakeHost(),
        publish=events.append,
        snapshot_dir=lambda: tmp_path,
        clock=clock,
    )
    monkeypatch.setattr(session_store, "_store", store)
    monkeypatch.setattr("sendspin_bridge.application.sync_tuning._control", lambda: ctl)
    monkeypatch.setattr("sendspin_bridge.application.sync_tuning.get_ma_groups", lambda: [GROUP])
    return ctl, store, events, clock


def _upload(client, sid, member, lag):
    rec = synth(member, lag_s=lag)
    params = {"member": member, "start_time_s": rec.start_time_s, "sample_rate": rec.sample_rate}
    return client.post(f"/api/v1/sync/sessions/{sid}/recordings", params=params, content=rec.pcm, headers=OCTET)


def _ready(client, mode="semi") -> str:
    """A session whose group was stopped and whose clicker plays (the wizard's first steps)."""
    sid = client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": mode}).json()["id"]
    assert client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "stop"}).status_code == 200
    assert client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "start"}).status_code == 200
    return sid


def _measured_session(client) -> str:
    sid = _ready(client)
    for member, lag in (("a", 0.0), ("b", 0.03)):
        assert _upload(client, sid, member, lag).json()["status"] == "ok"
    return sid


# -- groups -----------------------------------------------------------------------------


def test_groups_list_members_with_origin_and_adjustability(api_client, sync_env):
    resp = api_client.get("/api/v1/sync/groups")
    assert resp.status_code == 200
    g = resp.json()[0]
    assert g["id"] == "g" and g["name"] == "Downstairs" and g["session_id"] is None
    assert g["restore_pending"] is None
    assert {m["ma_player_id"]: m["adjustable"] for m in g["members"]} == {"a": True, "b": True, "cast": False}
    assert {m["ma_player_id"]: m["origin"] for m in g["members"]} == {"a": "bridge", "b": "sendspin", "cast": "other"}


def test_a_group_reports_mute_support_availability_and_volume_per_member(api_client, sync_env):
    from dataclasses import replace

    ctl = sync_env[0]
    a, b, cast = ctl.member_list
    ctl.member_list = [
        replace(a, can_mute=True, available=True, volume=40, muted=False),
        replace(b, can_mute=False, available=True, volume=0, muted=None),
        replace(cast, can_mute=True, available=False, volume=None, muted=False),
    ]
    members = {m["ma_player_id"]: m for m in api_client.get("/api/v1/sync/groups/g").json()["members"]}
    assert (members["a"]["can_mute"], members["a"]["available"], members["a"]["volume"]) == (True, True, 40)
    assert (members["b"]["can_mute"], members["b"]["volume"], members["b"]["muted"]) == (False, 0, None)
    assert members["cast"]["available"] is False


def test_one_group_and_an_unknown_group(api_client, sync_env):
    assert api_client.get("/api/v1/sync/groups/g").json()["id"] == "g"
    missing = api_client.get("/api/v1/sync/groups/nope")
    assert missing.status_code == 404 and missing.json()["code"] == "not_found"


def test_a_group_under_tuning_names_its_session(api_client, sync_env):
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"}).json()["id"]
    assert api_client.get("/api/v1/sync/groups/g").json()["session_id"] == sid


def test_music_assistant_not_connected_is_503(api_client, sync_env, monkeypatch):
    from sendspin_bridge.sync_tuning.ma_api import MaApiError

    def down():
        raise MaApiError("ma_unavailable", "Music Assistant is not connected")

    monkeypatch.setattr("sendspin_bridge.application.sync_tuning._control", down)
    resp = api_client.get("/api/v1/sync/groups")
    assert resp.status_code == 503 and resp.json()["code"] == "ma_unavailable"


def test_one_group_failing_does_not_hide_the_others(api_client, sync_env, monkeypatch):
    ctl = sync_env[0]
    other = {"id": "h", "name": "Upstairs", "members": [{"id": "x", "name": "X"}]}
    monkeypatch.setattr("sendspin_bridge.application.sync_tuning.get_ma_groups", lambda: [GROUP, other])
    real = ctl.members

    def members(group_id):
        if group_id == "h":
            raise ControlError("member_gone", "group h is not known")
        return real(group_id)

    monkeypatch.setattr(ctl, "members", members)
    resp = api_client.get("/api/v1/sync/groups")
    assert resp.status_code == 200
    by_id = {g["id"]: g for g in resp.json()}
    assert by_id["g"]["error"] is None and len(by_id["g"]["members"]) == 3
    assert by_id["h"]["error"] == "member_gone" and by_id["h"]["members"] == []
    one = api_client.get("/api/v1/sync/groups/h")
    assert one.status_code == 409 and one.json()["code"] == "member_gone"


def test_manual_delay_write_refuses_a_non_adjustable_member(api_client, sync_env):
    ctl = sync_env[0]
    ok = api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"b": 250}})
    assert ok.status_code == 200 and ctl.delays_now["b"] == 250
    bad = api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"a": 10, "cast": 10}})
    assert bad.status_code == 409 and bad.json()["code"] == "not_adjustable"
    assert ctl.delays_now["a"] == 0  # nothing half-written
    gone = api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"zz": 10}})
    assert gone.status_code == 409 and gone.json()["code"] == "member_gone"
    assert api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"a": 6000}}).status_code == 422
    assert api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"a": -1}}).status_code == 422


def test_manual_delay_write_is_refused_while_the_group_is_tuned(api_client, sync_env):
    api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"})
    resp = api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"b": 250}})
    assert resp.status_code == 409 and resp.json()["code"] == "session_active"


# -- sessions ---------------------------------------------------------------------------


def test_session_flow_over_http(api_client, sync_env):
    ctl, store, events, _clock = sync_env
    created = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"})
    assert created.status_code == 201
    sid = created.json()["id"]
    base = f"/api/v1/sync/sessions/{sid}"
    # a new session reads the group and writes nothing: music still plays, no clicker yet
    body = created.json()
    assert body["state"] == "measuring" and body["group_state"] == "playing" and body["clicker_playing"] is False
    assert body["clicker_starts"] == 0 and body["muted"] == [] and body["volumes"] == {"a": 40, "b": 40, "cast": 20}
    assert ctl.calls == []
    second = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"})
    assert second.status_code == 409 and second.json()["code"] == "session_active"
    early = api_client.post(f"{base}/signal", json={"action": "start"})
    assert early.status_code == 409 and early.json()["code"] == "group_playing"
    stopped = api_client.post(f"{base}/signal", json={"action": "stop"}).json()
    assert stopped["group_state"] == "idle"
    started = api_client.post(f"{base}/signal", json={"action": "start"}).json()
    assert started["clicker_playing"] is True and started["clicker_starts"] == 1
    for member, lag in (("a", 0.0), ("b", 0.03)):
        assert api_client.post(f"{base}/solo", json={"member": member}).status_code == 200
        resp = _upload(api_client, sid, member, lag)
        assert resp.status_code == 200 and resp.json()["status"] == "ok"
    prop = api_client.post(f"{base}/proposal")
    assert prop.status_code == 200
    assert {r["member"]: r["after_ms"] for r in prop.json()["rows"]} == {"a": 0, "b": 30}
    again = api_client.get(f"{base}/proposal")  # reading it twice is fine
    assert again.status_code == 200 and again.json() == prop.json()
    applied = api_client.post(f"{base}/apply", json={})
    # applied, and the session goes on as the manual editor while the clicks play
    assert applied.status_code == 200 and applied.json()["state"] == "tuning"
    assert ctl.delays_now["b"] == 30 and applied.json()["clicker_playing"] is True
    assert api_client.put(f"{base}/delays/b", json={"ms": 34}).json()["state"] == "tuning"
    finished = api_client.post(f"{base}/finish")
    assert finished.status_code == 200 and finished.json()["state"] == "restoring"
    assert events and events[-1]["id"] == sid and events[-1]["state"] == "restoring"
    assert api_client.delete(base).status_code == 204
    store.tick()
    assert api_client.get(base).json()["state"] == "done"  # DELETE kept the outcome
    assert not any(ctl.muted_now.values())
    assert ctl.calls[-3:] == [("stop_group",), ("mute", "a", False), ("mute", "cast", False)]  # stop, then unmute


def test_side_controls_and_manual_live_write(api_client, sync_env):
    ctl = sync_env[0]
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"}).json()["id"]
    base = f"/api/v1/sync/sessions/{sid}"
    stopped = api_client.post(f"{base}/solo", json={"member": "a"})
    assert stopped.status_code == 409 and stopped.json()["code"] == "clicker_stopped"
    api_client.post(f"{base}/signal", json={"action": "stop"})
    assert api_client.post(f"{base}/signal", json={"action": "start"}).json()["clicker_starts"] == 1
    assert api_client.get(base).json()["clicker_playing"] is True
    soloed = api_client.post(f"{base}/solo", json={"member": "a"}).json()
    assert soloed["soloed"] == "a" and soloed["muted"] == ["b", "cast"]
    assert api_client.post(f"{base}/solo", json={"member": None}).json()["soloed"] is None
    assert api_client.post(f"{base}/ab", json={"a": "b", "b": "a"}).json()["soloed"] == "b"
    assert api_client.post(f"{base}/reference", json={"member": "b"}).json()["reference"] == "b"
    written = api_client.put(f"{base}/delays/a", json={"ms": 75})
    assert written.status_code == 200 and ctl.delays_now["a"] == 75
    assert {m["ma_player_id"]: m["delay_ms"] for m in written.json()["members"]}["a"] == 75
    assert api_client.put(f"{base}/delays/a", json={"ms": 5001}).status_code == 422
    bad = api_client.put(f"{base}/delays/cast", json={"ms": 10})
    assert bad.status_code == 409 and bad.json()["code"] == "not_adjustable"
    gone = api_client.post(f"{base}/solo", json={"member": "zz"})
    assert gone.status_code == 409 and gone.json()["code"] == "member_gone"
    after = api_client.post(f"{base}/signal", json={"action": "stop"}).json()
    assert after["clicker_playing"] is False and after["group_state"] == "idle"


def test_member_volumes_are_set_through_the_session(api_client, sync_env):
    ctl = sync_env[0]
    sid = _ready(api_client, "manual")
    base = f"/api/v1/sync/sessions/{sid}"
    resp = api_client.put(f"{base}/volumes/b", json={"level": 70})
    assert resp.status_code == 200 and resp.json()["volumes"]["b"] == 70 and ctl.volumes["b"] == 70
    assert resp.json()["volumes_before"] == {"b": 40}  # put back when the session ends
    assert resp.json()["clicker_left_s"] == pytest.approx(180.0)
    assert api_client.put(f"{base}/volumes/b", json={"level": 101}).status_code == 422
    gone = api_client.put(f"{base}/volumes/zz", json={"level": 10})
    assert gone.status_code == 409 and gone.json()["code"] == "member_gone"


def test_a_member_that_cannot_be_muted_is_named_in_the_problem(api_client, sync_env):
    ctl = sync_env[0]
    ctl.no_mute = {"cast"}
    sid = _ready(api_client)
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/solo", json={"member": "a"})
    assert resp.status_code == 409
    problem = resp.json()
    assert problem["code"] == "sync_mute_unsupported" and problem["member"] == "cast"
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["muted"] == []


def test_a_clicker_that_stopped_answers_clicker_stopped_on_record(api_client, sync_env):
    ctl = sync_env[0]
    sid = _ready(api_client)
    ctl.clicker_ends()
    resp = _upload(api_client, sid, "a", 0.0)
    assert resp.status_code == 409 and resp.json()["code"] == "clicker_stopped"
    assert api_client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "start"}).status_code == 200
    assert _upload(api_client, sid, "a", 0.0).json()["status"] == "ok"


def test_skip_and_semi_apply_of_operator_rows(api_client, sync_env):
    sid = _ready(api_client)
    base = f"/api/v1/sync/sessions/{sid}"
    assert _upload(api_client, sid, "a", 0.0).json()["status"] == "ok"
    assert _upload(api_client, sid, "b", 0.03).json()["status"] == "ok"
    skipped = api_client.post(f"{base}/skip", json={"member": "b"})
    assert skipped.status_code == 200 and skipped.json()["measurements"]["b"]["status"] == "skipped"
    assert _upload(api_client, sid, "b", 0.03).json()["status"] == "ok"
    api_client.post(f"{base}/proposal")
    applied = api_client.post(f"{base}/apply", json={"rows": {"b": 25}})
    assert applied.status_code == 200 and applied.json()["state"] == "tuning"
    assert sync_env[0].delays_now["b"] == 25


def test_apply_rows_are_bounded(api_client, sync_env):
    sid = _measured_session(api_client)
    api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={"rows": {"b": 9000}})
    assert resp.status_code == 422
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["state"] == "proposed"


def test_errors_of_the_session_state_machine(api_client, sync_env):
    missing = api_client.get("/api/v1/sync/sessions/nope")
    assert missing.status_code == 404 and missing.json()["code"] == "session_expired"
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
    early = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={})
    assert early.status_code == 409 and early.json()["code"] == "session_state"
    nothing = api_client.get(f"/api/v1/sync/sessions/{sid}/proposal")
    assert nothing.status_code == 409 and nothing.json()["code"] == "no_measurement"
    computed = api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    assert computed.status_code == 409 and computed.json()["code"] == "no_measurement"
    early_finish = api_client.post(f"/api/v1/sync/sessions/{sid}/finish")
    assert early_finish.status_code == 200  # a session may be finished at any point while it runs
    late = api_client.post(f"/api/v1/sync/sessions/{sid}/solo", json={"member": "a"})
    assert late.status_code == 409 and late.json()["code"] == "restore_pending"


def test_the_automatic_mode_and_its_endpoints_are_gone(api_client, sync_env):
    """UX round (user decision 2026-10-16): measuring always ends in the operator's hands."""
    auto = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "auto"})
    assert auto.status_code == 422
    sid = _ready(api_client)
    for path in ("undo", "verify"):
        assert api_client.post(f"/api/v1/sync/sessions/{sid}/{path}").status_code in (404, 405)
    body = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert "round" not in body and "undo_until_s" not in body


def test_finish_keeps_the_delays_and_ends_done(api_client, sync_env):
    ctl, store, _events, _clock = sync_env
    sid = _ready(api_client, "manual")
    base = f"/api/v1/sync/sessions/{sid}"
    assert api_client.get(base).json()["state"] == "tuning"
    api_client.put(f"{base}/delays/b", json={"ms": 80})
    assert api_client.post(f"{base}/finish").json()["state"] == "restoring"
    store.tick()
    assert api_client.get(base).json()["state"] == "done" and ctl.delays_now["b"] == 80
    again = api_client.post(f"{base}/finish")
    assert again.status_code == 409 and again.json()["code"] == "session_state"


def test_garbage_recordings_are_400_not_500(api_client, sync_env):
    sid = _ready(api_client)
    url = f"/api/v1/sync/sessions/{sid}/recordings"
    for params, body in (
        ({"member": "a", "start_time_s": 0, "sample_rate": 7000}, bytes(32000)),
        ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, bytes(4801)),
        ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, bytes(2_100_000)),
        ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, bytes(10 * 1024 * 1024)),
        ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, bytes(600)),
        ({"member": "a", "start_time_s": 0, "sample_rate": 16000}, b""),
        ({"member": "a", "start_time_s": "nan", "sample_rate": 16000}, synth("a", lag_s=0.0).pcm),
        ({"member": "a", "start_time_s": "inf", "sample_rate": 16000}, synth("a", lag_s=0.0).pcm),
        ({"member": "a", "start_time_s": "-inf", "sample_rate": 16000}, synth("a", lag_s=0.0).pcm),
    ):
        resp = api_client.post(url, params=params, content=body, headers=OCTET)
        assert resp.status_code == 400 and resp.json()["code"] == "recording_invalid", (params, len(body))
    silent = api_client.post(
        url,
        params={"member": "a", "start_time_s": 0, "sample_rate": 16000},
        content=bytes(6 * 16000 * 2),
        headers=OCTET,
    )
    assert silent.status_code == 200 and silent.json()["status"] == "not_heard"
    gone = api_client.post(
        url,
        params={"member": "zz", "start_time_s": 0, "sample_rate": 16000},
        content=bytes(6 * 16000 * 2),
        headers=OCTET,
    )
    assert gone.status_code == 409 and gone.json()["code"] == "member_gone"
    session = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert session["state"] == "measuring"  # the session goes on
    assert _upload(api_client, sid, "a", 0.0).json()["status"] == "ok"


def test_forbidden_apply_keeps_the_proposal(api_client, sync_env, monkeypatch):
    ctl = sync_env[0]
    sid = _measured_session(api_client)
    proposal = api_client.post(f"/api/v1/sync/sessions/{sid}/proposal").json()

    def boom(member, ms):
        raise ControlError("ma_forbidden", "scope")

    monkeypatch.setattr(ctl, "set_delay", boom)
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={})
    assert resp.status_code == 403 and resp.json()["code"] == "ma_forbidden"
    again = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert again["state"] == "proposed" and again["proposal"] == proposal
    assert ctl.delays_now == {"a": 0, "b": 0}


# -- restores left from before a restart -----------------------------------------------------


def test_a_pending_restore_is_shown_and_can_be_retried(api_client, sync_env, tmp_path):
    ctl, store, _, _ = sync_env
    entry = {"session_id": "old", "group_id": "g", "muted": ["b"], "clicker_url": None}
    (tmp_path / SNAPSHOT_FILE).write_text(json.dumps({"sessions": [entry]}))
    ctl.muted_now["b"] = True  # what the session left behind
    ctl.fail_unmute = 1
    store.load_persisted()  # the orchestrator does this before the API serves
    store.tick()  # first attempt fails, then backs off
    group = api_client.get("/api/v1/sync/groups/g").json()
    pending = {"session_id": "old", "group_id": "g", "error": "ma_unavailable"}
    assert group["restore_pending"] == pending
    assert api_client.get("/api/v1/sync/restores").json() == [pending]  # readable without Music Assistant
    blocked = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"})
    assert blocked.status_code == 409 and blocked.json()["code"] == "restore_pending"
    retried = api_client.post("/api/v1/sync/groups/g/restore/retry")
    assert retried.status_code == 202 and retried.json() == pending
    store.tick()  # no time passed: only the retry makes it run now
    assert api_client.get("/api/v1/sync/groups/g").json()["restore_pending"] is None
    assert api_client.get("/api/v1/sync/restores").json() == []
    nothing = api_client.post("/api/v1/sync/groups/g/restore/retry")
    assert nothing.status_code == 404 and nothing.json()["code"] == "not_found"


# -- profiles ---------------------------------------------------------------------------


def test_profiles_crud_apply_and_capture(api_client, sync_env):
    ctl = sync_env[0]
    ctl.delays_now = {"a": 120, "b": 0}
    cap = api_client.post("/api/v1/sync/groups/g/capture", json={"name": "Evening", "notes": "sofa"})
    assert cap.status_code == 201
    assert cap.json()["delays"] == {"a": 120, "b": 0} and cap.json()["notes"] == "sofa"
    assert cap.json()["kind"] == "device" and cap.json()["group_id"] == "g" and cap.json()["created_at"]
    pid = cap.json()["id"]
    assert [p["name"] for p in api_client.get("/api/v1/sync/profiles", params={"group_id": "g"}).json()] == ["Evening"]
    patched = api_client.patch(f"/api/v1/sync/profiles/{pid}", json={"name": "Night"})
    assert patched.json()["name"] == "Night" and patched.json()["notes"] == "sofa"
    ctl.member_list = [m for m in ctl.member_list if m.ma_player_id != "b"]
    ctl.delays_now = {"a": 0}
    applied = api_client.post(f"/api/v1/sync/profiles/{pid}/apply").json()
    assert applied == {"applied": {"a": 120}, "skipped": ["b"]} and ctl.delays_now["a"] == 120
    assert api_client.delete(f"/api/v1/sync/profiles/{pid}").status_code == 204
    assert api_client.get("/api/v1/sync/profiles", params={"group_id": "g"}).json() == []
    for resp in (
        api_client.delete(f"/api/v1/sync/profiles/{pid}"),
        api_client.patch(f"/api/v1/sync/profiles/{pid}", json={"name": "x"}),
        api_client.post(f"/api/v1/sync/profiles/{pid}/apply"),
    ):
        assert resp.status_code == 404 and resp.json()["code"] == "not_found"


def test_profile_create_validates_delays(api_client, sync_env):
    made = api_client.post(
        "/api/v1/sync/profiles", json={"group_id": "g", "name": "Party", "kind": "position", "delays": {"a": 10}}
    )
    assert made.status_code == 201 and made.json()["kind"] == "position" and made.json()["delays"] == {"a": 10}
    bad = api_client.post("/api/v1/sync/profiles", json={"group_id": "g", "name": "X", "delays": {"a": 5001}})
    assert bad.status_code == 422


def test_profile_apply_is_refused_while_the_group_is_tuned(api_client, sync_env):
    pid = api_client.post("/api/v1/sync/profiles", json={"group_id": "g", "name": "P", "delays": {"a": 10}}).json()[
        "id"
    ]
    api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"})
    resp = api_client.post(f"/api/v1/sync/profiles/{pid}/apply")
    assert resp.status_code == 409 and resp.json()["code"] == "session_active"


# -- security ---------------------------------------------------------------------------


def test_sync_routes_need_credentials_when_auth_is_on(tmp_config):
    from tests.support.api_client import make_client

    client = make_client({"AUTH_ENABLED": True})
    assert client.get("/api/v1/sync/groups").status_code == 401
    assert client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).status_code == 401
    assert client.post("/api/v1/sync/sessions/x/recordings", content=b"\0\0", headers=OCTET).status_code == 401


def test_session_cookie_writes_need_the_csrf_token(tmp_config, sync_env, monkeypatch):
    from sendspin_bridge.config import hash_password
    from tests.support.api_client import make_client

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    cfg = {"AUTH_ENABLED": True, "AUTH_PASSWORD_HASH": hash_password("pw-pw-pw-pw")}
    tmp_config.write_text(json.dumps(cfg))
    client = make_client(cfg)
    signed = client.post("/api/v1/auth/session", json={"method": "password", "password": "pw-pw-pw-pw"})
    csrf = signed.json()["csrf_token"]
    assert client.get("/api/v1/sync/groups").status_code == 200
    refused = client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"})
    assert refused.status_code == 403 and refused.json()["code"] == "csrf_failed"
    rec = client.post(
        "/api/v1/sync/sessions/x/recordings",
        params={"member": "a", "start_time_s": 0, "sample_rate": 16000},
        content=bytes(4),
        headers=OCTET,
    )
    assert rec.status_code == 403 and rec.json()["code"] == "csrf_failed"
    ok = client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 201


def test_the_upload_body_is_documented_as_binary(api_client):
    spec = api_client.get("/api/v1/openapi.json").json()
    op = spec["paths"]["/api/v1/sync/sessions/{session_id}/recordings"]["post"]
    assert op["requestBody"]["content"]["application/octet-stream"]["schema"]["format"] == "binary"
    assert {p["name"] for p in op["parameters"]} >= {"member", "start_time_s", "sample_rate"}


# -- review fixes: nothing half-applied, GET is read-only -----------------------------------


def test_apply_with_a_non_adjustable_row_writes_nothing(api_client, sync_env):
    ctl = sync_env[0]
    sid = _measured_session(api_client)
    proposal = api_client.post(f"/api/v1/sync/sessions/{sid}/proposal").json()
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={"rows": {"b": 25, "cast": 10}})
    assert resp.status_code == 409 and resp.json()["code"] == "not_adjustable"
    assert ctl.delays_now == {"a": 0, "b": 0}
    session = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert session["state"] == "proposed" and session["proposal"] == proposal
    assert {m["ma_player_id"]: m["delay_ms"] for m in session["members"]}["b"] == 0


def test_apply_failing_on_the_second_member_puts_the_first_back(api_client, sync_env, monkeypatch):
    ctl = sync_env[0]
    sid = _measured_session(api_client)
    api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    real = ctl.set_delay

    def flaky(member, ms):
        if member.ma_player_id == "b":
            raise ControlError("ma_unavailable", "gone")
        real(member, ms)

    monkeypatch.setattr(ctl, "set_delay", flaky)
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={"rows": {"a": 10, "b": 20}})
    assert resp.status_code == 503 and resp.json()["code"] == "ma_unavailable"
    assert ctl.delays_now == {"a": 0, "b": 0}
    session = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert session["state"] == "proposed" and session["proposal"]["rows"]
    assert {m["ma_player_id"]: m["delay_ms"] for m in session["members"]} == {"a": 0, "b": 0, "cast": None}


def test_get_proposal_never_changes_the_session(api_client, sync_env):
    ctl = sync_env[0]
    sid = _ready(api_client)
    for member, lag in (("a", 0.0), ("b", 0.03)):
        _upload(api_client, sid, member, lag)
    before = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    read = api_client.get(f"/api/v1/sync/sessions/{sid}/proposal")
    assert read.status_code == 409 and read.json()["code"] == "no_measurement"
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json() == before  # still measuring
    api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    writes = list(ctl.calls)
    snapshot = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert api_client.get(f"/api/v1/sync/sessions/{sid}/proposal").status_code == 200
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json() == snapshot
    assert ctl.calls == writes  # no delay written by a GET


def test_computing_a_proposal_is_a_write_that_needs_csrf_or_same_site(tmp_config, sync_env, monkeypatch):
    from sendspin_bridge.config import hash_password
    from tests.support.api_client import make_client

    anonymous = make_client()  # auth off: cross-site writes are refused by Sec-Fetch-Site / Origin
    sid = _measured_session(anonymous)
    cross = anonymous.post(f"/api/v1/sync/sessions/{sid}/proposal", headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403
    assert anonymous.get(f"/api/v1/sync/sessions/{sid}").json()["state"] == "measuring"

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    cfg = {"AUTH_ENABLED": True, "AUTH_PASSWORD_HASH": hash_password("pw-pw-pw-pw")}
    tmp_config.write_text(json.dumps(cfg))
    client = make_client(cfg)
    csrf = client.post("/api/v1/auth/session", json={"method": "password", "password": "pw-pw-pw-pw"}).json()[
        "csrf_token"
    ]
    refused = client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    assert refused.status_code == 403 and refused.json()["code"] == "csrf_failed"
    ok = client.post(f"/api/v1/sync/sessions/{sid}/proposal", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200 and ok.json()["rows"]


def test_group_write_failing_midway_reports_what_was_applied(api_client, sync_env, monkeypatch):
    ctl = sync_env[0]
    real = ctl.set_delay

    def flaky(member, ms):
        if member.ma_player_id == "b":
            raise ControlError("ma_unavailable", "gone")
        real(member, ms)

    monkeypatch.setattr(ctl, "set_delay", flaky)
    resp = api_client.put("/api/v1/sync/groups/g/delays", json={"delays": {"a": 10, "b": 20}})
    assert resp.status_code == 503 and resp.json()["code"] == "ma_unavailable"
    assert resp.json()["applied"] == {"a": 10}
    pid = api_client.post(
        "/api/v1/sync/profiles", json={"group_id": "g", "name": "P", "delays": {"a": 30, "b": 40}}
    ).json()["id"]
    applied = api_client.post(f"/api/v1/sync/profiles/{pid}/apply")
    assert applied.status_code == 503 and applied.json()["applied"] == {"a": 30}


@pytest.mark.parametrize("clock", ["nan", "inf", "-inf"])
def test_a_non_finite_clock_is_refused_at_the_boundary(api_client, sync_env, monkeypatch, clock):
    from sendspin_sync import estimator
    from sendspin_sync.models import Measurement

    sid = _ready(api_client)
    monkeypatch.setattr(estimator, "estimate", lambda rec, **kw: Measurement(member=rec.member, status="ok"))
    params = {"member": "a", "start_time_s": clock, "sample_rate": 16000}
    resp = api_client.post(
        f"/api/v1/sync/sessions/{sid}/recordings", params=params, content=bytes(6 * 16000 * 2), headers=OCTET
    )
    assert resp.status_code == 400 and resp.json()["code"] == "recording_invalid"
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["measurements"] == {}


def test_a_group_shows_when_each_member_was_last_measured(api_client, sync_env):
    from datetime import UTC, datetime

    _, store, _, _ = sync_env
    before = datetime.now(UTC).replace(microsecond=0)
    sid = _ready(api_client)
    assert _upload(api_client, sid, "a", 0.0).json()["status"] == "ok"
    silence = api_client.post(
        f"/api/v1/sync/sessions/{sid}/recordings",
        params={"member": "b", "start_time_s": 0.0, "sample_rate": 48000},
        content=bytes(48000 * 2 * 8),
        headers=OCTET,
    )
    assert silence.status_code == 200 and silence.json()["status"] != "ok"
    during = {
        m["ma_player_id"]: m["last_measured_at"] for m in api_client.get("/api/v1/sync/groups/g").json()["members"]
    }
    assert during["b"] is None and during["cast"] is None  # a failed measurement is not a measurement
    assert datetime.fromisoformat(during["a"]) >= before
    assert api_client.delete(f"/api/v1/sync/sessions/{sid}").status_code == 204
    store.tick()
    assert store.active_for("g") is None
    listed = api_client.get("/api/v1/sync/groups").json()[0]["members"]
    assert {m["ma_player_id"]: m["last_measured_at"] for m in listed}["a"] == during["a"]  # kept after the session


def test_session_less_requests_use_the_shared_music_assistant_connection(monkeypatch):
    from sendspin_bridge.application import sync_tuning as uc
    from sendspin_bridge.sync_tuning import ma_api

    sentinel = object()
    monkeypatch.setattr(ma_api, "shared_ma_api", lambda: sentinel)
    assert uc._control()._api is sentinel and uc._control()._api is sentinel


def test_stop_clicker_never_stops_music_someone_started(api_client, sync_env):
    ctl = sync_env[0]
    sid = _ready(api_client, "manual")
    ctl.group_state, ctl.media_uri = "playing", "library://track/9"  # music started from MA meanwhile
    ctl.calls.clear()
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "stop_clicker"})
    assert resp.status_code == 200 and ctl.calls == []
    api_client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "stop"})
    api_client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "start"})
    ctl.calls.clear()
    api_client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "stop_clicker"})
    assert ctl.calls == [("stop_group",)]


def test_reset_puts_back_every_delay_this_session_wrote(api_client, sync_env):
    ctl = sync_env[0]
    sid = _measured_session(api_client)
    base = f"/api/v1/sync/sessions/{sid}"
    api_client.post(f"{base}/proposal")
    api_client.post(f"{base}/apply", json={})
    api_client.put(f"{base}/delays/a", json={"ms": 15})  # an edit of a member the apply did not move
    resp = api_client.post(f"{base}/reset")
    assert resp.status_code == 200 and resp.json()["state"] == "tuning"
    assert ctl.delays_now == {"a": 0, "b": 0}


def test_manual_tuning_picks_its_listening_sound_over_http(api_client, sync_env):
    ctl = sync_env[0]
    sid = _ready(api_client, mode="manual")
    base = f"/api/v1/sync/sessions/{sid}"
    assert api_client.get(base).json()["sound"] == "clicks"
    assert ctl.calls[-1] == ("clicker", f"http://bridge/api/v1/sync/signal/{sid}/clicks.wav")
    api_client.post(f"{base}/solo", json={"member": "a"})
    switched = api_client.put(f"{base}/sound", json={"sound": "noise"})
    assert switched.status_code == 200
    body = switched.json()
    assert body["sound"] == "noise" and body["clicker_playing"] is True and body["soloed"] == "a"
    assert ctl.calls[-1] == ("clicker", f"http://bridge/api/v1/sync/signal/{sid}/noise.wav")
    assert api_client.put(f"{base}/sound", json={"sound": "music"}).status_code == 422


def test_the_measurement_refuses_a_listening_sound(api_client, sync_env):
    sid = _ready(api_client)
    resp = api_client.put(f"/api/v1/sync/sessions/{sid}/sound", json={"sound": "accent"})
    assert resp.status_code == 409 and resp.json()["code"] == "session_state"
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["sound"] is None


def test_after_a_semi_automatic_apply_the_editor_has_the_listening_sounds(api_client, sync_env):
    ctl = sync_env[0]
    sid = _measured_session(api_client)
    base = f"/api/v1/sync/sessions/{sid}"
    assert api_client.post(f"{base}/proposal").status_code == 200
    proposed = api_client.put(f"{base}/sound", json={"sound": "clicks"})
    assert proposed.status_code == 409 and proposed.json()["code"] == "session_state"
    applied = api_client.post(f"{base}/apply", json={}).json()
    assert applied["state"] == "tuning" and applied["sound"] == "clicks"
    assert ctl.calls[-1] == ("clicker", f"http://bridge/api/v1/sync/signal/{sid}/clicks.wav")
    assert api_client.put(f"{base}/sound", json={"sound": "accent"}).json()["sound"] == "accent"
    assert ctl.calls[-1] == ("clicker", f"http://bridge/api/v1/sync/signal/{sid}/accent.wav")


def test_a_sound_file_that_cannot_be_written_is_a_clear_problem(api_client, sync_env):
    store = sync_env[1]
    sid = _ready(api_client, mode="manual")

    def full(track="measure"):
        raise ControlError("disk_full", "No space left for the sound file")

    store._host.prepare = full
    resp = api_client.put(f"/api/v1/sync/sessions/{sid}/sound", json={"sound": "noise"})
    assert resp.status_code == 507 and resp.json()["code"] == "disk_full"
