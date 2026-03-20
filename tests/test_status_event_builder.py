from __future__ import annotations

from services.status_event_builder import StatusEventBuilder


def test_build_emits_expected_event_sequence_for_transition_batch():
    previous = {
        "bluetooth_connected": False,
        "server_connected": False,
        "playing": False,
        "audio_streaming": False,
        "reconnecting": False,
        "reanchoring": False,
        "last_error": None,
    }
    current = {
        "bluetooth_connected": True,
        "server_connected": True,
        "playing": True,
        "audio_streaming": True,
        "reconnecting": True,
        "reconnect_attempt": 2,
        "reanchoring": True,
        "reanchor_count": 1,
        "current_track": "Track",
        "current_artist": "Artist",
        "last_error": "Route degraded",
        "last_error_at": "2026-03-18T08:40:00+00:00",
    }
    updates = {
        "bluetooth_connected": True,
        "server_connected": True,
        "playing": True,
        "audio_streaming": True,
        "reconnecting": True,
        "reanchoring": True,
        "last_error": "Route degraded",
    }

    events = StatusEventBuilder.build(previous, current, updates)

    assert [event["event_type"] for event in events] == [
        "bluetooth-connected",
        "daemon-connected",
        "playback-started",
        "audio-streaming",
        "reconnecting",
        "reanchoring",
        "runtime-error",
    ]
    assert events[2]["details"] == {}
    assert events[4]["details"] == {"attempt": 2}
    assert events[5]["details"] == {"reanchor_count": 1}
    assert events[-1]["details"] == {"last_error_at": "2026-03-18T08:40:00+00:00"}


def test_build_emits_reconnect_recovery_and_management_disable_events():
    previous = {
        "bluetooth_connected": False,
        "reconnecting": True,
        "reconnect_attempt": 3,
        "audio_streaming": True,
        "playing": True,
        "bt_management_enabled": True,
    }
    current = {
        "bluetooth_connected": True,
        "reconnecting": False,
        "reconnect_attempt": 0,
        "audio_streaming": False,
        "playing": True,
        "bt_management_enabled": False,
        "bt_released_by": "auto",
    }
    updates = {
        "bluetooth_connected": True,
        "reconnecting": False,
        "audio_streaming": False,
        "bt_management_enabled": False,
    }

    events = StatusEventBuilder.build(previous, current, updates)

    assert [event["event_type"] for event in events] == [
        "bluetooth-connected",
        "audio-stream-stalled",
        "bluetooth-reconnected",
        "bt-management-disabled",
    ]
    assert events[2]["details"] == {"attempt": 3}
    assert events[3]["details"] == {"released_by": "auto"}
