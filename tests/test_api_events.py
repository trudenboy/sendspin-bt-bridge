"""Tests for GET /api/events and GET /api/events/stats endpoints."""

from __future__ import annotations

import json
import sys

import pytest
from flask import Flask

from services.event_store import EventStore
from services.internal_events import InternalEvent


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory so the web app can start."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def store():
    """Return a fresh EventStore for testing."""
    return EventStore()


@pytest.fixture()
def client(store, monkeypatch):
    """Return a Flask test client with status_bp registered and event store patched."""
    import state

    monkeypatch.setattr(state, "get_event_store", lambda: store)

    # Remove any cached route modules that might be stubs
    _stashed: dict[str, object] = {}
    for mod_name in [
        "routes.api_status",
        "routes",
    ]:
        cached = sys.modules.get(mod_name)
        if cached is not None and getattr(cached, "__file__", None) is None:
            _stashed[mod_name] = sys.modules.pop(mod_name)

    from routes.api_status import status_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(status_bp)

    yield app.test_client()

    for mod_name, mod in _stashed.items():
        sys.modules.setdefault(mod_name, mod)


def _make_event(
    event_type: str = "test-event",
    subject_id: str = "player1",
    category: str = "device",
    payload: dict | None = None,
    at: str = "2026-01-15T12:00:00+00:00",
) -> InternalEvent:
    return InternalEvent(
        event_type=event_type,
        subject_id=subject_id,
        category=category,
        payload=payload or {},
        at=at,
    )


# --- Tests ---


def test_events_endpoint_returns_200(client, store):
    """GET /api/events returns 200 with a JSON array."""
    store.record(_make_event())
    resp = client.get("/api/events")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) == 1


def test_events_endpoint_empty(client):
    """GET /api/events returns empty list when no events exist."""
    resp = client.get("/api/events")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == []


def test_events_endpoint_with_player_id(client, store):
    """?player_id=X filters events to that player."""
    store.record(_make_event(subject_id="player-a"))
    store.record(_make_event(subject_id="player-b"))
    store.record(_make_event(subject_id="player-a"))

    resp = client.get("/api/events?player_id=player-a")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 2
    assert all(e["subject_id"] == "player-a" for e in data)


def test_events_endpoint_with_type_filter(client, store):
    """?type=X filters by event type; comma-separated for multiple."""
    store.record(_make_event(event_type="bluetooth-connected"))
    store.record(_make_event(event_type="playback-started"))
    store.record(_make_event(event_type="bluetooth-disconnected"))

    resp = client.get("/api/events?type=bluetooth-connected")
    data = resp.get_json()
    assert len(data) == 1
    assert data[0]["event_type"] == "bluetooth-connected"

    # Comma-separated
    resp = client.get("/api/events?type=bluetooth-connected,playback-started")
    data = resp.get_json()
    assert len(data) == 2


def test_events_endpoint_with_limit(client, store):
    """?limit=N returns at most N events (tail of the buffer)."""
    for i in range(10):
        store.record(_make_event(at=f"2026-01-15T12:00:{i:02d}+00:00"))

    resp = client.get("/api/events?limit=5")
    data = resp.get_json()
    assert len(data) == 5
    # Should be the last 5 events
    assert data[0]["at"] == "2026-01-15T12:00:05+00:00"


def test_events_endpoint_with_since(client, store):
    """?since=<iso> filters events whose timestamp >= since."""
    store.record(_make_event(at="2025-12-01T00:00:00+00:00"))
    store.record(_make_event(at="2026-01-10T00:00:00+00:00"))
    store.record(_make_event(at="2026-02-01T00:00:00+00:00"))

    resp = client.get("/api/events?since=2026-01-01T00:00:00+00:00")
    data = resp.get_json()
    assert len(data) == 2


def test_events_stats_endpoint(client, store):
    """GET /api/events/stats returns EventStore statistics."""
    store.record(_make_event(subject_id="player-x"))
    store.record(_make_event(subject_id="player-x"))
    store.record(_make_event(subject_id="player-y"))

    resp = client.get("/api/events/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total_events"] == 3
    assert data["player_counts"]["player-x"] == 2
    assert data["player_counts"]["player-y"] == 1
    assert "bridge_buffer_size" in data
    assert "bridge_buffer_capacity" in data
    assert "player_buffer_capacity" in data


def test_events_serialization(client, store):
    """Events are properly serialized with all expected fields."""
    store.record(
        _make_event(
            event_type="bluetooth-connected",
            subject_id="player-abc",
            category="device",
            payload={"adapter": "hci0", "mac": "AA:BB:CC:DD:EE:FF"},
            at="2026-03-20T10:30:00+00:00",
        )
    )

    resp = client.get("/api/events")
    data = resp.get_json()
    assert len(data) == 1
    event = data[0]
    assert event["event_type"] == "bluetooth-connected"
    assert event["subject_id"] == "player-abc"
    assert event["category"] == "device"
    assert event["at"] == "2026-03-20T10:30:00+00:00"
    assert event["payload"] == {"adapter": "hci0", "mac": "AA:BB:CC:DD:EE:FF"}
