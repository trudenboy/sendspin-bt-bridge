"""Shared helper for applying the bridge's log level.

The bridge has historically open-coded ``logging.getLogger().setLevel(...)`` in
three places (``web/interface.py`` startup, ``bridge/orchestrator.py`` boot,
``web/routes/api_config.py`` ``/api/settings/log_level``).  Each copy duplicated
the same input validation and ``os.environ`` mirror.  This helper consolidates
the pattern so the ``/api/config`` save path can reuse it as well — previously
that path only sent ``set_log_level`` IPC commands to subprocesses, leaving the
parent process logger at its old level until restart.
"""

from __future__ import annotations

import logging
import os

# Mirrors the historical accept-list — the rest of the bridge (UI dropdowns,
# HA addon options) only surfaces INFO and DEBUG, so any other value is
# treated as a fat-fingered config and silently downgraded to INFO.
_VALID_LEVELS: frozenset[str] = frozenset({"INFO", "DEBUG"})

# music-assistant-client includes its complete auth command in DEBUG output.
# Keep its WebSocket dependency at INFO too: wire logs add no operator value and
# can contain credentials supplied by a future upstream client implementation.
_SENSITIVE_DEPENDENCY_LOGGERS: tuple[str, ...] = ("music_assistant_client", "websockets")


def apply_log_level(level: str | None) -> str:
    """Normalize, validate, set the parent-process root logger, sync ``LOG_LEVEL``.

    Returns the canonical (uppercase, validated) level.  Falls back to ``INFO``
    when the input is empty or not recognised — defense-in-depth for callers
    that pass through unsanitised user input.
    """
    normalized = (level or "INFO").strip().upper()
    if normalized not in _VALID_LEVELS:
        normalized = "INFO"
    logging.getLogger().setLevel(getattr(logging, normalized))
    for name in _SENSITIVE_DEPENDENCY_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
    os.environ["LOG_LEVEL"] = normalized
    return normalized


__all__ = ["apply_log_level"]
