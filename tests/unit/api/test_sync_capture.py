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
    monkeypatch.setattr(uc, "capture_https_base", lambda: None)

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


# -- review fix round + live test feedback -------------------------------------------------------------


def test_the_phone_starts_the_sequence_itself_for_the_speakers_still_to_measure(api_client, capture):
    sid = _ready(api_client)
    api_client.post(f"/api/v1/sync/sessions/{sid}/skip", json={"member": "b"})
    token = _link(api_client, sid)["token"]
    resp = api_client.post(f"/api/v1/sync/capture/{token}/start", json={"mode": "recorder"})
    assert resp.status_code == 202, resp.text
    assert resp.headers["cache-control"] == "no-store"
    deadline = time.monotonic() + 5
    while api_client.get(f"/api/v1/sync/capture/{token}").json()["sequence"]["phase"] not in ("done", "failed"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    seq_ = api_client.get(f"/api/v1/sync/sessions/{sid}").json()["sequence"]
    assert seq_["members"] == ["a"] and seq_["lead_s"] == 10.0  # "b" was skipped; time to start the recorder
    live = api_client.post(f"/api/v1/sync/capture/{token}/start", json={"mode": "live"})
    assert live.status_code == 202
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["sequence"]["lead_s"] == 3.0


def test_the_phone_is_told_what_to_do_when_the_session_is_not_ready(api_client, capture, sync_env):  # noqa: F811
    ctl = sync_env[0]
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    ctl.clicker_ends()
    resp = api_client.post(f"/api/v1/sync/capture/{token}/start", json={"mode": "live"})
    assert resp.status_code == 409 and resp.json()["code"] == "clicker_stopped"
    assert resp.json()["detail"]  # a sentence the page can show


def test_the_capture_token_still_cannot_do_anything_else(api_client, capture, sync_env):  # noqa: F811
    from tests.support.api_client import make_client

    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    other = api_client.post("/api/v1/sync/sessions", json={"group_id": "g2", "mode": "semi"}).json()["id"]
    for path, body in (("finish", None), ("apply", {}), ("solo", {"member": "a"}), ("signal", {"action": "stop"})):
        assert api_client.post(f"/api/v1/sync/capture/{token}/{path}", json=body).status_code in (404, 405)
    assert api_client.post(f"/api/v1/sync/capture/{token}/start", json={"mode": "bogus"}).status_code == 422
    # the token is bound to its session: the other session is untouched
    api_client.post(f"/api/v1/sync/capture/{token}/start", json={"mode": "live"})
    assert api_client.get(f"/api/v1/sync/sessions/{other}").json()["sequence"] is None
    assert make_client().post(f"/api/v1/sync/capture/{'z' * 43}/start", json={"mode": "live"}).status_code == 404


def test_a_second_upload_while_one_is_measured_is_refused(api_client, capture, monkeypatch):
    import threading

    from sendspin_bridge.sync_tuning import audio_decode

    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    _play_sequence(api_client, sid)
    entered, release = threading.Event(), threading.Event()
    real = audio_decode.decode_to_mono

    def slow(data, **kw):
        entered.set()
        assert release.wait(5)
        return real(data, **kw)

    monkeypatch.setattr(audio_decode, "decode_to_mono", slow)
    pcm, _ = sequence_recording({"a": 0.0, "b": 0.03})
    body = pcm_to_m4a(pcm, 16000)
    first = {}
    t = threading.Thread(
        target=lambda: first.update(
            r=api_client.post(f"/api/v1/sync/capture/{token}/recording", content=body, headers=OCTET)
        )
    )
    t.start()
    assert entered.wait(5)
    second = api_client.post(f"/api/v1/sync/capture/{token}/recording", content=body, headers=OCTET)
    assert second.status_code == 409 and second.json()["code"] == "upload_running"
    release.set()
    t.join(10)
    assert first["r"].status_code == 200


def test_only_one_recording_is_decoded_at_a_time_across_sessions(api_client, capture, monkeypatch):
    from sendspin_bridge.application import sync_tuning

    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    _play_sequence(api_client, sid)
    assert sync_tuning._decode_gate.acquire(blocking=False)  # another session's upload is being decoded
    try:
        busy = api_client.post(f"/api/v1/sync/capture/{token}/recording", content=b"RIFF0000WAVE", headers=OCTET)
        assert busy.status_code == 409 and busy.json()["code"] == "upload_running"
    finally:
        sync_tuning._decode_gate.release()


def test_nothing_is_decoded_once_the_session_tunes_by_ear(api_client, capture, monkeypatch):
    from sendspin_bridge.sync_tuning import audio_decode

    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    _play_sequence(api_client, sid)
    pcm, _ = sequence_recording({"a": 0.0, "b": 0.03})
    api_client.post(f"/api/v1/sync/capture/{token}/recording", content=pcm_to_m4a(pcm, 16000), headers=OCTET)
    api_client.post(f"/api/v1/sync/sessions/{sid}/proposal")
    api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={})
    monkeypatch.setattr(audio_decode, "decode_to_mono", lambda *a, **k: pytest.fail("decoded"))
    late = api_client.post(f"/api/v1/sync/capture/{token}/recording", content=b"x" * 100, headers=OCTET)
    assert late.status_code == 409 and late.json()["code"] == "session_state"


