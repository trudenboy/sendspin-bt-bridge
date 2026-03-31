"""Tests for EventStore integration in build_device_snapshot().

Verifies that build_device_snapshot() queries the centralized EventStore
for recent_events, falling back to legacy state.get_device_events() when
the EventStore returns an empty result.
"""

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import state
from services.event_store import EventStore
from services.internal_events import InternalEvent
from services.status_snapshot import build_device_snapshot

UTC = timezone.utc


def _make_client(
    *,
    player_name="Kitchen",
    player_id="sendspin-kitchen",
    mac="AA:BB:CC:DD:EE:FF",
):
    return SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": False,
            "volume": 55,
            "uptime_start": datetime.now(tz=UTC),
        },
        _status_lock=threading.Lock(),
        player_name=player_name,
        player_id=player_id,
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=-500.0,
        connected_server_url="",
        bt_manager=SimpleNamespace(
            mac_address=mac,
            effective_adapter_mac="11:22:33:44:55:66",
            adapter="hci0",
            adapter_hci_name="hci0",
            battery_level=None,
            paired=True,
            max_reconnect_fails=0,
        ),
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )


def test_snapshot_uses_eventstore_events(monkeypatch):
    """build_device_snapshot() includes events from EventStore when available."""
    store = EventStore()
    store.record(
        InternalEvent(
            event_type="bt-connected",
            category="device",
            subject_id="sendspin-kitchen",
            payload={"detail": "paired"},
        )
    )
    store.record(
        InternalEvent(
            event_type="volume-changed",
            category="device",
            subject_id="sendspin-kitchen",
            payload={"volume": 70},
        )
    )
    monkeypatch.setattr("services.status_snapshot.get_event_store", lambda: store)
    # Ensure legacy path returns nothing so we know events come from store
    monkeypatch.setattr(state, "get_device_events", lambda *a, **kw: [])

    client = _make_client()
    snapshot = build_device_snapshot(client)

    assert len(snapshot.recent_events) == 2
    types = [e["event_type"] for e in snapshot.recent_events]
    assert "bt-connected" in types
    assert "volume-changed" in types


def test_snapshot_eventstore_empty_falls_back_to_legacy(monkeypatch):
    """When EventStore returns empty, fall back to legacy device events."""
    store = EventStore()
    monkeypatch.setattr("services.status_snapshot.get_event_store", lambda: store)

    legacy_events = [
        {"event_type": "legacy-event", "level": "info", "at": "2026-01-01T00:00:00+00:00"},
    ]
    monkeypatch.setattr(state, "get_device_events", lambda *a, **kw: list(legacy_events))

    client = _make_client()
    snapshot = build_device_snapshot(client)

    assert len(snapshot.recent_events) == 1
    assert snapshot.recent_events[0]["event_type"] == "legacy-event"


def test_snapshot_eventstore_filters_by_player_id(monkeypatch):
    """Events for other players are NOT included in the snapshot."""
    store = EventStore()
    store.record(
        InternalEvent(
            event_type="bt-connected",
            category="device",
            subject_id="sendspin-kitchen",
        )
    )
    store.record(
        InternalEvent(
            event_type="bt-connected",
            category="device",
            subject_id="other-player",
        )
    )
    monkeypatch.setattr("services.status_snapshot.get_event_store", lambda: store)
    monkeypatch.setattr(state, "get_device_events", lambda *a, **kw: [])

    client = _make_client(player_id="sendspin-kitchen")
    snapshot = build_device_snapshot(client)

    assert len(snapshot.recent_events) == 1
    assert snapshot.recent_events[0]["subject_id"] == "sendspin-kitchen"


def test_snapshot_eventstore_events_are_dicts(monkeypatch):
    """EventStore events are converted to plain dicts in recent_events."""
    store = EventStore()
    store.record(
        InternalEvent(
            event_type="sink-found",
            category="device",
            subject_id="sendspin-kitchen",
            payload={"sink": "bluez_sink.AA"},
        )
    )
    monkeypatch.setattr("services.status_snapshot.get_event_store", lambda: store)
    monkeypatch.setattr(state, "get_device_events", lambda *a, **kw: [])

    client = _make_client()
    snapshot = build_device_snapshot(client)

    assert len(snapshot.recent_events) == 1
    evt = snapshot.recent_events[0]
    assert isinstance(evt, dict)
    assert evt["event_type"] == "sink-found"
    assert evt["payload"]["sink"] == "bluez_sink.AA"
    assert "at" in evt


def test_snapshot_eventstore_respects_limit(monkeypatch):
    """EventStore query is called with a limit to cap recent events."""
    store = EventStore()
    for i in range(30):
        store.record(
            InternalEvent(
                event_type=f"event-{i}",
                category="device",
                subject_id="sendspin-kitchen",
            )
        )
    monkeypatch.setattr("services.status_snapshot.get_event_store", lambda: store)
    monkeypatch.setattr(state, "get_device_events", lambda *a, **kw: [])

    client = _make_client()
    snapshot = build_device_snapshot(client)

    # Should be capped at 20 (the limit we'll use in the implementation)
    assert len(snapshot.recent_events) <= 20
