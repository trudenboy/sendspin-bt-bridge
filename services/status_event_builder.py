"""Helpers for deriving structured device events from status transitions."""

from __future__ import annotations

from typing import Any

from services.internal_events import DeviceEventType


class StatusEventBuilder:
    """Pure builder for meaningful status-transition events."""

    @staticmethod
    def build(
        previous: dict[str, object],
        current: dict[str, object],
        updates: dict[str, Any],
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []

        def _add(
            event_type: str,
            *,
            level: str = "info",
            message: str | None = None,
            details: dict[str, object] | None = None,
        ) -> None:
            events.append(
                {
                    "event_type": event_type,
                    "level": level,
                    "message": message or event_type.replace("-", " "),
                    "details": dict(details or {}),
                }
            )

        if "bluetooth_connected" in updates and current.get("bluetooth_connected") != previous.get(
            "bluetooth_connected"
        ):
            if current.get("bluetooth_connected"):
                _add(DeviceEventType.BLUETOOTH_CONNECTED.value, message="Bluetooth speaker connected")
            else:
                _add(
                    DeviceEventType.BLUETOOTH_DISCONNECTED.value,
                    level="warning",
                    message="Bluetooth speaker disconnected",
                )

        if "server_connected" in updates and current.get("server_connected") != previous.get("server_connected"):
            if current.get("server_connected"):
                _add(DeviceEventType.DAEMON_CONNECTED.value, message="Sendspin daemon connected to Music Assistant")
            else:
                _add(
                    DeviceEventType.DAEMON_DISCONNECTED.value,
                    level="warning",
                    message="Sendspin daemon disconnected",
                )

        if "playing" in updates and current.get("playing") != previous.get("playing"):
            if current.get("playing"):
                _add(DeviceEventType.PLAYBACK_STARTED.value, message="Playback started")
            else:
                _add(DeviceEventType.PLAYBACK_STOPPED.value, message="Playback stopped")

        if (
            "audio_streaming" in updates
            and current.get("audio_streaming")
            and current.get("audio_streaming") != previous.get("audio_streaming")
        ):
            _add(DeviceEventType.AUDIO_STREAMING.value, message="Audio stream became active")

        if (
            "audio_streaming" in updates
            and not current.get("audio_streaming")
            and previous.get("audio_streaming")
            and current.get("playing")
        ):
            _add(
                DeviceEventType.AUDIO_STREAM_STALLED.value,
                level="warning",
                message="Playback active without audio stream",
            )

        if "reconnecting" in updates and current.get("reconnecting"):
            _add(
                DeviceEventType.RECONNECTING.value,
                level="warning",
                message="Bluetooth reconnect in progress",
                details={"attempt": current.get("reconnect_attempt")},
            )

        if (
            "reconnecting" in updates
            and not current.get("reconnecting")
            and previous.get("reconnecting")
            and current.get("bluetooth_connected")
        ):
            _add(
                DeviceEventType.BLUETOOTH_RECONNECTED.value,
                message="Bluetooth reconnect succeeded",
                details={"attempt": previous.get("reconnect_attempt") or current.get("reconnect_attempt")},
            )

        if "reanchoring" in updates and current.get("reanchoring"):
            _add(
                DeviceEventType.REANCHORING.value,
                level="warning",
                message="Route re-anchor in progress",
                details={"reanchor_count": current.get("reanchor_count")},
            )

        if (
            "bt_management_enabled" in updates
            and not current.get("bt_management_enabled")
            and previous.get("bt_management_enabled", True)
        ):
            _add(
                DeviceEventType.BT_MANAGEMENT_DISABLED.value,
                level="warning",
                message="Bluetooth management disabled",
                details={"released_by": current.get("bt_released_by")},
            )

        current_error = str(current.get("last_error") or "").strip()
        previous_error = str(previous.get("last_error") or "").strip()
        if current_error and current_error != previous_error:
            _add(
                DeviceEventType.RUNTIME_ERROR.value,
                level="error",
                message=current_error,
                details={"last_error_at": current.get("last_error_at")},
            )

        return events
