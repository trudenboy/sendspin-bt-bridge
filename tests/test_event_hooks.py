from __future__ import annotations

from services.event_hooks import EventHookRegistry
from services.internal_events import InternalEvent


def test_event_hook_registry_delivers_matching_events(monkeypatch):
    registry = EventHookRegistry()
    requests = []

    class _Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(request, timeout=0):
        requests.append(
            {
                "url": request.full_url,
                "body": request.data.decode("utf-8"),
                "headers": dict(request.header_items()),
                "timeout": timeout,
            }
        )
        return _Response()

    monkeypatch.setattr("services.event_hooks.urllib.request.urlopen", _fake_urlopen)
    hook = registry.register(url="https://example.com/hook", categories=["bridge_event"])

    count = registry.dispatch(
        InternalEvent(
            event_type="bridge.startup.completed",
            category="bridge_event",
            subject_id="bridge",
            payload={"active_clients": 2},
        ),
        background=False,
    )

    assert count == 1
    assert requests[0]["url"] == "https://example.com/hook"
    assert '"event_type": "bridge.startup.completed"' in requests[0]["body"]
    snapshot = registry.snapshot()
    assert snapshot["hooks"][0]["id"] == hook["id"]
    assert snapshot["hooks"][0]["success_count"] == 1
    assert snapshot["recent_deliveries"][0]["status"] == "success"


def test_event_hook_registry_records_delivery_failures(monkeypatch):
    registry = EventHookRegistry()

    def _fake_urlopen(_request, timeout=0):
        raise OSError(f"timeout after {timeout}s")

    monkeypatch.setattr("services.event_hooks.urllib.request.urlopen", _fake_urlopen)
    registry.register(url="https://example.com/hook", event_types=["device.event.recorded"], timeout_sec=1.5)

    count = registry.dispatch(
        InternalEvent(
            event_type="device.event.recorded",
            category="device_event",
            subject_id="sendspin-kitchen",
            payload={"event_type": "runtime-error"},
        ),
        background=False,
    )

    assert count == 1
    snapshot = registry.snapshot()
    assert snapshot["hooks"][0]["failure_count"] == 1
    assert "timeout after 1.5s" in snapshot["hooks"][0]["last_error"]
    assert snapshot["recent_deliveries"][0]["status"] == "failed"


def test_event_hook_registry_rejects_invalid_urls():
    registry = EventHookRegistry()

    try:
        registry.register(url="/relative/path")
    except ValueError as exc:
        assert str(exc) == "url must be an absolute http:// or https:// URL"
    else:
        raise AssertionError("Expected ValueError for invalid hook URL")


def test_event_hook_registry_unregister_removes_hook():
    registry = EventHookRegistry()
    hook = registry.register(url="https://example.com/hook")

    assert registry.unregister(hook["id"]) is True
    assert registry.unregister(hook["id"]) is False
    assert registry.snapshot()["summary"]["registered_hooks"] == 0
