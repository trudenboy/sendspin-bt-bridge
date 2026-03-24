"""Shared private helpers used across multiple services modules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc


def _device_extra(device: Any) -> dict[str, Any]:
    """Return the ``extra`` dict from a device object or dict, always as a dict."""
    if isinstance(device, dict):
        extra = device.get("extra")
    else:
        extra = getattr(device, "extra", None)
    return extra if isinstance(extra, dict) else {}


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a tz-aware datetime, or *None*."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
