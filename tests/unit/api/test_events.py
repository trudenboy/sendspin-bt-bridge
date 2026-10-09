"""``/events`` (SSE) and ``/events/ws``: one envelope, status/job/log, same auth as the API."""

from __future__ import annotations

import asyncio
import json

import pytest

import sendspin_bridge.api.events as events
from sendspin_bridge.application.jobs import jobs
from tests.support.api_client import make_client


@pytest.fixture(autouse=True)
def _standalone(monkeypatch, tmp_config):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)


def _sse_events(body: str) -> list[dict]:
    found = []
    for block in body.split("\n\n"):
        data = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            found.append(json.loads("".join(data)))
    return found


# -- the hub ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listeners_get_only_the_types_they_asked_for():
    hub = events.EventHub(auth_enabled=False)
    async with hub.listen(frozenset({"job"})) as jobs_only, hub.listen(frozenset({"status", "job"})) as both:
        hub._broadcast(events.envelope("status", {"n": 1}))
        hub._broadcast(events.envelope("job", {"id": "j"}))
        assert [e["type"] for e in _drain(jobs_only)] == ["job"]
        assert [e["type"] for e in _drain(both)] == ["status", "job"]


@pytest.mark.asyncio
async def test_a_slow_listener_drops_its_oldest_events():
    hub = events.EventHub(auth_enabled=False)
    async with hub.listen(frozenset({"job"})) as listener:
        for n in range(events._QUEUE_SIZE + 5):
            hub._broadcast(events.envelope("job", {"n": n}))
        received = [e["data"]["n"] for e in _drain(listener)]
    assert len(received) == events._QUEUE_SIZE
    assert received[-1] == events._QUEUE_SIZE + 4


@pytest.mark.asyncio
async def test_a_new_status_listener_starts_from_the_last_status():
    hub = events.EventHub(auth_enabled=False)
    hub._last_status = events.envelope("status", {"bridge": "x"})
    async with hub.listen(frozenset({"status"})) as listener:
        assert _drain(listener)[0]["data"] == {"bridge": "x"}


@pytest.mark.asyncio
async def test_listener_count_is_capped(monkeypatch):
    monkeypatch.setattr(events, "_MAX_LISTENERS", 1)
    hub = events.EventHub(auth_enabled=False)
    async with hub.listen(frozenset({"job"})):
        with pytest.raises(events.ApiError) as exc:
            async with hub.listen(frozenset({"job"})):
                pass
    assert exc.value.status == 503


def _drain(listener) -> list[dict]:
    out = []
    while True:
        try:
            out.append(listener.queue.get_nowait())
        except asyncio.QueueEmpty:
            return out


def test_envelope_shape():
    event = events.envelope("job", {"id": "x"})
    assert set(event) == {"v", "type", "at", "data"}
    assert event["v"] == events.EVENT_VERSION


# -- SSE --------------------------------------------------------------------------------------


def test_sse_carries_status_and_job_events(monkeypatch):
    monkeypatch.setattr(events, "_MAX_LIFETIME_S", 0.8)
    with make_client() as client:
        job_started = []

        def _start_job_soon():
            import time

            time.sleep(0.2)
            job_started.append(jobs.start("test.kind", lambda _ctx: {"done": True}))

        import threading

        threading.Thread(target=_start_job_soon, daemon=True).start()
        resp = client.get("/api/v1/events", params={"types": "status,job"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["Cache-Control"] == "no-cache, no-transform"
    received = _sse_events(resp.text)
    kinds = [e["type"] for e in received]
    assert "status" in kinds
    job_events = [e["data"] for e in received if e["type"] == "job"]
    assert {e["status"] for e in job_events} >= {"succeeded"}
    assert all(e["kind"] == "test.kind" for e in job_events)


def test_sse_rejects_unknown_types():
    resp = make_client().get("/api/v1/events", params={"types": "status,gossip"})
    assert resp.status_code == 400
    assert resp.json()["code"] == "unknown_event_type"


def test_sse_needs_credentials_when_auth_is_on():
    resp = make_client({"AUTH_ENABLED": True}).get("/api/v1/events")
    assert resp.status_code == 401


# -- WebSocket -----------------------------------------------------------------------------------


def test_websocket_delivers_job_events():
    with make_client() as client, client.websocket_connect("/api/v1/events/ws?types=job") as ws:
        jobs.start("test.ws", lambda _ctx: 42)
        seen = []
        while not any(e["data"]["status"] == "succeeded" for e in seen):
            seen.append(ws.receive_json())
    assert all(e["type"] == "job" and e["data"]["kind"] == "test.ws" for e in seen)


def test_websocket_from_another_site_is_refused():
    from starlette.websockets import WebSocketDisconnect

    with (
        make_client() as client,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/v1/events/ws", headers={"Origin": "http://evil.example"}),
    ):
        pass
    assert exc.value.code == 4403


def test_websocket_with_a_session_needs_the_csrf_parameter(tmp_config):
    from starlette.websockets import WebSocketDisconnect

    from sendspin_bridge.config import hash_password

    tmp_config.write_text(json.dumps({"AUTH_ENABLED": True, "AUTH_PASSWORD_HASH": hash_password("pw-12345678")}))
    with make_client({"AUTH_ENABLED": True}) as client:
        csrf = client.post("/api/v1/auth/session", json={"password": "pw-12345678"}).json()["csrf_token"]

        with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/api/v1/events/ws"):
            pass
        assert exc.value.code == 4403

        with client.websocket_connect(f"/api/v1/events/ws?types=job&csrf={csrf}") as ws:
            jobs.start("test.csrf", lambda _ctx: None)
            assert ws.receive_json()["type"] == "job"


def test_websocket_without_credentials_is_refused_when_auth_is_on():
    from starlette.websockets import WebSocketDisconnect

    with (
        make_client({"AUTH_ENABLED": True}) as client,
        pytest.raises(WebSocketDisconnect) as exc,
        client.websocket_connect("/api/v1/events/ws"),
    ):
        pass
    assert exc.value.code == 4401
