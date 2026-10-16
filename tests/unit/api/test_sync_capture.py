"""The phone capture page: a link with a short-lived, unguessable per-session token that allows only
reading that session's capture state and uploading a recording for it."""

from __future__ import annotations

import json
import time

import pytest

from sendspin_bridge.application import sync_tuning as uc
from sendspin_bridge.sync_tuning import capture_tokens
from tests.support.audio_files import pcm_to_m4a
from tests.support.sync_sequence import sequence_recording
from tests.unit.api.test_sync_api import _ready, sync_env  # noqa: F401 (fixture)

OCTET = {"content-type": "application/octet-stream"}


@pytest.fixture
def capture(monkeypatch, sync_env):  # noqa: F811
    _ctl, _store, _events, clock = sync_env
    tokens = capture_tokens.CaptureTokens(clock=clock)
    monkeypatch.setattr(capture_tokens, "tokens", tokens)
    monkeypatch.setattr(uc, "_capture_base_url", lambda: "http://192.168.1.5:8080")

    def sleep(dt):  # the sequence runs on the test's clock, without waiting
        clock.now += dt

    monkeypatch.setattr(uc, "_sequence_sleep", sleep)
    return tokens, clock


def _link(client, sid, lang="en"):
    resp = client.post(f"/api/v1/sync/sessions/{sid}/capture-link", json={"lang": lang})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _play_sequence(client, sid, members=("a", "b")):
    resp = client.post(f"/api/v1/sync/sessions/{sid}/sequence", json={"members": list(members)})
    assert resp.status_code == 202, resp.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        seq = client.get(f"/api/v1/sync/sessions/{sid}").json()["sequence"]
        if seq and seq["phase"] in ("done", "failed"):
            return seq
        time.sleep(0.01)
    raise AssertionError("the sequence did not finish")


def test_the_link_points_at_the_bridge_with_an_unguessable_token(api_client, capture):
    sid = _ready(api_client)
    link = _link(api_client, sid, "ru")
    token = link["token"]
    assert len(token) >= 40 and link["url"] == f"http://192.168.1.5:8080/sync/capture/{token}?lang=ru"
    assert link["expires_in_s"] == pytest.approx(capture_tokens.CAPTURE_TTL_S)
    again = _link(api_client, sid)  # a new link revokes the old one
    assert again["token"] != token
    assert api_client.get(f"/api/v1/sync/capture/{token}").status_code == 404


def test_the_token_reads_the_capture_state_without_signing_in_and_nothing_else(tmp_config, capture, sync_env):  # noqa: F811
    from sendspin_bridge.config import hash_password
    from tests.support.api_client import make_client

    cfg = {"AUTH_ENABLED": True, "AUTH_PASSWORD_HASH": hash_password("pw-pw-pw-pw")}
    tmp_config.write_text(json.dumps(cfg))
    owner = make_client(cfg)
    csrf = owner.post("/api/v1/auth/session", json={"method": "password", "password": "pw-pw-pw-pw"}).json()[
        "csrf_token"
    ]
    owner.headers["X-CSRF-Token"] = csrf
    sid = _ready(owner)
    token = _link(owner, sid)["token"]

    phone = make_client(cfg)  # no session cookie, no password
    state = phone.get(f"/api/v1/sync/capture/{token}")
    assert state.status_code == 200
    body = state.json()
    assert body["group"] == "Downstairs" and [m["name"] for m in body["members"]] == ["A", "B"]
    assert body["sequence"] is None and body["clicks_playing"] is True
    assert "delay_ms" not in json.dumps(body) and sid not in json.dumps(body)
    # the token is not a credential anywhere else
    for path in (f"/api/v1/sync/sessions/{sid}", "/api/v1/sync/groups", "/api/v1/config"):
        assert phone.get(path).status_code == 401
        assert phone.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert phone.post(f"/api/v1/sync/sessions/{sid}/solo", json={"member": "a"}).status_code == 401
    assert phone.get("/api/v1/sync/capture/" + "x" * len(token)).status_code == 404


