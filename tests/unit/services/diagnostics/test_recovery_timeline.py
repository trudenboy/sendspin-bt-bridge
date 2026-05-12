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


def test_build_recovery_timeline_downgrades_runtime_errors_resolved_by_cleared_event():
    """A ``runtime-error`` followed by a ``runtime-error-cleared`` event must
    be surfaced as resolved (level downgraded, summary annotated) so a
    startup-race connection failure doesn't keep ringing as a current
    concern after the bridge has reconnected (issue #296).
    """
    device = SimpleNamespace(
        player_name="Sony Black",
        health_summary={},
        recent_events=[
            {
                "at": "2026-05-12T14:25:00+00:00",
                "level": "info",
                "event_type": "runtime-error-cleared",
                "message": "Runtime error cleared",
                "details": {"cleared_error": "Cannot connect to Sendspin server at ws://10.0.0.5:8927/sendspin."},
            },
            {
                "at": "2026-05-12T14:21:30+00:00",
                "level": "error",
                "event_type": "runtime-error",
                "message": "Cannot connect to Sendspin server at ws://10.0.0.5:8927/sendspin.",
                "details": {},
            },
        ],
    )

    timeline = build_recovery_timeline([device], {})

    entries = timeline["entries"]
    runtime_error_entry = next(entry for entry in entries if entry["label"] == "runtime-error")
    assert runtime_error_entry["level"] == "info"
    assert "(recovered)" in runtime_error_entry["summary"]
    # Sanity: the runtime-error-cleared entry itself stays an info event.
    cleared_entry = next(entry for entry in entries if entry["label"] == "runtime-error-cleared")
    assert cleared_entry["level"] == "info"


def test_build_recovery_timeline_keeps_runtime_error_when_not_yet_cleared():
    """An unresolved runtime-error must still surface as an error in the timeline."""
    device = SimpleNamespace(
        player_name="Sony Black",
        health_summary={},
        recent_events=[
            {
                "at": "2026-05-12T14:21:30+00:00",
                "level": "error",
                "event_type": "runtime-error",
                "message": "Cannot connect to Sendspin server at ws://10.0.0.5:8927/sendspin.",
                "details": {},
            },
        ],
    )

    timeline = build_recovery_timeline([device], {})

    entries = timeline["entries"]
    runtime_error_entry = next(entry for entry in entries if entry["label"] == "runtime-error")
    assert runtime_error_entry["level"] == "error"
    assert "(recovered)" not in runtime_error_entry["summary"]
