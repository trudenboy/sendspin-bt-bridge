"""The HTTPS capture server: a self-signed certificate kept in the config dir, and an app that serves
only the capture page and the capture token's endpoints."""

from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from fastapi.testclient import TestClient

from sendspin_bridge.sync_tuning import capture_https


def test_the_certificate_is_made_once_for_the_bridge_address_and_kept(tmp_path):
    cert, key = capture_https.ensure_certificate(tmp_path, ["192.168.1.5"])
    first = cert.read_bytes()
    loaded = x509.load_pem_x509_certificate(first)
    ips = [
        str(ip)
        for ip in loaded.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
            x509.IPAddress
        )
    ]
    assert "192.168.1.5" in ips and "127.0.0.1" in ips
    assert loaded.not_valid_after_utc - dt.datetime.now(dt.UTC) > dt.timedelta(days=3000)
    assert (key.stat().st_mode & 0o777) == 0o600
    again, _ = capture_https.ensure_certificate(tmp_path, ["192.168.1.5"])
    assert again.read_bytes() == first  # kept: the phone accepts it once
    moved, _ = capture_https.ensure_certificate(tmp_path, ["10.0.0.7"])
    assert moved.read_bytes() != first  # a new address gets a new certificate


def test_the_port_follows_the_web_port_and_can_be_changed_or_turned_off(monkeypatch):
    monkeypatch.setattr(capture_https, "resolve_web_port", lambda: 8080)
    monkeypatch.delenv("SYNC_CAPTURE_HTTPS_PORT", raising=False)
    assert capture_https.https_port() == 8443
    monkeypatch.setenv("SYNC_CAPTURE_HTTPS_PORT", "9443")
    assert capture_https.https_port() == 9443
    monkeypatch.setenv("SYNC_CAPTURE_HTTPS_PORT", "0")
    assert capture_https.https_port() is None


@pytest.mark.parametrize(
    "path",
    ["/api/v1/sync/groups", "/api/v1/config", "/api/v1/sync/sessions/x", "/", "/api/v1/openapi.json", "/api/v1/docs"],
)
def test_the_capture_app_serves_nothing_but_the_capture_page_and_its_endpoints(path):
    client = TestClient(capture_https.build_capture_app(), raise_server_exceptions=False)
    assert client.get(path).status_code == 404


def test_the_capture_app_has_the_capture_routes():
    client = TestClient(capture_https.build_capture_app(), raise_server_exceptions=False)
    token = "z" * 43
    for method, path in (
        ("get", f"/api/v1/sync/capture/{token}"),
        ("post", f"/api/v1/sync/capture/{token}/start"),
        ("post", f"/api/v1/sync/capture/{token}/recording"),
    ):
        resp = getattr(client, method)(path, json={"mode": "live"}) if method == "post" else client.get(path)
        assert resp.status_code == 404 and resp.json()["detail"] == "This capture link is not valid any more", path
    page = client.get(f"/sync/capture/{token}")
    assert page.status_code == 404 and page.headers["content-type"].startswith("text/html")


def test_the_server_starts_on_its_own_port_and_a_busy_port_leaves_http(tmp_path, monkeypatch):
    import asyncio
    import socket
    import ssl
    import urllib.request

    from sendspin_bridge import config as cfg

    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        "sendspin_bridge.sync_tuning.click_track_host.default_base_url", lambda: "http://127.0.0.1:8080"
    )
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("SYNC_CAPTURE_HTTPS_PORT", str(port))

    async def scenario():
        server = await capture_https.serve_capture_https()
        assert server is not None
        for _ in range(100):
            if capture_https.active_port() == port:
                break
            await asyncio.sleep(0.02)
        assert capture_https.active_port() == port
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        loop = asyncio.get_running_loop()

        def fetch(path):
            try:
                return urllib.request.urlopen(f"https://127.0.0.1:{port}{path}", context=ctx, timeout=5).status
            except urllib.error.HTTPError as e:
                return e.code

        assert await loop.run_in_executor(None, fetch, "/api/v1/sync/capture/" + "z" * 43) == 404
        assert await loop.run_in_executor(None, fetch, "/api/v1/config") == 404
        # a second server on the same (now busy) port does not start, and nothing exits
        assert await capture_https.serve_capture_https() is None
        await capture_https.stop_capture_https()

    asyncio.run(scenario())