def test_a_bad_token_is_refused_before_the_body_is_read(api_client, capture):
    resp = api_client.post(
        f"/api/v1/sync/capture/{'q' * 43}/recording",
        content=b"\0" * 1000,
        headers={**OCTET, "content-length": str(10**9)},
    )
    assert resp.status_code == 404


def test_the_state_carries_no_player_ids_and_is_not_cached(api_client, capture):
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    resp = api_client.get(f"/api/v1/sync/capture/{token}")
    assert resp.headers["cache-control"] == "no-store"
    assert all(set(m) == {"name", "status"} for m in resp.json()["members"])


def test_refused_uploads_are_logged_with_their_reason_but_never_the_token(api_client, capture, caplog):
    import logging

    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    with caplog.at_level(logging.INFO):
        api_client.post(f"/api/v1/sync/capture/{token}/recording", content=b"\0" * 100, headers=OCTET)
    # the bridge's own log (the test's HTTP client logs request URLs itself; uvicorn's access log is off)
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("sendspin"))
    assert "no_sequence" in text and token not in text


def test_an_unexpected_error_on_a_capture_route_never_logs_the_token(api_client, capture, monkeypatch, caplog):
    import logging

    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    monkeypatch.setattr(uc, "capture_state", lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    with caplog.at_level(logging.ERROR):
        assert api_client.get(f"/api/v1/sync/capture/{token}").status_code == 500
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("sendspin"))
    assert "/sync/capture/<token>" in text and token not in text


def test_the_link_prefers_https_when_the_capture_server_runs(api_client, capture, monkeypatch):
    monkeypatch.setattr(uc, "capture_https_base", lambda: "https://192.168.1.5:8443")
    sid = _ready(api_client)
    link = _link(api_client, sid, "ru")
    token = link["token"]
    assert link["url"] == f"https://192.168.1.5:8443/sync/capture/{token}?lang=ru" and link["secure"] is True
    assert link["http_url"] == f"http://192.168.1.5:8080/sync/capture/{token}?lang=ru"


def test_the_http_page_offers_the_secure_page_when_there_is_one(api_client, capture, monkeypatch):
    sid = _ready(api_client)
    token = _link(api_client, sid)["token"]
    assert 'id="https-link"' not in api_client.get(f"/sync/capture/{token}").text
    monkeypatch.setattr(uc, "capture_https_base", lambda: "https://192.168.1.5:8443")
    page = api_client.get(f"/sync/capture/{token}?lang=en").text
    assert f'href="https://192.168.1.5:8443/sync/capture/{token}?lang=en"' in page
