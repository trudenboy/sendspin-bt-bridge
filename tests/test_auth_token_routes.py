"""End-to-end tests for the bearer-token endpoints in ``routes/auth.py``."""

from __future__ import annotations

import json

import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Test client with the auth blueprint registered.

    The token endpoints require an authenticated session; we set that
    flag manually rather than going through the full login flow.
    """
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"AUTH_TOKENS": []}))
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config, "load_config", lambda: json.loads(cfg_file.read_text()))

    import services.auth_tokens as M

    monkeypatch.setattr(M, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(M, "load_config", lambda: json.loads(cfg_file.read_text()))

    from routes.auth import auth_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(auth_bp)
    yield app.test_client()


def _login(client) -> None:
    """Set the session as authenticated without going through password flow."""
    with client.session_transaction() as session:
        session["authenticated"] = True
        # routes/auth._validate_csrf_token reads ``session["csrf_token"]``
        # against the form-posted ``csrf_token`` field.
        session["csrf_token"] = "test-csrf"


# ---------------------------------------------------------------------------
# /api/auth/tokens GET — list
# ---------------------------------------------------------------------------


def test_list_tokens_requires_session(client):
    resp = client.get("/api/auth/tokens")
    assert resp.status_code == 401


def test_list_tokens_empty(client):
    _login(client)
    resp = client.get("/api/auth/tokens")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["tokens"] == []


# ---------------------------------------------------------------------------
# POST /api/auth/tokens — issue
# ---------------------------------------------------------------------------


def test_create_token_requires_session(client):
    resp = client.post("/api/auth/tokens", json={"label": "x"})
    assert resp.status_code == 401


def test_create_token_returns_plaintext_once(client):
    _login(client)
    resp = client.post(
        "/api/auth/tokens",
        json={"label": "ha-cc"},
        headers={"X-CSRF-Token": "test-csrf"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["token"]
    plaintext = body["token"]
    record = body["record"]
    assert record["label"] == "ha-cc"
    assert "token_hash" not in record  # never exposed

    # Listing afterward must NOT include the plaintext.
    list_resp = client.get("/api/auth/tokens")
    listed = list_resp.get_json()
    assert len(listed["tokens"]) == 1
    assert plaintext not in json.dumps(listed)
    assert listed["tokens"][0]["id"] == record["id"]


# ---------------------------------------------------------------------------
# DELETE /api/auth/tokens/<id> — revoke
# ---------------------------------------------------------------------------


def test_delete_token_unknown_returns_404(client):
    _login(client)
    resp = client.delete("/api/auth/tokens/nonexistent")
    assert resp.status_code == 404


def test_delete_token_revokes_existing(client):
    _login(client)
    create_resp = client.post(
        "/api/auth/tokens",
        json={"label": "x"},
        headers={"X-CSRF-Token": "test-csrf"},
    )
    body = create_resp.get_json()
    record_id = body["record"]["id"]

    resp = client.delete(f"/api/auth/tokens/{record_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["id"] == record_id

    list_resp = client.get("/api/auth/tokens")
    assert list_resp.get_json()["tokens"] == []


# ---------------------------------------------------------------------------
# POST /api/auth/ha-pair — Supervisor pairing
# ---------------------------------------------------------------------------


def test_ha_pair_rejects_lan_caller(client):
    """A direct call from a LAN IP must NOT yield a token, even with the
    ingress header set — the IP check fails."""
    resp = client.post(
        "/api/auth/ha-pair",
        headers={"X-Ingress-Path": "/api/auth/ha-pair"},
        environ_base={"REMOTE_ADDR": "192.168.1.50"},
    )
    assert resp.status_code == 403


def test_ha_pair_rejects_supervisor_ip_without_ingress_header(client):
    """The Supervisor proxy injects ``X-Ingress-Path`` — without it we
    must refuse even from the trusted IP."""
    resp = client.post(
        "/api/auth/ha-pair",
        environ_base={"REMOTE_ADDR": "172.30.32.2"},
    )
    assert resp.status_code == 403


def test_ha_pair_mints_token_when_supervisor_indicators_present(client):
    resp = client.post(
        "/api/auth/ha-pair",
        environ_base={"REMOTE_ADDR": "172.30.32.2"},
        headers={"X-Ingress-Path": "/api/auth/ha-pair"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["token"]
    assert body["record"]["label"] == "ha-custom-component"
