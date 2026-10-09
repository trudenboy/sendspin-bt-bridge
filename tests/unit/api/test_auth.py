"""Who may call the API: sessions, bearer tokens, HA ingress, CSRF, lockout."""

from __future__ import annotations

import json

import pytest

from tests.support.api_client import get_session, make_client, set_session

PASSWORD = "correct horse battery"


@pytest.fixture
def config_file(tmp_config, monkeypatch):
    """Authentication on, with a local password."""
    from sendspin_bridge.config import hash_password

    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    tmp_config.write_text(json.dumps({"AUTH_ENABLED": True, "AUTH_PASSWORD_HASH": hash_password(PASSWORD)}))
    return tmp_config


@pytest.fixture
def client(config_file):
    return make_client(json.loads(config_file.read_text()))


def _sign_in(client) -> str:
    resp = client.post("/api/v1/auth/session", json={"method": "password", "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "signed_in"
    return resp.json()["csrf_token"]


# -- the gate -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/status"),
        ("get", "/api/v1/devices"),
        ("get", "/api/v1/config"),
        ("post", "/api/v1/bridge/restart"),
        ("get", "/api/ha/state"),
        ("post", "/api/ha/command"),
    ],
)
def test_protected_endpoints_answer_401_without_credentials(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/bridge/preflight", "/api/v1/auth/session"])
def test_public_endpoints_need_no_credentials(client, path):
    assert client.get(path).status_code == 200


def test_session_state_before_sign_in(client):
    state = client.get("/api/v1/auth/session").json()
    assert state["authenticated"] is False
    assert state["auth_enabled"] is True
    assert "password" in state["methods"]
    assert state["csrf_token"]


# -- password sign-in and CSRF ----------------------------------------------------


def test_password_sign_in_opens_a_session(client):
    _sign_in(client)
    state = client.get("/api/v1/auth/session").json()
    assert state["authenticated"] is True
    assert state["principal"] == "session"
    assert client.get("/api/v1/devices").status_code == 200


def test_wrong_password_is_401_and_opens_nothing(client):
    resp = client.post("/api/v1/auth/session", json={"method": "password", "password": "nope"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "invalid_credentials"
    assert client.get("/api/v1/devices").status_code == 401


def test_session_writes_need_the_csrf_token(client, monkeypatch):
    import sendspin_bridge.application.bridge_control as bridge_control

    monkeypatch.setattr(bridge_control, "set_log_level", lambda level: {"level": level})
    csrf = _sign_in(client)

    missing = client.put("/api/v1/bridge/log-level", json={"level": "DEBUG"})
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_failed"

    wrong = client.put("/api/v1/bridge/log-level", json={"level": "DEBUG"}, headers={"X-CSRF-Token": "x" * 64})
    assert wrong.status_code == 403

    ok = client.put("/api/v1/bridge/log-level", json={"level": "DEBUG"}, headers={"X-CSRF-Token": csrf})
    assert ok.status_code != 403


def test_sign_out_clears_the_session_but_keeps_the_lockout_bucket(client):
    csrf = _sign_in(client)
    set_session(client, {**get_session(client), "_lockout_client_id": "bucket-1"})

    assert client.delete("/api/v1/auth/session").status_code == 403  # no CSRF
    resp = client.delete("/api/v1/auth/session", headers={"X-CSRF-Token": csrf})

    assert resp.status_code == 204
    assert get_session(client) == {"_lockout_client_id": "bucket-1"}
    assert client.get("/api/v1/devices").status_code == 401


def test_repeated_failures_lock_the_client_out(client, monkeypatch):
    import sendspin_bridge.application.auth as auth_uc
    from sendspin_bridge.security.login_rate_limiter import LockoutSettings, LoginRateLimiter

    monkeypatch.setattr(
        auth_uc,
        "rate_limiter",
        LoginRateLimiter(settings_provider=lambda: LockoutSettings(max_attempts=2)),
    )
    for _ in range(2):
        assert client.post("/api/v1/auth/session", json={"password": "nope"}).status_code == 401

    locked = client.post("/api/v1/auth/session", json={"password": PASSWORD})
    assert locked.status_code == 429
    assert locked.json()["code"] == "locked_out"


# -- MFA bookkeeping ----------------------------------------------------------------


def test_ha_mfa_prompt_remembers_the_user_until_the_flow_ends(client, monkeypatch):
    import sendspin_bridge.application.auth as auth_uc

    monkeypatch.setattr(
        auth_uc,
        "sign_in",
        lambda method, **kw: (
            auth_uc.LoginOutcome("mfa_required", user="alice", flow_id="f1")
            if not kw.get("flow_id")
            else auth_uc.LoginOutcome("success", user=kw.get("pending_user"))
        ),
    )
    first = client.post("/api/v1/auth/session", json={"method": "ha_via_ma", "username": "alice", "password": "pw"})
    assert first.json()["status"] == "mfa_required"
    assert get_session(client)["_ha_login_user"] == "alice"

    second = client.post("/api/v1/auth/session", json={"method": "ha_via_ma", "flow_id": "f1", "code": "123456"})
    assert second.json() == {**second.json(), "status": "signed_in", "user": "alice"}
    assert "_ha_login_user" not in get_session(client)


def test_a_pending_mfa_user_is_not_reused_by_a_fresh_sign_in(client, monkeypatch):
    import sendspin_bridge.application.auth as auth_uc

    seen: list[str] = []

    def fake_sign_in(method, **kw):
        seen.append(kw.get("pending_user", ""))
        return auth_uc.LoginOutcome("invalid", message="no", counts_as_failure=False)

    monkeypatch.setattr(auth_uc, "sign_in", fake_sign_in)
    set_session(client, {"_ha_login_user": "alice"})

    client.post("/api/v1/auth/session", json={"method": "ha_via_ma", "username": "mallory", "password": "pw"})

    assert seen == [""]


# -- password change -----------------------------------------------------------------


def test_password_change_needs_the_current_password(client, config_file):
    csrf = _sign_in(client)
    headers = {"X-CSRF-Token": csrf}

    wrong = client.put(
        "/api/v1/auth/password", json={"current_password": "nope", "password": "new password 1"}, headers=headers
    )
    assert wrong.status_code == 403

    ok = client.put(
        "/api/v1/auth/password", json={"current_password": PASSWORD, "password": "new password 1"}, headers=headers
    )
    assert ok.status_code == 204
    from sendspin_bridge.config import check_password

    assert check_password("new password 1", json.loads(config_file.read_text())["AUTH_PASSWORD_HASH"])


# -- bearer tokens -----------------------------------------------------------------------


def test_tokens_need_a_signed_in_person(client):
    assert client.get("/api/v1/auth/tokens").status_code == 401
    assert client.post("/api/v1/auth/tokens", json={"label": "x"}).status_code == 401


def test_issued_token_is_shown_once_and_authenticates(client):
    csrf = _sign_in(client)
    issued = client.post("/api/v1/auth/tokens", json={"label": "script"}, headers={"X-CSRF-Token": csrf})
    assert issued.status_code == 201
    token = issued.json()["token"]
    listed = client.get("/api/v1/auth/tokens").json()["tokens"]
    assert [t["label"] for t in listed] == ["script"]
    assert token not in json.dumps(listed)

    client.cookies.clear()
    resp = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_bearer_writes_need_no_csrf_but_cannot_manage_credentials(client):
    csrf = _sign_in(client)
    token = client.post("/api/v1/auth/tokens", json={"label": "ha"}, headers={"X-CSRF-Token": csrf}).json()["token"]
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post("/api/v1/auth/tokens", json={"label": "more"}, headers=headers).status_code == 403
    assert client.put("/api/v1/auth/password", json={"password": "whatever123"}, headers=headers).status_code == 403
    # An ordinary write goes through without a CSRF header.
    resp = client.post("/api/ha/command/bridge", json={"command": "nope"}, headers=headers)
    assert resp.status_code not in (401, 403)


def test_revoking_unknown_token_is_404_and_revoked_tokens_stop_working(client):
    csrf = _sign_in(client)
    headers = {"X-CSRF-Token": csrf}
    assert client.delete("/api/v1/auth/tokens/nope", headers=headers).status_code == 404

    issued = client.post("/api/v1/auth/tokens", json={"label": "tmp"}, headers=headers).json()
    assert client.delete(f"/api/v1/auth/tokens/{issued['record']['id']}", headers=headers).status_code == 204

    client.cookies.clear()
    resp = client.get("/api/v1/devices", headers={"Authorization": f"Bearer {issued['token']}"})
    assert resp.status_code == 401


# -- authentication off --------------------------------------------------------------------


def test_with_auth_off_everything_is_open_to_same_origin_callers(tmp_config):
    client = make_client()
    assert client.get("/api/v1/devices").status_code == 200
    assert client.get("/api/v1/auth/tokens").status_code == 200
    # Scripts and the HA integration post without any token, as before.
    assert client.post("/api/ha/command/bridge", json={"command": "nope"}).status_code != 403


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null"},
    ],
)
def test_with_auth_off_cross_site_browser_writes_are_refused(tmp_config, headers):
    client = make_client()
    resp = client.post("/api/ha/command/bridge", json={"command": "restart"}, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == "cross_site"


def test_with_auth_off_same_origin_browser_writes_pass(tmp_config):
    client = make_client()
    headers = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    resp = client.post("/api/ha/command/bridge", json={"command": "nope"}, headers=headers)
    assert resp.status_code != 403


# -- Home Assistant ingress ---------------------------------------------------------------


@pytest.fixture
def addon(monkeypatch, tmp_config):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "test-supervisor-token")
    return tmp_config


@pytest.mark.parametrize("peer", ["172.30.32.2", "172.30.32.1", "172.30.33.2", "172.30.32.150"])
def test_addon_ingress_from_the_hassio_network_authenticates(addon, peer):
    client = make_client(peer=peer)
    resp = client.get("/api/v1/auth/session", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.json()["authenticated"] is True
    assert resp.json()["principal"] == "ingress"


@pytest.mark.parametrize("peer", ["192.168.10.5", "10.0.0.1", "172.30.34.1", "172.30.31.255"])
def test_ingress_header_from_outside_the_hassio_network_is_ignored(addon, peer):
    client = make_client(peer=peer)
    resp = client.get("/api/v1/devices", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.status_code == 401


def test_standalone_ingress_header_from_loopback_is_ignored(tmp_config, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    client = make_client({"AUTH_ENABLED": True}, peer="127.0.0.1")
    resp = client.get("/api/v1/devices", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert resp.status_code == 401


# -- HA custom component pairing -------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/auth/ha-pair", "/api/auth/ha-pair"])
@pytest.mark.parametrize("peer", ["172.30.32.2", "172.30.32.1", "172.30.33.42"])
def test_ha_pair_mints_a_token_through_the_supervisor(addon, path, peer):
    client = make_client(peer=peer)
    resp = client.post(path, headers={"X-Ingress-Path": "/api/auth/ha-pair"})
    assert resp.status_code in (200, 201)
    body = resp.json()
    assert body["token"]
    assert body["record"]["label"] == "ha-custom-component"


def test_ha_pair_legacy_path_keeps_its_body(addon):
    resp = make_client(peer="172.30.32.1").post("/api/auth/ha-pair", headers={"X-Ingress-Path": "/x"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.parametrize("peer", ["192.168.10.5", "172.30.34.1"])
def test_ha_pair_refuses_other_networks(addon, peer):
    client = make_client(peer=peer)
    assert client.post("/api/v1/auth/ha-pair", headers={"X-Ingress-Path": "/x"}).status_code == 403
    legacy = client.post("/api/auth/ha-pair", headers={"X-Ingress-Path": "/x"})
    assert legacy.status_code == 403
    assert legacy.json() == {"success": False, "error": "Not allowed from this network"}


def test_ha_pair_needs_the_ingress_header(addon):
    assert make_client(peer="172.30.32.2").post("/api/v1/auth/ha-pair").status_code == 403


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "172.30.32.1"])
def test_ha_pair_refuses_outside_addon_mode(tmp_config, monkeypatch, peer):
    """Standalone has no Supervisor: a forged header from loopback must not mint a token."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    client = make_client(peer=peer)
    assert client.post("/api/v1/auth/ha-pair", headers={"X-Ingress-Path": "/x"}).status_code == 403


def test_an_ingress_visit_does_not_leave_an_authenticated_cookie(addon):
    """Cookies ignore ports: a session marked authenticated through ingress
    would also open the bridge's direct port on the same host."""
    via_ingress = make_client(peer="172.30.32.2")
    via_ingress.get("/api/v1/devices", headers={"X-Ingress-Path": "/api/hassio_ingress/abc"})
    assert get_session(via_ingress).get("authenticated") is not True

    direct = make_client(peer="192.168.10.50")
    direct.cookies = via_ingress.cookies
    assert direct.get("/api/v1/devices").status_code == 401
