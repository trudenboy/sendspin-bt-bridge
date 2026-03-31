"""Tests for services.event_store — in-memory ring-buffer event store."""

from __future__ import annotations

import threading
from datetime import timezone

import pytest

from services.internal_events import InternalEvent, InternalEventPublisher

UTC = timezone.utc


def _make_event(
    event_type: str = "test-event",
    category: str = "device",
    subject_id: str = "player-1",
    at: str | None = None,
    **payload_kw,
) -> InternalEvent:
    kw: dict = dict(
        event_type=event_type,
        category=category,
        subject_id=subject_id,
        payload=payload_kw,
    )
    if at is not None:
        kw["at"] = at
    return InternalEvent(**kw)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestEventStoreCreation:
    def test_empty_store(self):
        from services.event_store import EventStore

        store = EventStore()
        assert store.query() == []
        assert store.get_player_ids() == set()

    def test_custom_capacities(self):
        from services.event_store import EventStore

        store = EventStore(player_capacity=10, bridge_capacity=20)
        s = store.stats()
        assert s.bridge_buffer_capacity == 20
        assert s.player_buffer_capacity == 10


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class TestRecord:
    def test_record_stores_in_bridge_and_player_buffers(self):
        from services.event_store import EventStore

        store = EventStore()
        ev = _make_event(subject_id="p1")
        store.record(ev)

        assert store.query() == [ev]
        assert store.query(player_id="p1") == [ev]

    def test_record_empty_subject_id_goes_only_to_bridge(self):
        from services.event_store import EventStore

        store = EventStore()
        ev = _make_event(subject_id="")
        store.record(ev)

        assert len(store.query()) == 1
        assert store.get_player_ids() == set()

    def test_player_ring_buffer_caps(self):
        from services.event_store import EventStore

        cap = 5
        store = EventStore(player_capacity=cap, bridge_capacity=100)
        events = [_make_event(subject_id="p1", event_type=f"e{i}") for i in range(cap + 3)]
        for ev in events:
            store.record(ev)

        player_events = store.query(player_id="p1")
        assert len(player_events) == cap
        assert player_events[0].event_type == f"e{3}"

    def test_bridge_ring_buffer_caps(self):
        from services.event_store import EventStore

        cap = 10
        store = EventStore(player_capacity=100, bridge_capacity=cap)
        events = [_make_event(event_type=f"e{i}") for i in range(cap + 5)]
        for ev in events:
            store.record(ev)

        bridge_events = store.query()
        assert len(bridge_events) == cap
        assert bridge_events[0].event_type == f"e{5}"


# ---------------------------------------------------------------------------
# Query filters
# ---------------------------------------------------------------------------


class TestQuery:
    @pytest.fixture()
    def populated_store(self):
        from services.event_store import EventStore

        store = EventStore()
        store.record(_make_event(subject_id="p1", event_type="a", at="2025-01-01T00:00:00"))
        store.record(_make_event(subject_id="p2", event_type="b", at="2025-01-02T00:00:00"))
        store.record(_make_event(subject_id="p1", event_type="c", at="2025-01-03T00:00:00"))
        store.record(_make_event(subject_id="p2", event_type="a", at="2025-01-04T00:00:00"))
        store.record(_make_event(subject_id="p1", event_type="b", at="2025-01-05T00:00:00"))
        return store

    def test_no_filters_returns_all(self, populated_store):
        assert len(populated_store.query()) == 5

    def test_filter_by_player_id(self, populated_store):
        results = populated_store.query(player_id="p1")
        assert len(results) == 3
        assert all(e.subject_id == "p1" for e in results)

    def test_filter_by_player_id_unknown(self, populated_store):
        assert populated_store.query(player_id="unknown") == []

    def test_filter_by_event_types(self, populated_store):
        results = populated_store.query(event_types=["a", "b"])
        assert len(results) == 4
        assert all(e.event_type in ("a", "b") for e in results)

    def test_filter_by_single_event_type(self, populated_store):
        results = populated_store.query(event_types=["c"])
        assert len(results) == 1
        assert results[0].event_type == "c"

    def test_filter_by_since(self, populated_store):
        results = populated_store.query(since="2025-01-03T00:00:00")
        assert len(results) == 3
        assert all(e.at >= "2025-01-03T00:00:00" for e in results)

    def test_filter_by_since_excludes_earlier(self, populated_store):
        results = populated_store.query(since="2025-01-04T00:00:01")
        assert len(results) == 1

    def test_filter_by_limit(self, populated_store):
        results = populated_store.query(limit=2)
        assert len(results) == 2
        # Should return the last 2 events
        assert results[0].at == "2025-01-04T00:00:00"
        assert results[1].at == "2025-01-05T00:00:00"

    def test_combined_filters(self, populated_store):
        results = populated_store.query(
            player_id="p1",
            event_types=["a", "c"],
            since="2025-01-02T00:00:00",
            limit=1,
        )
        assert len(results) == 1
        assert results[0].event_type == "c"
        assert results[0].subject_id == "p1"


