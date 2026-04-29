from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.services.diagnostics.recovery_timeline import (
    RECOVERY_TIMELINE_VISIBLE_ENTRIES,
    build_recovery_timeline,
)


def _device(name: str, event_count: int) -> SimpleNamespace:
    recent_events = [
        {
            "at": f"2026-03-23T10:{index:02d}:00+00:00",
            "level": "warning" if index % 3 else "error",
            "event_type": f"event_{index}",
            "message": f"{name} event {index}",
        }
        for index in range(event_count)
    ]
    return SimpleNamespace(player_name=name, recent_events=recent_events, health_summary={})


def test_build_recovery_timeline_exposes_truncation_and_filter_metadata():
    devices = [_device(f"Speaker {index}", 8) for index in range(8)]

    timeline = build_recovery_timeline(devices, {})

    summary = timeline["summary"]
    entries = timeline["entries"]
    assert len(entries) == RECOVERY_TIMELINE_VISIBLE_ENTRIES
    assert summary["entry_count"] == RECOVERY_TIMELINE_VISIBLE_ENTRIES
    assert summary["visible_entry_count"] == RECOVERY_TIMELINE_VISIBLE_ENTRIES
    assert summary["total_entry_count"] == 64
    assert summary["truncated_entry_count"] == 4
    assert summary["max_visible_entries"] == RECOVERY_TIMELINE_VISIBLE_ENTRIES
    assert summary["level_counts"]["error"] > 0
    assert summary["level_counts"]["warning"] > 0
    assert summary["source_type_counts"]["device"] == RECOVERY_TIMELINE_VISIBLE_ENTRIES
    assert summary["sources"][0]["source"].startswith("Speaker ")
    assert summary["sources"][0]["count"] == 8


def test_build_recovery_timeline_tracks_bridge_source_counts():
    timeline = build_recovery_timeline(
        [_device("Kitchen", 2)],
        {
            "status": "error",
            "phase": "audio",
            "message": "Audio backend unavailable",
            "updated_at": "2026-03-23T10:30:00+00:00",
        },
    )

    summary = timeline["summary"]
    bridge_source = next(item for item in summary["sources"] if item["source"] == "Bridge startup")
    assert bridge_source["count"] == 1
    assert summary["source_type_counts"]["bridge"] == 1
    assert summary["level_counts"]["error"] >= 1
