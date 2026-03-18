from __future__ import annotations

import state
from services.internal_events import InternalEventPublisher


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
