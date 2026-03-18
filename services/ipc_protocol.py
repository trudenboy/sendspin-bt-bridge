"""Shared versioned contract helpers for parent↔subprocess IPC."""

from __future__ import annotations

from typing import Any

IPC_PROTOCOL_VERSION = 1
IPC_PROTOCOL_VERSION_KEY = "protocol_version"


def with_protocol_version(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of payload with the current protocol version attached."""
    message = dict(payload)
    message.setdefault(IPC_PROTOCOL_VERSION_KEY, IPC_PROTOCOL_VERSION)
    return message


def parse_protocol_version(value: object) -> int | None:
    """Return an integer protocol version or None when it is absent/invalid."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def is_compatible_protocol_version(value: object) -> bool:
    """Treat missing version as legacy-compatible; require exact match otherwise."""
    parsed = parse_protocol_version(value)
    return parsed is None or parsed == IPC_PROTOCOL_VERSION
