"""Helpers for parsing daemon stdout IPC messages."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from services.ipc_protocol import (
    IPC_PROTOCOL_VERSION,
    IPC_PROTOCOL_VERSION_KEY,
    coerce_message_dict,
    parse_error_envelope,
    parse_log_envelope,
    parse_protocol_version,
    parse_status_envelope,
)

if TYPE_CHECKING:
    from collections.abc import Callable


_IPC_MAX_LINE_BYTES = 1_048_576  # 1 MB


class SubprocessIpcService:
    """Own daemon stdout parsing, protocol warnings, and message dispatch."""

    def __init__(
        self,
        *,
        player_name: str,
        protocol_warning_cache: set[str],
        status_updater: Callable[[dict[str, Any]], None],
        log_methods: dict[str, Callable[..., None]] | None = None,
        logger_: logging.Logger | None = None,
        allowed_keys: frozenset[str] | None = None,
    ):
        self.player_name = player_name
        self._protocol_warning_cache = protocol_warning_cache
        self._status_updater = status_updater
        self._logger = logger_ or logging.getLogger(__name__)
        self._log_methods = log_methods or {
            "info": self._logger.info,
            "warning": self._logger.warning,
            "error": self._logger.error,
            "critical": self._logger.critical,
        }
        self._allowed_keys = allowed_keys or frozenset()

    async def read_stream(self, stdout) -> None:
        """Parse daemon stdout JSON-line messages until EOF."""
        if stdout is None:
            return
        while True:
            line = await stdout.readline()
            if not line:
                break
            if len(line) > _IPC_MAX_LINE_BYTES:
                self._logger.warning(
                    "[%s] Skipping oversized IPC message (%d bytes)",
                    self.player_name,
                    len(line),
                )
                continue
            msg = self.parse_line(line)
            if msg is None:
                continue
            self.handle_message(msg)

    def parse_line(self, line: bytes) -> dict[str, Any] | None:
        """Decode one stdout line into a JSON message if possible."""
        try:
            return coerce_message_dict(json.loads(line.decode().strip()))
        except (json.JSONDecodeError, ValueError):
            return None

    def handle_message(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one parsed IPC message and return status updates if present."""
        self._warn_incompatible_protocol(msg.get(IPC_PROTOCOL_VERSION_KEY))
        status_envelope = parse_status_envelope(msg, allowed_keys=self._allowed_keys)
        if status_envelope is not None:
            if status_envelope.updates:
                self._status_updater(status_envelope.updates)
            return status_envelope.updates

        error_envelope = parse_error_envelope(msg)
        if error_envelope is not None:
            updates = {
                "last_error": error_envelope.message,
                "last_error_at": error_envelope.details.get("at"),
            }
            self._status_updater(updates)
            self._logger.error("[%s/proc] %s", self.player_name, error_envelope.message)
            return updates

        log_envelope = parse_log_envelope(msg)
        if log_envelope is not None:
            log_fn = self._log_methods.get(log_envelope.level, self._logger.info)
            log_fn("[%s/proc] %s", self.player_name, log_envelope.msg)
        return None

    def _warn_incompatible_protocol(self, value: object) -> None:
        if value is None:
            return
        parsed = parse_protocol_version(value)
        if parsed == IPC_PROTOCOL_VERSION:
            return
        cache_key = str(value)
        if cache_key in self._protocol_warning_cache:
            return
        self._protocol_warning_cache.add(cache_key)
        self._logger.warning(
            "[%s] Received daemon IPC message with protocol_version=%r; attempting compatible parse",
            self.player_name,
            value,
        )
