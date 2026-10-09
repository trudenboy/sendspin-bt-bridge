"""Ingress prefix, security headers, compression and the SPA catch-all."""

from __future__ import annotations

import gzip

import pytest
from fastapi import Request  # noqa: TC002 - FastAPI reads the annotation at runtime

from tests.support.api_client import make_client


@pytest.fixture
def spa(tmp_path):
    root = tmp_path / "spa"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><html><head><script src="./assets/app-1a2b.js"></script></head></html>'
    )
    (root / "assets" / "app-1a2b.js").write_text("console.log(1);" * 200)
    (root / "favicon.svg").write_text("<svg/>")
    return root


@pytest.fixture(autouse=True)
def _standalone(monkeypatch, tmp_config):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)


# -- ingress -----------------------------------------------------------------------


def _ingress_echo(client):
    @client.app.get("/api/v1/echo-root")
    def _echo(request: Request) -> dict[str, str]:
        return {"root_path": request.scope.get("root_path", ""), "url": str(request.url_for("_echo"))}

    return client


def test_trusted_peer_sets_the_ingress_prefix():
    client = _ingress_echo(make_client(peer="172.30.32.2"))
    body = client.get("/api/v1/echo-root", headers={"X-Ingress-Path": "/api/hassio_ingress/tok/"}).json()
    assert body["root_path"] == "/api/hassio_ingress/tok"
    assert body["url"].endswith("/api/hassio_ingress/tok/api/v1/echo-root")


def test_untrusted_peer_cannot_set_the_prefix():
    client = _ingress_echo(make_client(peer="192.168.1.50"))
    body = client.get("/api/v1/echo-root", headers={"X-Ingress-Path": "/evil"}).json()
    assert body["root_path"] == ""


@pytest.mark.parametrize("value", ["//evil.example/x", "evil", "http://evil.example"])
def test_unsafe_prefixes_are_ignored(value):
    client = _ingress_echo(make_client(peer="172.30.32.2"))
    assert client.get("/api/v1/echo-root", headers={"X-Ingress-Path": value}).json()["root_path"] == ""


def test_a_path_that_starts_with_the_prefix_still_routes():
    """The HA integration sends ``X-Ingress-Path: /api/auth/ha-pair`` to that very path."""
    client = make_client(peer="172.30.32.2")
    resp = client.get("/api/v1/health", headers={"X-Ingress-Path": "/api/v1/health"})
    assert resp.status_code == 200


# -- headers -------------------------------------------------------------------------


def test_json_gets_nosniff_but_no_html_policy():
    resp = make_client().get("/api/v1/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" not in resp.headers
    assert resp.headers.get("Cache-Control") != "no-cache, no-store, must-revalidate"


def test_html_gets_a_strict_policy_and_no_store(spa):
    resp = make_client(spa_dir=spa).get("/")
    assert resp.status_code == 200
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "frame-ancestors 'self'" in csp
    assert resp.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_unknown_api_path_is_a_problem_not_the_spa(spa):
    resp = make_client(spa_dir=spa).get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert make_client(spa_dir=spa).get("/api/nope").status_code == 404


# -- compression -----------------------------------------------------------------------


def test_large_json_is_gzipped_for_clients_that_ask(spa):
    resp = make_client(spa_dir=spa).get("/api/v1/openapi.json", headers={"Accept-Encoding": "gzip"})
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert resp.json()["openapi"].startswith("3.")


def test_no_gzip_without_accept_encoding():
    client = make_client()
    resp = client.get("/api/v1/openapi.json", headers={"Accept-Encoding": "identity"})
    assert "Content-Encoding" not in resp.headers or resp.headers["Content-Encoding"] == "identity"
    gzip_free = resp.content
    assert not gzip_free.startswith(b"\x1f\x8b")
    assert gzip.compress(b"") != gzip_free


# -- SPA ------------------------------------------------------------------------------------


def test_spa_routes_fall_back_to_index(spa):
    client = make_client(spa_dir=spa)
    for path in ("/", "/devices", "/settings/bluetooth"):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "<!doctype html>" in resp.text


def test_hashed_assets_are_cached_forever(spa):
    resp = make_client(spa_dir=spa).get("/assets/app-1a2b.js")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_files_outside_the_spa_are_not_served(spa, tmp_path):
    (tmp_path / "secret.txt").write_text("nope")
    resp = make_client(spa_dir=spa).get("/../secret.txt")
    assert "nope" not in resp.text
    resp = make_client(spa_dir=spa).get("/%2e%2e/secret.txt")
    assert "nope" not in resp.text


def test_missing_build_shows_a_pointer_to_the_api(monkeypatch):
    import sendspin_bridge.api.spa as spa_module

    monkeypatch.setattr(spa_module, "find_spa_dir", lambda: None)
    client = make_client()
    spa_module.mount_spa(client.app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "api/v1/docs" in resp.text


# -- session lifetime -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "hours"),
    [(24, 24), ("12", 12), (None, 24), ("abc", 24), (0, 1), (-5, 1), (1, 1), (168, 168), (500, 168)],
)
def test_session_timeout_hours_are_coerced_and_clamped(value, hours):
    from sendspin_bridge.api.app import _session_max_age

    assert _session_max_age({"SESSION_TIMEOUT_HOURS": value}) == hours * 3600
