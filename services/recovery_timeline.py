"""Shared recovery timeline builders for diagnostics and export."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc


def _parse_timestamp(value: Any) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return datetime.min.replace(tzinfo=UTC)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def build_recovery_timeline(devices: list[Any], startup_progress: dict[str, Any]) -> dict[str, Any]:
    """Build a chronological recovery timeline from startup and device events."""
    entries: list[dict[str, Any]] = []
    if startup_progress:
        entries.append(
            {
                "at": startup_progress.get("updated_at")
                or startup_progress.get("completed_at")
                or startup_progress.get("started_at"),
                "level": "error" if startup_progress.get("status") == "error" else "info",
                "source_type": "bridge",
                "source": "Bridge startup",
                "label": startup_progress.get("phase") or "startup",
                "summary": startup_progress.get("message") or "Startup progress available",
            }
        )

    for device in devices:
        name = str(getattr(device, "player_name", None) or "Device")
        recent_events = list(getattr(device, "recent_events", []) or [])
        if recent_events:
            for event in recent_events[:8]:
                entries.append(
                    {
                        "at": event.get("at"),
                        "level": event.get("level") or "info",
                        "source_type": "device",
                        "source": name,
                        "device_name": name,
                        "label": event.get("event_type") or "event",
                        "summary": event.get("message") or "Recovery event",
                    }
                )
            continue
        health = getattr(device, "health_summary", None) or {}
        state = str(health.get("state") or "")
        summary = str(health.get("summary") or "")
        if state in {"ready", "streaming"} or not summary:
            continue
        entries.append(
            {
                "at": None,
                "level": "error" if str(health.get("severity") or "") == "error" else "warning",
                "source_type": "device",
                "source": name,
                "device_name": name,
                "label": state or "health",
                "summary": summary,
            }
        )

    entries.sort(key=lambda entry: (_parse_timestamp(entry.get("at")), str(entry.get("source") or "")))
    visible_entries = entries[-30:]
    error_count = sum(1 for entry in visible_entries if entry.get("level") == "error")
    warning_count = sum(1 for entry in visible_entries if entry.get("level") == "warning")
    latest_at = visible_entries[-1].get("at") if visible_entries else None
    return {
        "summary": {
            "entry_count": len(visible_entries),
            "error_count": error_count,
            "warning_count": warning_count,
            "latest_at": latest_at,
        },
        "entries": visible_entries,
    }


def build_recovery_timeline_csv(timeline: dict[str, Any]) -> str:
    """Serialize a recovery timeline payload into CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["at", "level", "source_type", "source", "label", "summary"])
    for entry in (timeline or {}).get("entries") or []:
        writer.writerow(
            [
                entry.get("at") or "",
                entry.get("level") or "",
                entry.get("source_type") or "",
                entry.get("source") or "",
                entry.get("label") or "",
                entry.get("summary") or "",
            ]
        )
    return buffer.getvalue()
