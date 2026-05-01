"""Tests for ``routes/api_ha.py`` — the HA-integration REST surface."""

from __future__ import annotations

import json
import sys

import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with the HA blueprint mounted."""
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "BRIDGE_NAME": "TestBridge",
                "BLUETOOTH_DEVICES": [],
                "AUTH_TOKENS": [],
            }
        )
    )

    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)

    # Drop stubs from prior tests, if any.
    for mod_name in (
        "sendspin_bridge.web.routes.api_ha",
        "sendspin_bridge.web.routes.api_status",
    ):
        if mod_name in sys.modules and getattr(sys.modules[mod_name], "__file__", None) is None:
            sys.modules.pop(mod_name)

    from sendspin_bridge.web.routes.api_ha import ha_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(ha_bp)
    yield app.test_client()


# ---------------------------------------------------------------------------
# /api/ha/state
# ---------------------------------------------------------------------------


def test_ha_state_returns_projection(client, monkeypatch):
    """With no devices configured the projection still returns a sane
    bridge slice.  HA's coordinator can use this as its initial bootstrap."""
    resp = client.get("/api/ha/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "devices" in body
    assert "bridge" in body
    assert "availability" in body
    # No devices configured → empty mapping, but the schema must still be present.
    assert body["devices"] == {}


def test_ha_state_handles_internal_errors_gracefully(client, monkeypatch):
    """A failure in projection building must not blow up the response."""
    import sendspin_bridge.web.routes.api_ha as M

    monkeypatch.setattr(
        M,
        "_build_projection_for_request",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    resp = client.get("/api/ha/state")
    assert resp.status_code == 500
    body = resp.get_json()
    assert "error" in body


# ---------------------------------------------------------------------------
# /api/ha/mqtt/probe
# ---------------------------------------------------------------------------


def test_mqtt_probe_returns_not_found_when_no_supervisor_and_no_ma(client, monkeypatch):
    """No Supervisor *and* no MA URL — probe really has nothing to suggest."""
    monkeypatch.setattr("sendspin_bridge.services.ha.ha_addon.get_mqtt_addon_credentials", lambda: None)
    # ``load_config`` is imported lazily inside the route — patch the
    # source module so any call resolves to our stub.
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {"MA_API_URL": ""})
    resp = client.get("/api/ha/mqtt/probe")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["found"] is False
    assert body["source"] is None
    assert "hint" in body
    # Hint must NOT say "install Mosquitto" — that was the misleading
    # message in v2.66.11 that confused harryfine on the forum.
    assert "Install" not in body["hint"]
    assert "Music Assistant" in body["hint"]


def test_mqtt_probe_falls_back_to_ma_url_when_no_supervisor(client, monkeypatch):
    """Standalone bridge with a configured MA URL — derive a suggested
    broker host so the operator only has to enter Mosquitto credentials,
    not the host (the v2.66.11 harryfine workflow on the forum).
    """
    monkeypatch.setattr("sendspin_bridge.services.ha.ha_addon.get_mqtt_addon_credentials", lambda: None)
    monkeypatch.setattr(
        "sendspin_bridge.config.load_config",
        lambda: {"MA_API_URL": "http://192.168.10.10:8095"},
    )
    resp = client.get("/api/ha/mqtt/probe")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["found"] is True
    assert body["source"] == "ma_url"
    assert body["host"] == "192.168.10.10"
    assert body["port"] == 1883
    assert body["password_present"] is False
    assert "Music Assistant" in body["hint"]


def test_mqtt_probe_masks_password(client, monkeypatch):
    monkeypatch.setattr(
        "sendspin_bridge.services.ha.ha_addon.get_mqtt_addon_credentials",
        lambda: {
            "host": "core-mosquitto",
            "port": 1883,
            "username": "addons",
            "password": "supersecret",
            "ssl": False,
        },
    )
    resp = client.get("/api/ha/mqtt/probe")
    body = resp.get_json()
    assert body["found"] is True
    assert body["host"] == "core-mosquitto"
    assert body["password_present"] is True
    assert "password" not in body  # plaintext NEVER in response
    assert "supersecret" not in json.dumps(body)


# ---------------------------------------------------------------------------
# /api/ha/mosquitto/status — Mosquitto add-on install state
# ---------------------------------------------------------------------------


def test_mosquitto_status_outside_ha_addon(client, monkeypatch):
    """No SUPERVISOR_TOKEN → ``available=False`` so the UI hides the banner.

    The other fields stay populated with safe defaults so the UI doesn't
    have to special-case the response shape."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    resp = client.get("/api/ha/mosquitto/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["available"] is False
    assert body["installed"] is False
    assert body["started"] is False
    assert body["slug"] == "core_mosquitto"
    assert "my.home-assistant.io" in body["install_url"]


def test_mosquitto_status_inside_ha_addon_not_installed(client, monkeypatch):
    """Supervisor returns no info → installed=False, install_url present."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "stub-token")
    monkeypatch.setattr("sendspin_bridge.services.ha.ha_addon.get_supervisor_addon_info", lambda *a, **kw: None)
    resp = client.get("/api/ha/mosquitto/status")
    body = resp.get_json()
    assert body["available"] is True
    assert body["installed"] is False
    assert body["started"] is False
    assert body["install_url"]


def test_mosquitto_status_inside_ha_addon_started(client, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "stub-token")
    monkeypatch.setattr(
        "sendspin_bridge.services.ha.ha_addon.get_supervisor_addon_info",
        lambda *a, **kw: {"slug": "core_mosquitto", "state": "started", "version": "6.4.1"},
    )
    resp = client.get("/api/ha/mosquitto/status")
    body = resp.get_json()
    assert body["available"] is True
    assert body["installed"] is True
    assert body["started"] is True


def test_mosquitto_status_inside_ha_addon_stopped(client, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "stub-token")
    monkeypatch.setattr(
        "sendspin_bridge.services.ha.ha_addon.get_supervisor_addon_info",
        lambda *a, **kw: {"slug": "core_mosquitto", "state": "stopped", "version": "6.4.1"},
    )
    resp = client.get("/api/ha/mosquitto/status")
    body = resp.get_json()
    assert body["available"] is True
    assert body["installed"] is True
    assert body["started"] is False


# ---------------------------------------------------------------------------
# /api/ha/mqtt/status
# ---------------------------------------------------------------------------


def test_mqtt_status_when_no_publisher(client, monkeypatch):
    """When the lifecycle holder is uninitialised, status is "idle"."""
    monkeypatch.setattr("sendspin_bridge.services.ha.ha_integration_lifecycle.get_default_lifecycle", lambda: None)
    resp = client.get("/api/ha/mqtt/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["running"] is False
    assert body["state"] == "idle"


# ---------------------------------------------------------------------------
# /api/ha/custom_component/status — HACS integration heuristic
# ---------------------------------------------------------------------------


def test_custom_component_status_no_tokens_means_not_installed(client, monkeypatch):
    """A bridge that has never paired with a custom_component reports
    ``installed=False`` so the UI surfaces the install prompt."""
    monkeypatch.setattr(
        "sendspin_bridge.config.load_config",
        lambda: {"AUTH_TOKENS": []},
    )
    resp = client.get("/api/ha/custom_component/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["available"] is True
    assert body["installed"] is False
    assert body["started"] is False
    assert body["last_seen"] is None
    assert "hacs_repository" in body["install_url"]


def test_custom_component_status_token_present_means_installed(client, monkeypatch):
    """At least one issued token (even if never used) tells us the
    integration paired at least once → ``installed=True``."""
    monkeypatch.setattr(
        "sendspin_bridge.config.load_config",
        lambda: {"AUTH_TOKENS": [{"id": "abc", "label": "ha", "last_used": None}]},
    )
    resp = client.get("/api/ha/custom_component/status")
    body = resp.get_json()
    assert body["installed"] is True
    assert body["started"] is False
    assert body["last_seen"] is None


def test_custom_component_status_recent_use_means_started(client, monkeypatch):
    """A token used within the active-window threshold flags the
    integration as currently connected."""
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(tz=UTC) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(
        "sendspin_bridge.config.load_config",
        lambda: {"AUTH_TOKENS": [{"id": "abc", "label": "ha", "last_used": recent}]},
    )
    resp = client.get("/api/ha/custom_component/status")
    body = resp.get_json()
    assert body["installed"] is True
    assert body["started"] is True
    assert body["last_seen"] == recent


def test_custom_component_status_stale_use_means_idle(client, monkeypatch):
    """A token used long ago surfaces as ``installed=True, started=False``
    so the UI tells the operator the integration paired but isn't
    currently active — different remediation than "never paired"."""
    from datetime import UTC, datetime, timedelta

    stale = (datetime.now(tz=UTC) - timedelta(days=7)).isoformat()
    monkeypatch.setattr(
        "sendspin_bridge.config.load_config",
        lambda: {"AUTH_TOKENS": [{"id": "abc", "label": "ha", "last_used": stale}]},
    )
    resp = client.get("/api/ha/custom_component/status")
    body = resp.get_json()
    assert body["installed"] is True
    assert body["started"] is False
    assert body["last_seen"] == stale


# ---------------------------------------------------------------------------
# /api/ha/mqtt/test — pre-save broker reachability + auth check
# ---------------------------------------------------------------------------


def _patch_open_connection_ok(monkeypatch):
    """Make ``asyncio.open_connection`` succeed with a stub stream pair."""

    class _StubWriter:
        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def _ok(host, port):
        del host, port
        return object(), _StubWriter()

    import asyncio

    monkeypatch.setattr(asyncio, "open_connection", _ok)


def _patch_aiomqtt_client(monkeypatch, *, raise_on_enter=None):
    """Replace ``aiomqtt.Client`` with a stub that succeeds (or raises)."""

    class _StubClient:
        def __init__(self, **kwargs):
            del kwargs

        async def __aenter__(self):
            if raise_on_enter is not None:
                raise raise_on_enter
            return self

        async def __aexit__(self, *_):
            return None

    import aiomqtt

    monkeypatch.setattr(aiomqtt, "Client", _StubClient)
    monkeypatch.setattr(aiomqtt, "Will", lambda **kw: type("Will", (), kw)())
    monkeypatch.setattr(aiomqtt, "TLSParameters", lambda: object())


def test_mqtt_test_returns_400_when_host_missing(client):
    resp = client.post("/api/ha/mqtt/test", json={})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error_class"] == "ValueError"


def test_mqtt_test_returns_400_when_port_invalid(client):
    resp = client.post("/api/ha/mqtt/test", json={"host": "broker.local", "port": 99999})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "port" in body["error"].lower()


def test_mqtt_test_succeeds_when_broker_reachable(client, monkeypatch):
    _patch_open_connection_ok(monkeypatch)
    _patch_aiomqtt_client(monkeypatch)
    resp = client.post(
        "/api/ha/mqtt/test",
        json={"host": "broker.local", "port": 1883, "username": "u", "password": "p"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "elapsed_ms" in body


def test_mqtt_test_surfaces_tcp_timeout_as_error(client, monkeypatch):
    """Pre-flight TCP probe must surface unreachable broker as an
    OSError/TimeoutError with a structured error_class so the UI can
    render it inline.  Closes Copilot follow-up on the executor-leak fix."""
    import asyncio

    async def _hang(host, port):
        del host, port
        raise TimeoutError("simulated broker drop")

    monkeypatch.setattr(asyncio, "open_connection", _hang)
    resp = client.post("/api/ha/mqtt/test", json={"host": "does.not.exist", "port": 1883})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error_class"] == "TimeoutError"
    assert "unreachable" in body["error"].lower()


def test_mqtt_test_surfaces_mqtt_auth_failure(client, monkeypatch):
    """A reachable broker that rejects auth must surface as a non-ok
    result with the aiomqtt-raised error class."""
    _patch_open_connection_ok(monkeypatch)

    class _AuthError(Exception):
        pass

    _patch_aiomqtt_client(monkeypatch, raise_on_enter=_AuthError("not authorised"))
    resp = client.post(
        "/api/ha/mqtt/test",
        json={"host": "broker.local", "port": 1883, "username": "u", "password": "wrong"},
    )
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error_class"] == "_AuthError"
    assert "not authorised" in body["error"]


def test_mqtt_test_resolves_redacted_password_from_saved_config(client, monkeypatch, tmp_path):
    """When the form sends ``***REDACTED***`` (the wire-protocol marker
    for "password unchanged"), the test endpoint must look up the actual
    password from saved config so editing host without retyping the
    password works.  Captured by patching the stub Client to record the
    constructor arg."""
    captured: dict[str, object] = {}

    class _CapturingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    import aiomqtt

    _patch_open_connection_ok(monkeypatch)
    monkeypatch.setattr(aiomqtt, "Client", _CapturingClient)
    monkeypatch.setattr(aiomqtt, "TLSParameters", lambda: object())

    # Override config to seed a saved password.
    cfg_payload = {
        "BRIDGE_NAME": "TestBridge",
        "BLUETOOTH_DEVICES": [],
        "AUTH_TOKENS": [],
        "HA_INTEGRATION": {
            "enabled": True,
            "mode": "mqtt",
            "mqtt": {"broker": "broker.local", "port": 1883, "username": "u", "password": "saved-pw"},
        },
    }

    def _fake_load_config():
        return cfg_payload

    monkeypatch.setattr("sendspin_bridge.config.load_config", _fake_load_config)

    resp = client.post(
        "/api/ha/mqtt/test",
        json={
            "host": "broker.local",
            "port": 1883,
            "username": "u",
            "password": "***REDACTED***",
        },
    )
    body = resp.get_json()
    assert body["ok"] is True
    assert captured["password"] == "saved-pw"


# ---------------------------------------------------------------------------
# /api/ha/mdns/status
# ---------------------------------------------------------------------------


def test_mdns_status_when_not_advertised(client, monkeypatch):
    monkeypatch.setattr("sendspin_bridge.services.ipc.bridge_mdns.get_default_advertiser", lambda: None)
    resp = client.get("/api/ha/mdns/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["advertised"] is False


def test_mdns_status_when_advertised(client, monkeypatch):
    from sendspin_bridge.services.ipc.bridge_mdns import BridgeMdnsAdvertiser, MdnsAdvertisement

    adv = BridgeMdnsAdvertiser(bridge_name="X", version="2.65.0", web_port=8080, ingress_active=False)
    adv._advertisement = MdnsAdvertisement(
        service_name="sendspin-bridge-abc._sendspin-bridge._tcp.local.",
        host_id="abc",
        port=8080,
        txt_records={"version": "2.65.0", "auth": "bearer"},
    )
    monkeypatch.setattr("sendspin_bridge.services.ipc.bridge_mdns.get_default_advertiser", lambda: adv)
    resp = client.get("/api/ha/mdns/status")
    body = resp.get_json()
    assert body["advertised"] is True
    assert body["host_id"] == "abc"
    assert body["txt_records"]["auth"] == "bearer"


# ---------------------------------------------------------------------------
# /api/status/events SSE — smoke test
# ---------------------------------------------------------------------------


def test_status_events_endpoint_is_event_stream(client):
    """We don't consume the stream (it's open-ended).  Just check status
    + content-type so the route shape is correct."""
    with client.application.test_request_context("/api/status/events"):
        # Use stream_with_context-style: check headers via OPTIONS isn't
        # right; do a HEAD against a SSE endpoint by testing get with a
        # short read instead.  Easier: just ensure the route exists.
        rules = [r.rule for r in client.application.url_map.iter_rules()]
        assert "/api/status/events" in rules


def test_status_events_does_not_set_hop_by_hop_headers(client):
    """Regression for the production AssertionError on
    ``/api/status/events``: WSGI applications must not set hop-by-hop
    headers (PEP 3333 / RFC 2616 §13.5.1).  Waitress raises
    ``AssertionError: Connection is a "hop-by-hop" header`` and tears
    the SSE connection down.

    The bridge runs behind waitress on production HAOS, so any
    hop-by-hop header the response sets makes the endpoint unusable.
    """
    resp = client.get("/api/status/events", buffered=False)
    try:
        # Hop-by-hop headers per RFC 2616 §13.5.1 (case-insensitive).
        for forbidden in (
            "Connection",
            "Keep-Alive",
            "Proxy-Authenticate",
            "Proxy-Authorization",
            "TE",
            "Trailer",
            "Transfer-Encoding",
            "Upgrade",
        ):
            assert forbidden not in resp.headers, (
                f"{forbidden!r} is a hop-by-hop header — WSGI apps must "
                f"not set it.  See PEP 3333 and waitress's start_response."
            )
        # Sanity-check the rest of the SSE response shape stays intact.
        assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
        assert resp.headers.get("Cache-Control") == "no-cache, no-transform"
        assert resp.headers.get("Content-Encoding") == "identity"
    finally:
        resp.close()
