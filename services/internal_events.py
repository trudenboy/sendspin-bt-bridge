"""Lightweight in-process event publisher for bridge runtime events."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

UTC = timezone.utc

if TYPE_CHECKING:
    from collections.abc import Callable


class DeviceEventType(str, Enum):
    """Canonical per-device event types used across runtime surfaces."""

    BLUETOOTH_CONNECTED = "bluetooth-connected"
    BLUETOOTH_DISCONNECTED = "bluetooth-disconnected"
    BLUETOOTH_RECONNECTED = "bluetooth-reconnected"
    BLUETOOTH_RECONNECT_FAILED = "bluetooth-reconnect-failed"
    DAEMON_CONNECTED = "daemon-connected"
    DAEMON_DISCONNECTED = "daemon-disconnected"
    PLAYBACK_STARTED = "playback-started"
    PLAYBACK_STOPPED = "playback-stopped"
    AUDIO_STREAMING = "audio-streaming"
    AUDIO_STREAM_STALLED = "audio-stream-stalled"
    RECONNECTING = "reconnecting"
    REANCHORING = "reanchoring"
    RUNTIME_ERROR = "runtime-error"
    BT_MANAGEMENT_DISABLED = "bt-management-disabled"
    MA_MONITOR_STALE = "ma-monitor-stale"


_DEFAULT_EVENT_LEVELS: dict[str, str] = {
    DeviceEventType.BLUETOOTH_CONNECTED.value: "info",
    DeviceEventType.BLUETOOTH_DISCONNECTED.value: "warning",
    DeviceEventType.BLUETOOTH_RECONNECTED.value: "info",
    DeviceEventType.BLUETOOTH_RECONNECT_FAILED.value: "warning",
    DeviceEventType.DAEMON_CONNECTED.value: "info",
    DeviceEventType.DAEMON_DISCONNECTED.value: "warning",
    DeviceEventType.PLAYBACK_STARTED.value: "info",
    DeviceEventType.PLAYBACK_STOPPED.value: "info",
    DeviceEventType.AUDIO_STREAMING.value: "info",
    DeviceEventType.AUDIO_STREAM_STALLED.value: "warning",
    DeviceEventType.RECONNECTING.value: "warning",
    DeviceEventType.REANCHORING.value: "warning",
    DeviceEventType.RUNTIME_ERROR.value: "error",
    DeviceEventType.BT_MANAGEMENT_DISABLED.value: "warning",
    DeviceEventType.MA_MONITOR_STALE.value: "warning",
}


def normalize_device_event(
    event_type: str | DeviceEventType,
    *,
    level: str = "info",
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize a per-device event payload for persistence and transport."""
    normalized_type = str(event_type.value if isinstance(event_type, DeviceEventType) else event_type or "").strip()
    if not normalized_type:
        return None
    normalized_level = str(level or _DEFAULT_EVENT_LEVELS.get(normalized_type, "info")).strip().lower() or "info"
    if normalized_level not in {"debug", "info", "warning", "error", "critical"}:
        normalized_level = _DEFAULT_EVENT_LEVELS.get(normalized_type, "info")
    payload_details = {str(key): value for key, value in dict(details or {}).items() if value is not None}
    return {
        "event_type": normalized_type,
        "level": normalized_level,
        "message": str(message or normalized_type.replace("-", " ")),
        "details": payload_details,
    }


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
            try:
                callback(event)
            except Exception:
                logger.exception("Subscriber callback %r failed for event %s", callback, event.event_type)
        return event

    def clear_subscribers(self) -> None:
        """Remove all subscribers."""
        with self._lock:
            self._subscribers.clear()
