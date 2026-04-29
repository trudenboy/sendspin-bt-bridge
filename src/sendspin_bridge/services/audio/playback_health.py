"""Playback health helpers for Sendspin subprocess watchdogs."""

from __future__ import annotations

from typing import Any


class PlaybackHealthMonitor:
    """Track playback watchdog state independently from SendspinClient."""

    def __init__(self, *, zombie_timeout_s: int = 15, max_zombie_restarts: int = 3):
        self._zombie_timeout_s = zombie_timeout_s
        self._max_zombie_restarts = max_zombie_restarts
        self._playing_since: float | None = None
        self._restart_count = 0
        self._has_streamed = False

    @property
    def zombie_timeout_s(self) -> int:
        return self._zombie_timeout_s

    @property
    def max_zombie_restarts(self) -> int:
        return self._max_zombie_restarts

    @property
    def playing_since(self) -> float | None:
        return self._playing_since

    @playing_since.setter
    def playing_since(self, value: float | None) -> None:
        self._playing_since = value

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @restart_count.setter
    def restart_count(self, value: int) -> None:
        self._restart_count = value

    @property
    def has_streamed(self) -> bool:
        return self._has_streamed

    @has_streamed.setter
    def has_streamed(self, value: bool) -> None:
        self._has_streamed = value

    def observe_status_update(self, *, previous_playing: bool, updates: dict[str, Any], now: float) -> None:
        """Track play-session state from in-band status updates."""
        if "playing" in updates:
            if updates["playing"] and not previous_playing:
                self._playing_since = now
                self._has_streamed = False
            elif not updates["playing"]:
                self._playing_since = None
                self._has_streamed = False
                self._restart_count = 0
        if updates.get("audio_streaming"):
            self._has_streamed = True
            self._restart_count = 0

    def reset_for_new_subprocess(self) -> None:
        """Reset stream-tracking state when a new daemon subprocess starts."""
        self._has_streamed = False

    def check_zombie_playback(
        self,
        *,
        is_playing: bool,
        is_streaming: bool,
        daemon_alive: bool,
        now: float,
    ) -> tuple[bool, float, int]:
        """Return restart instructions for a zombie play session."""
        if self._has_streamed:
            return False, 0.0, self._restart_count
        if not (is_playing and not is_streaming and daemon_alive and self._playing_since is not None):
            return False, 0.0, self._restart_count
        if self._restart_count >= self._max_zombie_restarts:
            return False, 0.0, self._restart_count

        elapsed = now - self._playing_since
        if elapsed < self._zombie_timeout_s:
            return False, elapsed, self._restart_count

        self._restart_count += 1
        self._playing_since = None
        return True, elapsed, self._restart_count
