from __future__ import annotations

import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import state
from services.internal_events import DeviceEventType, InternalEventPublisher, normalize_device_event


def test_internal_event_publisher_notifies_subscribers():
    publisher = InternalEventPublisher()
    received = []
    unsubscribe = publisher.subscribe(received.append)

    try:
        event = publisher.publish(
            event_type="device.event.recorded",
            category="device_event",
            subject_id="sendspin-kitchen",
            payload={"event_type": "runtime-error"},
        )
    finally:
        unsubscribe()

    assert event is not None
    assert received == [event]
    assert received[0].payload["event_type"] == "runtime-error"


def test_publish_device_event_persists_through_state_event_bus():
    state.clear_device_events("sendspin-kitchen")

    try:
        event = state.publish_device_event(
            "sendspin-kitchen",
            "runtime-error",
            level="error",
            message="Route degraded",
            details={"last_error_at": "2026-03-18T09:00:00+00:00"},
        )
        stored = state.get_device_events("sendspin-kitchen")
    finally:
        state.clear_device_events("sendspin-kitchen")

    assert event is not None
    assert stored[0]["event_type"] == "runtime-error"
    assert stored[0]["level"] == "error"
    assert stored[0]["message"] == "Route degraded"
    assert stored[0]["details"] == {"last_error_at": "2026-03-18T09:00:00+00:00"}


def test_publish_device_event_enriches_details_with_room_and_readiness_context(monkeypatch):
    client = SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": False,
            "uptime_start": datetime.now(tz=timezone.utc),
        },
        _status_lock=threading.Lock(),
        player_name="Kitchen",
        player_id="sendspin-kitchen",
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=-500.0,
        connected_server_url="",
        bt_manager=SimpleNamespace(
            mac_address="AA:BB:CC:DD:EE:FF",
            effective_adapter_mac="11:22:33:44:55:66",
            adapter="hci0",
            adapter_hci_name="hci0",
            battery_level=88,
        ),
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )
    monkeypatch.setattr(
        "services.status_snapshot.load_config",
        lambda: {
            "BLUETOOTH_DEVICES": [
                {
                    "player_name": "Kitchen",
                    "room_id": "living-room",
                    "room_name": "Living Room",
                }
            ]
        },
    )
    state.set_clients([client])
    state.clear_device_events("sendspin-kitchen")

    try:
        event = state.publish_device_event(
            "sendspin-kitchen",
            "bluetooth-reconnected",
            details={"attempt": 2},
        )
        stored = state.get_device_events("sendspin-kitchen")
    finally:
        state.clear_device_events("sendspin-kitchen")
        state.set_clients([])

    assert event is not None
    assert stored[0]["details"]["attempt"] == 2
    assert stored[0]["details"]["room_id"] == "living-room"
    assert stored[0]["details"]["room_name"] == "Living Room"
    assert "handoff_mode" not in stored[0]["details"]
    assert stored[0]["details"]["transfer_readiness"]["ready"] is True


def test_normalize_device_event_applies_defaults_and_drops_none_details():
    normalized = normalize_device_event(
        DeviceEventType.BLUETOOTH_RECONNECTED,
        message="Bluetooth reconnect succeeded",
        details={"attempt": 2, "next_retry_delay": None},
    )

    assert normalized == {
        "event_type": "bluetooth-reconnected",
        "level": "info",
        "message": "Bluetooth reconnect succeeded",
        "details": {"attempt": 2},
    }
