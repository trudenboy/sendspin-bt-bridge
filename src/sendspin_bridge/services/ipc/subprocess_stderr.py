"""Helpers for classifying and forwarding daemon subprocess stderr."""

from __future__ import annotations

import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from sendspin_bridge.services.diagnostics.log_analysis import classify_subprocess_stderr_level

UTC = timezone.utc

_PORT_NUMBER_RE = re.compile(r":(\d{1,5})\b")
_TAIL_MAXLEN = 20


class SubprocessStderrService:
    """Own stderr classification and crash-like status publication."""

    _CONNECTION_ERROR_THRESHOLD = 3
    _PORT_COLLISION_MARKERS = ("errno 98", "address already in use", "eaddrinuse")

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
        # Ring buffer of the most recent non-blank stderr lines.  Captured on
        # daemon death so the diagnostics report can show why a daemon exited
        # even when it didn't emit a structured error envelope (issue #291).
        self._tail: deque[str] = deque(maxlen=_TAIL_MAXLEN)

    def tail(self) -> list[str]:
        """Return a snapshot of the last 20 non-blank stderr lines, oldest first."""
        return list(self._tail)

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
        # Capture every non-blank stderr line into the ring buffer so the
        # death-detection path can attach context to "Daemon subprocess died"
        # without needing the subprocess to emit a structured error envelope.
        self._tail.append(text)

        # Port-collision (EADDRINUSE): must run before the generic classifier so the
        # hint with the actionable lsof command is not overwritten by a terse one.
        lower = text.lower()
        if any(marker in lower for marker in self._PORT_COLLISION_MARKERS):
            match = _PORT_NUMBER_RE.search(text)
            port_str = None
            if match:
                candidate = match.group(1)
                if 1 <= int(candidate) <= 65535:
                    port_str = candidate
            if port_str:
                hint = (
                    f"Port {port_str} already in use by another process. "
                    f"Run 'lsof -i :{port_str}' to identify the owner."
                )
            else:
                hint = "Listen port already in use by another process. Run 'lsof -i :<port>' to identify the owner."
            self._update_status(
                {
                    "port_collision": True,
                    "last_error": hint,
                    "last_error_at": self._now_factory().isoformat(),
                }
            )
            self._logger.error("[%s] daemon stderr: %s", self.player_name, text)
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