def test_the_token_expires(api_client, capture):
    _tokens, clock = capture
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    clock.now += capture_tokens.CAPTURE_TTL_S + 1
    assert api_client.get(f"/api/v1/sync/capture/{token}").status_code == 404
    assert api_client.post(f"/api/v1/sync/capture/{token}/recording", content=b"x", headers=OCTET).status_code == 404


def test_the_token_is_revoked_when_the_session_ends(api_client, capture, sync_env):  # noqa: F811
    _ctl, store, _events, _clock = sync_env
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    assert api_client.get(f"/api/v1/sync/capture/{token}").status_code == 200
    api_client.delete(f"/api/v1/sync/sessions/{sid}")
    assert api_client.get(f"/api/v1/sync/capture/{token}").status_code == 404  # at once, before the restore
    store.tick()
    assert capture[0].session_for(token) is None


def test_an_m4a_of_the_sequence_measures_every_member(api_client, capture):
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    seq = _play_sequence(api_client, sid)
    assert seq["phase"] == "done"
    state = api_client.get(f"/api/v1/sync/capture/{token}").json()
    assert state["sequence"]["phase"] == "done"
    pcm, _ = sequence_recording({"a": 0.0, "b": 0.030})
    upload = api_client.post(
        f"/api/v1/sync/capture/{token}/recording", content=pcm_to_m4a(pcm, 16000), headers={"content-type": "audio/mp4"}
    )
    assert upload.status_code == 200, upload.text
    assert {r["member"]: r["status"] for r in upload.json()["results"]} == {"a": "ok", "b": "ok"}
    session = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert {k: v["status"] for k, v in session["measurements"].items()} == {"a": "ok", "b": "ok"}
    rows = {r["member"]: r["after_ms"] for r in api_client.post(f"/api/v1/sync/sessions/{sid}/proposal").json()["rows"]}
    assert rows["b"] - rows["a"] == pytest.approx(30, abs=2)


def test_uploads_are_bounded_and_need_a_finished_sequence(api_client, capture):
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    early = api_client.post(f"/api/v1/sync/capture/{token}/recording", content=b"\0" * 100, headers=OCTET)
    assert early.status_code == 409 and early.json()["code"] == "no_sequence"
    _play_sequence(api_client, sid)
    big = api_client.post(
        f"/api/v1/sync/capture/{token}/recording", content=b"\0" * (uc.MAX_CAPTURE_BYTES + 1), headers=OCTET
    )
    assert big.status_code == 400 and big.json()["code"] == "recording_invalid"
    junk = api_client.post(f"/api/v1/sync/capture/{token}/recording", content=b"not audio" * 50, headers=OCTET)
    assert junk.status_code == 400 and junk.json()["code"] == "recording_invalid"


def test_the_capture_page_is_served_in_the_phones_language_with_its_own_strict_policy(api_client, capture):
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    page = api_client.get(f"/sync/capture/{token}", headers={"Accept-Language": "ru-RU,ru;q=0.9"})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert '<html lang="ru"' in page.text and "Запись" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'nonce-" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in page.text
    assert page.headers["referrer-policy"] == "no-referrer" and "no-store" in page.headers["cache-control"]
    assert 'name="viewport"' in page.text
    en = api_client.get(f"/sync/capture/{token}?lang=en", headers={"Accept-Language": "ru"})
    assert '<html lang="en"' in en.text
    gone = api_client.get("/sync/capture/" + "y" * 43)
    assert gone.status_code == 404 and token not in gone.text


def test_a_link_is_only_for_a_measuring_session(api_client, capture):
    sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "manual"}).json()["id"]
    resp = api_client.post(f"/api/v1/sync/sessions/{sid}/capture-link", json={})
    assert resp.status_code == 409 and resp.json()["code"] == "session_state"


def test_the_capture_page_wins_over_the_spa_catch_all(tmp_path, capture, sync_env):  # noqa: F811
    from tests.support.api_client import make_client

    spa = tmp_path / "spa"
    spa.mkdir()
    (spa / "index.html").write_text("<!doctype html><title>SPA</title>")
    client = make_client(spa_dir=spa)
    sid = _ready(client)
    token = _link(client, sid)["token"]
    page = client.get(f"/sync/capture/{token}")
    assert page.status_code == 200 and "SPA" not in page.text and "Measure with this phone" in page.text
