""" "Music Assistant is down" and "it does not know this speaker" are not the same.

The queue capability decided whether Music Assistant was reachable by looking
at the device's own now-playing cache.  A speaker Music Assistant has no queue
for therefore produced "Music Assistant API is not connected" — on a bridge
whose API connection, socket and queue polling were all healthy — and offered
"open Music Assistant settings", which cannot fix it.

The two states need different words because they need different actions: one
is repaired in the MA settings, the other by getting the speaker registered
with Music Assistant again.
"""

from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.services.bluetooth.device_health_state import build_device_capabilities


def _device(*, ma_connected: bool, queue_known: bool, server_connected: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        player_name="Kitchen",
        server_connected=server_connected,
        bluetooth_connected=True,
        bt_management_enabled=True,
        has_sink=True,
        enabled=True,
        extra={"ma_connected": ma_connected},
        ma_now_playing={"connected": True} if queue_known else {},
    )


def _queue(device) -> dict:
    return build_device_capabilities(device)["actions"]["queue_control"]


def test_a_reachable_queue_is_available():
    capability = _queue(_device(ma_connected=True, queue_known=True))

    assert capability["currently_available"] is True
    assert capability["blocked_reason"] is None


def test_music_assistant_being_down_is_named_as_such():
    capability = _queue(_device(ma_connected=False, queue_known=False))

    assert capability["currently_available"] is False
    assert capability["blocked_reason_detail"]["code"] == "ma_disconnected"
    assert capability["blocked_reason_detail"]["recommended_action"] == "open_ma_settings"


def test_a_speaker_music_assistant_does_not_know_is_a_different_problem():
    capability = _queue(_device(ma_connected=True, queue_known=False))

    detail = capability["blocked_reason_detail"]
    assert capability["currently_available"] is False
    assert detail["code"] == "ma_queue_unknown"
    assert "not connected" not in capability["blocked_reason"], capability["blocked_reason"]
    assert detail["recommended_action"] != "open_ma_settings"


def test_the_sendspin_daemon_still_wins_over_both():
    """A daemon that is down is the first thing to fix, whatever MA says."""
    capability = _queue(_device(ma_connected=True, queue_known=True, server_connected=False))

    assert capability["blocked_reason_detail"]["code"] == "daemon_disconnected"


# ── where the runtime flag comes from ────────────────────────────────────


def test_the_device_snapshot_carries_the_runtime_music_assistant_flag(monkeypatch):
    """The capability cannot ask the runtime itself — the snapshot tells it."""
    import threading
    from datetime import datetime, timezone

    import sendspin_bridge.services.lifecycle.status_snapshot as status_snapshot

    monkeypatch.setattr(status_snapshot, "is_ma_connected", lambda: True, raising=False)

    client = SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": False,
            "volume": 50,
            "uptime_start": datetime.now(tz=timezone.utc),
        },
        _status_lock=threading.Lock(),
        player_name="Kitchen",
        player_id="sendspin-kitchen",
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=0.0,
        connected_server_url="",
        bt_manager=None,
        bluetooth_sink_name="bluez_sink.AA.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )

    snapshot = status_snapshot.build_device_snapshot(client)

    assert snapshot.extra["ma_connected"] is True
