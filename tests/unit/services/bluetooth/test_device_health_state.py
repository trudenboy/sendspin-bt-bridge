from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.services.bluetooth.device_health_state import compute_device_health_state


def test_device_health_state_prefers_active_audio_over_control_disconnect():
    state = compute_device_health_state(
        SimpleNamespace(
            bt_management_enabled=True,
            bluetooth_connected=True,
            server_connected=False,
            playing=True,
            recent_events=[],
            extra={"audio_streaming": True},
        )
    )

    assert state.state == "streaming"
    assert state.summary == "Streaming audio"


def test_device_health_state_treats_planned_ma_reconnect_as_recovering():
    state = compute_device_health_state(
        SimpleNamespace(
            bt_management_enabled=True,
            bluetooth_connected=True,
            server_connected=False,
            playing=False,
            recent_events=[],
            extra={"ma_reconnecting": True},
        )
    )

    assert state.state == "recovering"
    assert state.summary == "Refreshing Music Assistant connection"
    assert "ma_reconnecting" in state.reasons


def test_device_health_state_degraded_when_sink_muted_but_app_not_muted():
    state = compute_device_health_state(
        SimpleNamespace(
            bt_management_enabled=True,
            bluetooth_connected=True,
            bluetooth_sink_name="bluez_sink.AA_BB.a2dp_sink",
            server_connected=True,
            playing=False,
            recent_events=[],
            extra={"sink_muted": True, "muted": False},
        )
    )

    assert state.state == "degraded"
    assert state.summary == "Audio sink muted at system level"
    assert "sink_muted_at_system_level" in state.reasons


def test_device_health_state_not_degraded_when_both_muted():
    """When user explicitly muted, sink_muted is expected — not degraded."""
    state = compute_device_health_state(
        SimpleNamespace(
            bt_management_enabled=True,
            bluetooth_connected=True,
            bluetooth_sink_name="bluez_sink.AA_BB.a2dp_sink",
            server_connected=True,
            playing=False,
            recent_events=[],
            extra={"sink_muted": True, "muted": True},
        )
    )

    assert state.state in ("ready", "idle")
    assert "sink_muted_at_system_level" not in state.reasons


def test_device_health_state_not_degraded_when_sink_muted_but_disconnected():
    """Sink mute on disconnected device is not a desync — device isn't usable anyway."""
    state = compute_device_health_state(
        SimpleNamespace(
            bt_management_enabled=True,
            bluetooth_connected=False,
            bluetooth_sink_name="bluez_sink.AA_BB.a2dp_sink",
            server_connected=False,
            playing=False,
            recent_events=[],
            extra={"sink_muted": True, "muted": False},
        )
    )

    assert state.state == "offline"


def _connected(**extra):
    return SimpleNamespace(
        bt_management_enabled=True,
        bluetooth_connected=True,
        bluetooth_sink_name="bluez_sink.AA_BB.a2dp_sink",
        server_connected=True,
        connected=True,
        playing=False,
        recent_events=[],
        extra=extra,
    )


def test_a_player_on_a_foreign_music_assistant_is_a_warning_not_ready():
    state = compute_device_health_state(
        _connected(connected_server_own=False, connected_server_host="192.168.10.229", connected_server_name="Test MA")
    )

    assert state.state == "degraded"
    assert state.severity == "warning"
    assert state.summary == (
        "Connected to Music Assistant on 192.168.10.229 (Test MA), not to this bridge's Music Assistant"
    )
    assert "foreign_server" in state.reasons


def test_a_player_on_the_own_or_an_unknown_music_assistant_is_ready():
    for own in (True, None):
        state = compute_device_health_state(_connected(connected_server_own=own, connected_server_host="1.2.3.4"))
        assert state.state == "ready"
        assert state.summary == "Connected and ready"
