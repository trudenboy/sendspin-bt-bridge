"""Helpers for classifying and forwarding daemon subprocess stderr."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from services.log_analysis import classify_subprocess_stderr_level

UTC = timezone.utc


class SubprocessStderrService:
    """Own stderr classification and crash-like status publication."""

    _CONNECTION_ERROR_THRESHOLD = 3

    def __init__(
        self,
        *,
        player_name: str,
        update_status: Callable[[dict], None],
        logger_: logging.Logger | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ):
        self.player_name = player_name
        self._update_status = update_status
        self._logger = logger_ or logging.getLogger(__name__)
        self._now_factory = now_factory or (lambda: datetime.now(tz=UTC))
        self._consecutive_connection_errors = 0

    async def read_stream(self, stderr) -> None:
        """Read stderr lines until EOF and forward them through classification."""
        if stderr is None:
            return
        while True:
            line = await stderr.readline()
            if not line:
                break
            self.handle_line(line.decode(errors="replace").rstrip())

    def handle_line(self, line: str) -> None:
        """Classify one daemon stderr line and mirror crash-like output into status."""
        text = line.rstrip()
        if not text:
            return

        # Track repeated connection errors to surface them
        if "Connection error" in text and "ClientConnectorError" in text:
            self._consecutive_connection_errors += 1
            if self._consecutive_connection_errors >= self._CONNECTION_ERROR_THRESHOLD:
                self._update_status(
                    {
                        "last_error": (
                            "Cannot connect to Sendspin server. "
                            "Check that SENDSPIN_PORT matches your Music Assistant Sendspin port."
                        ),
                        "last_error_at": self._now_factory().isoformat(),
                    }
                )
        else:
            self._consecutive_connection_errors = 0

        level = classify_subprocess_stderr_level(text)
        if level in ("error", "critical"):
            self._update_status(
                {
                    "last_error": text[:500],
                    "last_error_at": self._now_factory().isoformat(),
                }
            )
        log_fn = {
            "warning": self._logger.warning,
            "error": self._logger.error,
            "critical": self._logger.critical,
        }.get(level, self._logger.warning)
        log_fn("[%s] daemon stderr: %s", self.player_name, text)