# ---------------------------------------------------------------------------
# get_player_ids
# ---------------------------------------------------------------------------


class TestGetPlayerIds:
    def test_returns_all_player_ids(self):
        from services.event_store import EventStore

        store = EventStore()
        store.record(_make_event(subject_id="p1"))
        store.record(_make_event(subject_id="p2"))
        store.record(_make_event(subject_id="p1"))
        assert store.get_player_ids() == {"p1", "p2"}


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_specific_player(self):
        from services.event_store import EventStore

        store = EventStore()
        store.record(_make_event(subject_id="p1"))
        store.record(_make_event(subject_id="p2"))
        store.clear(player_id="p1")

        assert store.query(player_id="p1") == []
        assert len(store.query(player_id="p2")) == 1
        # Bridge-wide buffer still has both events
        assert len(store.query()) == 2
        assert "p1" not in store.get_player_ids()

    def test_clear_all(self):
        from services.event_store import EventStore

        store = EventStore()
        store.record(_make_event(subject_id="p1"))
        store.record(_make_event(subject_id="p2"))
        store.clear()

        assert store.query() == []
        assert store.get_player_ids() == set()

    def test_clear_nonexistent_player_is_noop(self):
        from services.event_store import EventStore

        store = EventStore()
        store.clear(player_id="nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_empty(self):
        from services.event_store import EventStore

        store = EventStore(player_capacity=100, bridge_capacity=500)
        s = store.stats()
        assert s.total_events == 0
        assert s.player_counts == {}
        assert s.bridge_buffer_size == 0
        assert s.bridge_buffer_capacity == 500
        assert s.player_buffer_capacity == 100

    def test_stats_with_events(self):
        from services.event_store import EventStore

        store = EventStore()
        store.record(_make_event(subject_id="p1"))
        store.record(_make_event(subject_id="p1"))
        store.record(_make_event(subject_id="p2"))

        s = store.stats()
        assert s.total_events == 3
        assert s.player_counts == {"p1": 2, "p2": 1}
        assert s.bridge_buffer_size == 3

    def test_stats_to_dict(self):
        from services.event_store import EventStore

        store = EventStore(player_capacity=10, bridge_capacity=50)
        store.record(_make_event(subject_id="p1"))
        d = store.stats().to_dict()
        assert isinstance(d, dict)
        assert d["total_events"] == 1
        assert d["player_counts"] == {"p1": 1}
        assert d["bridge_buffer_capacity"] == 50
        assert d["player_buffer_capacity"] == 10


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_writes(self):
        from services.event_store import EventStore

        store = EventStore(player_capacity=5000, bridge_capacity=10000)
        num_threads = 8
        events_per_thread = 200
        barrier = threading.Barrier(num_threads)

        def _writer(tid: int):
            barrier.wait()
            for i in range(events_per_thread):
                store.record(
                    _make_event(
                        subject_id=f"player-{tid}",
                        event_type=f"evt-{tid}-{i}",
                    )
                )

        threads = [threading.Thread(target=_writer, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total_expected = num_threads * events_per_thread
        assert len(store.query()) == total_expected
        assert len(store.get_player_ids()) == num_threads
        for tid in range(num_threads):
            assert len(store.query(player_id=f"player-{tid}")) == events_per_thread


# ---------------------------------------------------------------------------
# subscribe_to_publisher
# ---------------------------------------------------------------------------


class TestPublisherSubscription:
    def test_subscribe_auto_captures_events(self):
        from services.event_store import EventStore

        store = EventStore()
        pub = InternalEventPublisher()
        store.subscribe_to_publisher(pub)

        pub.publish(event_type="test", category="device", subject_id="p1")
        assert len(store.query()) == 1
        assert store.query()[0].event_type == "test"

    def test_unsubscribe_stops_capture(self):
        from services.event_store import EventStore

        store = EventStore()
        pub = InternalEventPublisher()
        store.subscribe_to_publisher(pub)

        pub.publish(event_type="before", category="device", subject_id="p1")
        store.unsubscribe()
        pub.publish(event_type="after", category="device", subject_id="p1")

        assert len(store.query()) == 1
        assert store.query()[0].event_type == "before"

    def test_resubscribe_replaces_previous(self):
        from services.event_store import EventStore

        store = EventStore()
        pub1 = InternalEventPublisher()
        pub2 = InternalEventPublisher()

        store.subscribe_to_publisher(pub1)
        pub1.publish(event_type="from-pub1", category="device", subject_id="p1")
        assert len(store.query()) == 1

        store.subscribe_to_publisher(pub2)
        pub1.publish(event_type="from-pub1-after", category="device", subject_id="p1")
        pub2.publish(event_type="from-pub2", category="device", subject_id="p1")

        # Should have: from-pub1 + from-pub2 (pub1 unsubscribed after resubscribe)
        assert len(store.query()) == 2
        types = [e.event_type for e in store.query()]
        assert "from-pub1" in types
        assert "from-pub2" in types
        assert "from-pub1-after" not in types
