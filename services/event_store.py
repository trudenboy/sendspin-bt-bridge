"""Structured event history with queryable ring buffers.

Provides per-player and bridge-wide event persistence backed by
bounded in-memory deques.  Thread-safe for concurrent reads and writes.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from services.internal_events import InternalEvent, InternalEventPublisher


@dataclass(frozen=True)
class EventStoreStats:
    """Summary statistics for the event store."""

    total_events: int
    player_counts: dict[str, int]
    bridge_buffer_size: int
    bridge_buffer_capacity: int
    player_buffer_capacity: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_events": self.total_events,
            "player_counts": dict(self.player_counts),
            "bridge_buffer_size": self.bridge_buffer_size,
            "bridge_buffer_capacity": self.bridge_buffer_capacity,
            "player_buffer_capacity": self.player_buffer_capacity,
        }


class EventStore:
    """In-memory ring-buffer event store with per-player and bridge-wide history."""

    def __init__(
        self,
        player_capacity: int = 1000,
        bridge_capacity: int = 5000,
    ) -> None:
        self._lock = threading.Lock()
        self._player_capacity = player_capacity
        self._bridge_capacity = bridge_capacity
        self._bridge_events: deque[InternalEvent] = deque(maxlen=bridge_capacity)
        self._player_events: dict[str, deque[InternalEvent]] = {}
        self._unsubscribe: Any = None

    def record(self, event: InternalEvent) -> None:
        """Append an event to both bridge-wide and per-player ring buffers."""
        with self._lock:
            self._bridge_events.append(event)
            player_id = event.subject_id
            if player_id:
                if player_id not in self._player_events:
                    self._player_events[player_id] = deque(maxlen=self._player_capacity)
                self._player_events[player_id].append(event)

    def query(
        self,
        *,
        player_id: str | None = None,
        event_types: Sequence[str] | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[InternalEvent]:
        """Query events with optional filters."""
        with self._lock:
            if player_id:
                source = list(self._player_events.get(player_id, []))
            else:
                source = list(self._bridge_events)

        results = source
        if event_types:
            type_set = set(event_types)
            results = [e for e in results if e.event_type in type_set]
        if since:
            results = [e for e in results if e.at >= since]
        if limit is not None and limit > 0:
            results = results[-limit:]
        return results

    def get_player_ids(self) -> set[str]:
        """Return set of player IDs that have events."""
        with self._lock:
            return set(self._player_events.keys())

    def clear(self, *, player_id: str | None = None) -> None:
        """Clear events. If player_id given, clear only that player's buffer."""
        with self._lock:
            if player_id:
                self._player_events.pop(player_id, None)
            else:
                self._bridge_events.clear()
                self._player_events.clear()

    def stats(self) -> EventStoreStats:
        """Return summary statistics."""
        with self._lock:
            player_counts = {pid: len(buf) for pid, buf in self._player_events.items()}
            return EventStoreStats(
                total_events=len(self._bridge_events),
                player_counts=player_counts,
                bridge_buffer_size=len(self._bridge_events),
                bridge_buffer_capacity=self._bridge_capacity,
                player_buffer_capacity=self._player_capacity,
            )

    def subscribe_to_publisher(self, publisher: InternalEventPublisher) -> None:
        """Auto-capture events from an InternalEventPublisher."""
        if self._unsubscribe:
            self._unsubscribe()
        self._unsubscribe = publisher.subscribe(self.record)

    def unsubscribe(self) -> None:
        """Disconnect from publisher."""
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
