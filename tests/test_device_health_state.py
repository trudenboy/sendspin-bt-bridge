from __future__ import annotations

from types import SimpleNamespace

from services.device_health_state import compute_device_health_state


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
