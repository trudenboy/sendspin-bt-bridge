"""Shared recovery timeline builders for diagnostics, text reports, and export."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
RECOVERY_TIMELINE_VISIBLE_ENTRIES = 60


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
    total_entry_count = len(entries)
    visible_entries = entries[-RECOVERY_TIMELINE_VISIBLE_ENTRIES:]
    error_count = sum(1 for entry in visible_entries if entry.get("level") == "error")
    warning_count = sum(1 for entry in visible_entries if entry.get("level") == "warning")
    latest_at = visible_entries[-1].get("at") if visible_entries else None
    level_counts: dict[str, int] = {}
    source_type_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for entry in visible_entries:
        level = str(entry.get("level") or "info")
        source_type = str(entry.get("source_type") or "unknown")
        source = str(entry.get("source") or "Unknown source")
        level_counts[level] = level_counts.get(level, 0) + 1
        source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "summary": {
            "entry_count": len(visible_entries),
            "visible_entry_count": len(visible_entries),
            "total_entry_count": total_entry_count,
            "truncated_entry_count": max(0, total_entry_count - len(visible_entries)),
            "max_visible_entries": RECOVERY_TIMELINE_VISIBLE_ENTRIES,
            "error_count": error_count,
            "warning_count": warning_count,
            "latest_at": latest_at,
            "level_counts": level_counts,
            "source_type_counts": source_type_counts,
            "sources": [
                {"source": source, "count": count}
                for source, count in sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
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


def _format_timeline_entry_text(entry: dict[str, Any]) -> str:
    timestamp = str(entry.get("at") or "").strip() or "Unknown time"
    level = str(entry.get("level") or "info").strip().upper()
    source = str(entry.get("source") or "Unknown source").strip()
    label = str(entry.get("label") or "event").strip()
    summary = str(entry.get("summary") or "Recovery event").strip()
    return f"- {timestamp} [{level}] {source} / {label}: {summary}"


def build_recovery_timeline_text(timeline: dict[str, Any], *, max_entries: int = 8) -> str:
    """Render a recovery timeline payload as operator-readable plain text."""
    summary = (timeline or {}).get("summary") or {}
    entries = list((timeline or {}).get("entries") or [])
    if not entries:
        return "No recovery timeline entries were recorded."

    visible_entries = entries[-max_entries:] if max_entries > 0 else entries
    lines = [
        (
            "Recent recovery activity: "
            f"{int(summary.get('entry_count') or len(entries))} entries"
            f" · {int(summary.get('error_count') or 0)} errors"
            f" · {int(summary.get('warning_count') or 0)} warnings"
        )
    ]
    latest_at = str(summary.get("latest_at") or "").strip()
    if latest_at:
        lines.append(f"Latest activity: {latest_at}")
    lines.append("")
    lines.extend(_format_timeline_entry_text(entry) for entry in visible_entries)
    return "\n".join(lines)


def build_recovery_timeline_excerpt(timeline: dict[str, Any], *, max_entries: int = 2) -> str:
    """Return a short single-line recovery timeline summary for support text."""
    entries = list((timeline or {}).get("entries") or [])
    if not entries:
        return ""

    parts: list[str] = []
    for entry in entries[-max_entries:]:
        source = str(entry.get("source") or "Unknown source").strip()
        summary = str(entry.get("summary") or "Recovery event").strip()
        level = str(entry.get("level") or "info").strip()
        parts.append(f"{level} from {source}: {summary}")
    return "; ".join(parts)
