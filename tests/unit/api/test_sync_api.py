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


def _measured_session(client) -> str:
    sid = client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
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
    ctl, store, events, _ = sync_env
    created = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"})
    assert created.status_code == 201
    sid = created.json()["id"]
    assert created.json()["state"] == "measuring" and created.json()["signal_running"] is True
    second = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"})
    assert second.status_code == 409 and second.json()["code"] == "session_active"
    for member, lag in (("a", 0.0), ("b", 0.03)):
        resp = _upload(api_client, sid, member, lag)
        assert resp.status_code == 200 and resp.json()["status"] == "ok"
    prop = api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    assert prop.status_code == 200
    assert {r["member"]: r["after_ms"] for r in prop.json()["rows"]} == {"a": 0, "b": 30}
    again = api_client.get(f"/api/v1/sync/sessions/{sid}/proposal")  # reading it twice is fine
    assert again.status_code == 200 and again.json() == prop.json()
    applied = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={})
    assert applied.status_code == 200 and applied.json()["state"] == "restoring"
    assert ctl.delays_now["b"] == 30
    assert events and events[-1]["id"] == sid and events[-1]["state"] == "restoring"
    assert api_client.delete(f"/api/v1/sync/sessions/{sid}").status_code == 204
    store.tick()
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["state"] == "done"  # DELETE kept the outcome


def test_side_controls_and_manual_live_write(api_client, sync_env):
    ctl = sync_env[0]
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"}).json()["id"]
    base = f"/api/v1/sync/sessions/{sid}"
    assert api_client.post(f"{base}/signal", json={"action": "start"}).json()["signal_running"] is True
    assert api_client.post(f"{base}/solo", json={"member": "a"}).json()["soloed"] == "a"
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
    assert api_client.post(f"{base}/signal", json={"action": "stop"}).json()["signal_running"] is False


def test_skip_and_semi_apply_with_verification(api_client, sync_env):
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
    base = f"/api/v1/sync/sessions/{sid}"
    assert _upload(api_client, sid, "a", 0.0).json()["status"] == "ok"
    assert _upload(api_client, sid, "b", 0.03).json()["status"] == "ok"
    skipped = api_client.post(f"{base}/skip", json={"member": "b"})
    assert skipped.status_code == 200 and skipped.json()["measurements"]["b"]["status"] == "skipped"
    assert _upload(api_client, sid, "b", 0.03).json()["status"] == "ok"
    api_client.post(f"{base}/proposal")
    verifying = api_client.post(f"{base}/apply", json={"rows": {"b": 25}, "verify": True})
    assert verifying.status_code == 200 and verifying.json()["state"] == "verifying"
    assert verifying.json()["round"] == 2 and verifying.json()["measurements"] == {}
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
    undo = api_client.post(f"/api/v1/sync/sessions/{sid}/undo")
    assert undo.status_code == 409 and undo.json()["code"] == "undo_expired"
    api_client.delete(f"/api/v1/sync/sessions/{sid}")
    late = api_client.post(f"/api/v1/sync/sessions/{sid}/solo", json={"member": "a"})
    assert late.status_code == 409 and late.json()["code"] == "restore_pending"


def test_auto_apply_keeps_an_undo(api_client, sync_env):
    ctl = sync_env[0]
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "auto"}).json()["id"]
    for member, lag in (("a", 0.0), ("b", 0.03)):
        _upload(api_client, sid, member, lag)
    api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    applied = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={}).json()
    assert applied["state"] == "verifying" and applied["undo_until_s"] == pytest.approx(30.0)
    undone = api_client.post(f"/api/v1/sync/sessions/{sid}/undo")
    assert undone.status_code == 200 and undone.json()["undo_until_s"] is None and ctl.delays_now["b"] == 0


def test_garbage_recordings_are_400_not_500(api_client, sync_env):
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
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
    entry = {
        "session_id": "old",
        "group_id": "g",
        "signal_running": True,
        "soloed": None,
        "snapshot": {"group_id": "g", "delays": {}, "volumes": {"a": 40}, "muted": {"a": False}, "playing": True},
    }
    (tmp_path / SNAPSHOT_FILE).write_text(json.dumps({"sessions": [entry]}))
    ctl.fail_restore = 1
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
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "auto"}).json()["id"]
    for member, lag in (("a", 0.0), ("b", 0.03)):
        _upload(api_client, sid, member, lag)
    before = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    read = api_client.get(f"/api/v1/sync/sessions/{sid}/proposal")
    assert read.status_code == 409 and read.json()["code"] == "no_measurement"
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json() == before  # still measuring
    api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    verifying = api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={}).json()  # auto: verifies
    assert verifying["state"] == "verifying"
    for member, lag in (("a", 0.0), ("b", 0.05)):  # residual above the refine threshold
        _upload(api_client, sid, member, lag)
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

    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
    monkeypatch.setattr(estimator, "estimate", lambda rec, **kw: Measurement(member=rec.member, status="ok"))
    params = {"member": "a", "start_time_s": clock, "sample_rate": 16000}
    resp = api_client.post(
        f"/api/v1/sync/sessions/{sid}/recordings", params=params, content=bytes(6 * 16000 * 2), headers=OCTET
    )
    assert resp.status_code == 400 and resp.json()["code"] == "recording_invalid"
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["measurements"] == {}
