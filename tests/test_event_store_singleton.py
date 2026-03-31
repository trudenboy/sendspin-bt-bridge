"""Tests for EventStore singleton wiring in state.py."""


def test_get_event_store_returns_eventstore():
    """state.get_event_store() returns an EventStore instance."""
    from services.event_store import EventStore
    from state import get_event_store

    store = get_event_store()
    assert isinstance(store, EventStore)


def test_get_event_store_is_singleton():
    """Repeated calls return the same instance."""
    from state import get_event_store

    assert get_event_store() is get_event_store()


def test_event_store_captures_published_events():
    """Events published via publish_device_event() are captured by EventStore."""
    from state import get_event_store, publish_device_event

    store = get_event_store()

    # Clear any pre-existing events
    store.clear()

    # Publish an event
    publish_device_event("test-device", "test_event", message="hello")

    # Query events — publish_device_event wraps as "device.event.recorded"
    events = store.query(player_id="test-device")
    assert len(events) >= 1
    assert any(e.event_type == "device.event.recorded" for e in events)
    assert any(e.payload.get("event_type") == "test_event" for e in events)


def test_event_store_captures_bridge_events():
    """Events published via publish_bridge_event() are captured by EventStore."""
    from state import get_event_store, publish_bridge_event

    store = get_event_store()
    store.clear()

    publish_bridge_event("test_bridge_event", payload={"key": "value"})

    events = store.query(player_id="bridge")
    assert len(events) >= 1
    assert any(e.event_type == "test_bridge_event" for e in events)


def test_event_store_stats():
    """EventStore stats are accessible."""
    from state import get_event_store

    store = get_event_store()
    stats = store.stats()
    assert hasattr(stats, "total_events")
    assert hasattr(stats, "bridge_buffer_capacity")
