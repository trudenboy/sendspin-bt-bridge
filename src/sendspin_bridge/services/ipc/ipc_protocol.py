"""Shared versioned contract helpers for parent↔subprocess IPC."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

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


def coerce_message_dict(payload: object) -> dict[str, Any] | None:
    """Return a shallow dict copy for JSON objects; ignore non-object payloads."""
    if isinstance(payload, dict):
        return dict(payload)
    return None


@dataclass(frozen=True)
class StatusEnvelope:
    """Normalized daemon status envelope."""

    protocol_version: int | None
    updates: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LogEnvelope:
    """Normalized daemon log envelope."""

    protocol_version: int | None
    level: str
    name: str
    msg: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorEnvelope:
    """Normalized daemon error envelope."""

    protocol_version: int | None
    error_code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandEnvelope:
    """Normalized parent→daemon command envelope."""

    protocol_version: int | None
    cmd: str
    payload: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def build_status_envelope(status: Mapping[str, Any]) -> dict[str, Any]:
    """Build a status envelope with the current protocol version."""
    return with_protocol_version({"type": "status", **dict(status)})


def build_log_envelope(*, level: str = "info", name: str = "", msg: str = "") -> dict[str, Any]:
    """Build a log envelope with explicit defaults."""
    return with_protocol_version(
        {"type": "log", "level": str(level or "info"), "name": str(name or ""), "msg": str(msg or "")}
    )


def build_error_envelope(
    error_code: str,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an error envelope with normalized details."""
    return with_protocol_version(
        {
            "type": "error",
            "error_code": str(error_code or ""),
            "message": str(message or ""),
            "details": dict(details or {}),
        }
    )


def build_command_envelope(cmd: str, **payload: Any) -> dict[str, Any]:
    """Build a command envelope with the current protocol version."""
    return with_protocol_version({"cmd": str(cmd or ""), **payload})


def parse_status_envelope(message: object, *, allowed_keys: frozenset[str] | None = None) -> StatusEnvelope | None:
    """Parse and normalize a daemon status envelope."""
    raw = coerce_message_dict(message)
    if raw is None or raw.get("type") != "status":
        return None
    excluded = {"type", IPC_PROTOCOL_VERSION_KEY}
    if allowed_keys is None:
        updates = {key: value for key, value in raw.items() if key not in excluded}
    else:
        updates = {key: value for key, value in raw.items() if key in allowed_keys and key not in excluded}
    return StatusEnvelope(
        protocol_version=parse_protocol_version(raw.get(IPC_PROTOCOL_VERSION_KEY)),
        updates=updates,
        raw=raw,
    )


def parse_log_envelope(message: object) -> LogEnvelope | None:
    """Parse and normalize a daemon log envelope."""
    raw = coerce_message_dict(message)
    if raw is None or raw.get("type") != "log":
        return None
    return LogEnvelope(
        protocol_version=parse_protocol_version(raw.get(IPC_PROTOCOL_VERSION_KEY)),
        level=str(raw.get("level") or "info"),
        name=str(raw.get("name") or ""),
        msg=str(raw.get("msg") or ""),
        raw=raw,
    )


def parse_error_envelope(message: object) -> ErrorEnvelope | None:
    """Parse and normalize a daemon error envelope."""
    raw = coerce_message_dict(message)
    if raw is None or raw.get("type") != "error":
        return None
    details = raw.get("details")
    return ErrorEnvelope(
        protocol_version=parse_protocol_version(raw.get(IPC_PROTOCOL_VERSION_KEY)),
        error_code=str(raw.get("error_code") or ""),
        message=str(raw.get("message") or ""),
        details=dict(details) if isinstance(details, dict) else {},
        raw=raw,
    )


def parse_command_envelope(message: object) -> CommandEnvelope | None:
    """Parse and normalize a parent→daemon command envelope."""
    raw = coerce_message_dict(message)
    if raw is None:
        return None
    cmd = str(raw.get("cmd") or "").strip()
    if not cmd:
        return None
    payload = {key: value for key, value in raw.items() if key not in {"cmd", IPC_PROTOCOL_VERSION_KEY}}
    return CommandEnvelope(
        protocol_version=parse_protocol_version(raw.get(IPC_PROTOCOL_VERSION_KEY)),
        cmd=cmd,
        payload=payload,
        raw=raw,
    )
