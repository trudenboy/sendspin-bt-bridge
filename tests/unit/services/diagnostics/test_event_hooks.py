from __future__ import annotations

import socket

from sendspin_bridge.services.diagnostics.event_hooks import EventHookRegistry
from sendspin_bridge.services.diagnostics.internal_events import InternalEvent


def _allow_public_example(monkeypatch):
    monkeypatch.setattr(
        EventHookRegistry,
        "_resolve_host_addresses",
        staticmethod(lambda hostname, port, scheme: {"93.184.216.34"}),
    )


def test_event_hook_registry_delivers_matching_events(monkeypatch):
    _allow_public_example(monkeypatch)
    registry = EventHookRegistry()
    requests = []

    class _Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def _fake_urlopen(request, timeout=0, strict=None):
        requests.append(
            {
                "url": request.full_url,
                "body": request.data.decode("utf-8"),
                "headers": dict(request.header_items()),
                "timeout": timeout,
                "strict": strict,
            }
        )
        return _Response()

    monkeypatch.setattr("sendspin_bridge.services.diagnostics.event_hooks.safe_urlopen", _fake_urlopen)
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
    # Delivery must use the strict SSRF policy (block loopback / RFC1918 at
    # connect time), not the LAN-permissive default.
    assert requests[0]["strict"] is True
    snapshot = registry.snapshot()
    assert snapshot["hooks"][0]["id"] == hook["id"]
    assert snapshot["hooks"][0]["success_count"] == 1
    assert snapshot["recent_deliveries"][0]["status"] == "success"


def test_event_hook_registry_records_delivery_failures(monkeypatch):
    _allow_public_example(monkeypatch)
    registry = EventHookRegistry()

    def _fake_urlopen(_request, timeout=0, strict=None):
        raise OSError(f"timeout after {timeout}s")

    monkeypatch.setattr("sendspin_bridge.services.diagnostics.event_hooks.safe_urlopen", _fake_urlopen)
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


def test_event_hook_registry_rejects_private_network_targets(monkeypatch):
    monkeypatch.setattr(
        EventHookRegistry,
        "_resolve_host_addresses",
        staticmethod(lambda hostname, port, scheme: {"127.0.0.1"}),
    )
    registry = EventHookRegistry()

    try:
        registry.register(url="http://example.com/hook")
    except ValueError as exc:
        assert str(exc) == "url must not target loopback, local, or private network hosts"
    else:
        raise AssertionError("Expected ValueError for private target")


def test_event_hook_delivery_blocks_dns_rebinding_to_loopback(monkeypatch):
    """A hook whose host passed register-time validation (resolved public)
    must still be blocked at *delivery* time if the host now resolves to a
    disallowed peer — the DNS-rebinding TOCTOU.  Delivery must go through the
    SSRF-safe opener, not raw ``urllib.request.urlopen``."""
    import sendspin_bridge.services.infrastructure.url_safety as url_safety

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
    # Register time: host resolves to a public address, so registration passes.
    _allow_public_example(monkeypatch)
    registry = EventHookRegistry()
    registry.register(url="https://example.com/hook", categories=["bridge_event"], timeout_sec=2)

    # Delivery time: the host now resolves to loopback (rebinding).
    def _rebind_resolver(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", _rebind_resolver)

    # If the old code path is used, this raw urlopen would be hit and the
    # delivery would wrongly succeed — fail loudly so the test is a genuine
    # regression guard, not just an assertion on the new path.
    def _forbidden_raw_urlopen(*_a, **_k):
        raise AssertionError("delivery bypassed the SSRF-safe opener")

    monkeypatch.setattr(
        "sendspin_bridge.services.diagnostics.event_hooks.urllib.request.urlopen",
        _forbidden_raw_urlopen,
    )

    count = registry.dispatch(
        InternalEvent(
            event_type="bridge.startup.completed",
            category="bridge_event",
            subject_id="bridge",
            payload={},
        ),
        background=False,
    )

    assert count == 1
    snapshot = registry.snapshot()
    assert snapshot["hooks"][0]["failure_count"] == 1
    assert snapshot["hooks"][0]["success_count"] == 0
    assert snapshot["recent_deliveries"][0]["status"] == "failed"


def test_event_hook_registry_unregister_removes_hook(monkeypatch):
    _allow_public_example(monkeypatch)
    registry = EventHookRegistry()
    hook = registry.register(url="https://example.com/hook")

    assert registry.unregister(hook["id"]) is True
    assert registry.unregister(hook["id"]) is False
    assert registry.snapshot()["summary"]["registered_hooks"] == 0
