"""``/hooks``: runtime webhooks for bridge and device events."""

from __future__ import annotations

import pytest

from sendspin_bridge.services.diagnostics.event_hooks import EventHookRegistry, get_event_hook_registry


@pytest.fixture(autouse=True)
def hooks():
    registry = get_event_hook_registry()
    registry.clear()
    yield registry
    registry.clear()


@pytest.fixture
def resolves_to(monkeypatch):
    def _set(address: str) -> None:
        monkeypatch.setattr(EventHookRegistry, "_resolve_host_addresses", staticmethod(lambda h, p, s: {address}))

    return _set


def test_register_list_and_remove(api_client, resolves_to):
    resolves_to("93.184.216.34")

    created = api_client.post("/api/v1/hooks", json={"url": "https://example.com/hook", "categories": ["bridge_event"]})
    assert created.status_code == 201
    hook = created.json()

    listed = api_client.get("/api/v1/hooks").json()
    assert listed["summary"]["registered_hooks"] == 1
    assert listed["hooks"][0]["id"] == hook["id"]

    assert api_client.delete(f"/api/v1/hooks/{hook['id']}").status_code == 204
    assert api_client.delete(f"/api/v1/hooks/{hook['id']}").status_code == 404
    assert api_client.get("/api/v1/hooks").json()["summary"]["registered_hooks"] == 0


def test_a_relative_url_is_refused(api_client):
    resp = api_client.post("/api/v1/hooks", json={"url": "/relative/path"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "url must be an absolute http:// or https:// URL"


def test_a_private_network_target_is_refused(api_client, resolves_to):
    resolves_to("127.0.0.1")
    resp = api_client.post("/api/v1/hooks", json={"url": "http://example.com/hook"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "url must not target loopback, local, or private network hosts"


@pytest.mark.parametrize("timeout", [{"seconds": 5}, "soon", 0, 120])
def test_timeout_must_be_a_sensible_number(api_client, timeout):
    resp = api_client.post("/api/v1/hooks", json={"url": "https://example.com/hook", "timeout_sec": timeout})
    assert resp.status_code == 422
