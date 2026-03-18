"""Helpers for deriving structured device events from status transitions."""

from __future__ import annotations

from typing import Any


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
                _add("bluetooth-connected", message="Bluetooth speaker connected")
            else:
                _add("bluetooth-disconnected", level="warning", message="Bluetooth speaker disconnected")

        if "server_connected" in updates and current.get("server_connected") != previous.get("server_connected"):
            if current.get("server_connected"):
                _add("daemon-connected", message="Sendspin daemon connected to Music Assistant")
            else:
                _add("daemon-disconnected", level="warning", message="Sendspin daemon disconnected")

        if "playing" in updates and current.get("playing") != previous.get("playing"):
            if current.get("playing"):
                _add("playback-started", message="Playback started")
            else:
                _add("playback-stopped", message="Playback stopped")

        if (
            "audio_streaming" in updates
            and current.get("audio_streaming")
            and current.get("audio_streaming") != previous.get("audio_streaming")
        ):
            _add("audio-streaming", message="Audio stream became active")

        if "reconnecting" in updates and current.get("reconnecting"):
            _add(
                "reconnecting",
                level="warning",
                message="Bluetooth reconnect in progress",
                details={"attempt": current.get("reconnect_attempt")},
            )

        if "reanchoring" in updates and current.get("reanchoring"):
            _add(
                "reanchoring",
                level="warning",
                message="Route re-anchor in progress",
                details={"reanchor_count": current.get("reanchor_count")},
            )

        current_error = str(current.get("last_error") or "").strip()
        previous_error = str(previous.get("last_error") or "").strip()
        if current_error and current_error != previous_error:
            _add(
                "runtime-error",
                level="error",
                message=current_error,
                details={"last_error_at": current.get("last_error_at")},
            )

        return events
