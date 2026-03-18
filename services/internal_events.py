"""Lightweight in-process event publisher for bridge runtime events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import TYPE_CHECKING, Any

UTC = timezone.utc

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class InternalEvent:
    """Structured internal runtime event."""

    event_type: str
    category: str
    subject_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


class InternalEventPublisher:
    """Minimal thread-safe pub/sub for internal runtime events."""

    def __init__(self):
        self._subscribers: list[Callable[[InternalEvent], None]] = []
        self._lock = Lock()

    def subscribe(self, callback: Callable[[InternalEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def publish(
        self,
        *,
        event_type: str,
        category: str,
        subject_id: str,
        payload: dict[str, Any] | None = None,
    ) -> InternalEvent | None:
        if not event_type or not category or not subject_id:
            return None
        event = InternalEvent(
            event_type=event_type,
            category=category,
            subject_id=subject_id,
            payload=dict(payload or {}),
        )
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            callback(event)
        return event
