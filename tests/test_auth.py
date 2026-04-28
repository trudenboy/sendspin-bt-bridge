"""Unit tests for brute-force protection and URL validation in routes/auth.py."""

import time
from unittest.mock import patch

import pytest
from flask import Flask

from routes.auth import (
    _LOCKOUT_DURATION_SECS,
    _LOCKOUT_WINDOW_SECS,
    _check_rate_limit,
    _clear_failures,
    _detect_auth_methods,
    _failed,
    _get_rate_limit_client_id,
    _ma_validate_credentials,
    _record_failure,
    _safe_next_url,
)


@pytest.fixture(autouse=True)
def _reset_failed():
    _failed.clear()
    yield
    _failed.clear()


# ── Brute-force protection ───────────────────────────────────────────────


def test_no_failures_not_locked():
    assert _check_rate_limit("10.0.0.1") is False


def test_under_limit_not_locked():
    for _ in range(4):
        _record_failure("10.0.0.2")
    assert _check_rate_limit("10.0.0.2") is False


def test_at_limit_locks():
    for _ in range(5):
        _record_failure("10.0.0.3")
    assert _check_rate_limit("10.0.0.3") is True


def test_clear_failures_unlocks():
    for _ in range(5):
        _record_failure("10.0.0.4")
    assert _check_rate_limit("10.0.0.4") is True
    _clear_failures("10.0.0.4")
    assert _check_rate_limit("10.0.0.4") is False


def test_lockout_expires(monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(5):
        _record_failure("10.0.0.5")
    assert _check_rate_limit("10.0.0.5") is True

    # Advance past lockout duration
    fake_time[0] = 1000.0 + _LOCKOUT_DURATION_SECS + 1
    assert _check_rate_limit("10.0.0.5") is False


def test_window_reset(monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(4):
        _record_failure("10.0.0.6")

    # Jump past the window — next failure starts a fresh count
    fake_time[0] = 1000.0 + _LOCKOUT_WINDOW_SECS + 1
    _record_failure("10.0.0.6")

    # Only 1 failure in the new window → not locked
    assert _check_rate_limit("10.0.0.6") is False


def test_bruteforce_disabled_never_locks():
    with patch("routes.auth.load_config", return_value={"BRUTE_FORCE_PROTECTION": False}):
        for _ in range(10):
            _record_failure("10.0.0.7")
        assert _check_rate_limit("10.0.0.7") is False


def test_custom_max_attempts_applied():
    cfg = {
        "BRUTE_FORCE_PROTECTION": True,
        "BRUTE_FORCE_MAX_ATTEMPTS": 3,
        "BRUTE_FORCE_WINDOW_MINUTES": 1,
        "BRUTE_FORCE_LOCKOUT_MINUTES": 5,
    }
    with patch("routes.auth.load_config", return_value=cfg):
        _record_failure("10.0.0.8")
        _record_failure("10.0.0.8")
        assert _check_rate_limit("10.0.0.8") is False
        _record_failure("10.0.0.8")
        assert _check_rate_limit("10.0.0.8") is True


def test_rate_limit_client_id_uses_forwarded_for_from_trusted_proxy():
    with (
        patch("routes.auth.load_config", return_value={"TRUSTED_PROXIES": ["10.0.0.10"]}),
        _app.test_request_context(
            "/login",
            method="POST",
            data={"username": "alice"},
            environ_base={"REMOTE_ADDR": "10.0.0.10"},
            headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.10"},
        ),
    ):
        assert _get_rate_limit_client_id() == "198.51.100.7"


def test_rate_limit_client_id_ignores_forwarded_for_from_untrusted_proxy():
    with (
        patch("routes.auth.load_config", return_value={"TRUSTED_PROXIES": []}),
        _app.test_request_context(
            "/login",
            method="POST",
            data={"username": "alice"},
            environ_base={"REMOTE_ADDR": "10.0.0.10"},
            headers={"X-Forwarded-For": "198.51.100.7"},
        ),
    ):
        assert _get_rate_limit_client_id() == "10.0.0.10"


def test_rate_limit_client_id_falls_back_to_username_for_trusted_proxy_without_client_ip():
    with (
        patch("routes.auth.load_config", return_value={"TRUSTED_PROXIES": ["10.0.0.10"]}),
        _app.test_request_context(
            "/login",
            method="POST",
            data={"username": "Alice"},
            environ_base={"REMOTE_ADDR": "10.0.0.10"},
        ),
    ):
        assert _get_rate_limit_client_id() == "proxy-login:alice"


# ── _safe_next_url ───────────────────────────────────────────────────────

_app = Flask(__name__)
_app.secret_key = "test"


def test_safe_local_path():
    with _app.test_request_context("/?next=/settings"):
        assert _safe_next_url() == "/settings"


def test_rejects_absolute_url():
    with _app.test_request_context("/?next=http://evil.com"):
        assert _safe_next_url() == "/"


def test_rejects_protocol_relative():
    with _app.test_request_context("/?next=//evil.com"):
        assert _safe_next_url() == "/"


def test_rejects_no_leading_slash():
    with _app.test_request_context("/?next=evil.com"):
        assert _safe_next_url() == "/"


def test_default_is_root():
    with _app.test_request_context("/"):
        assert _safe_next_url() == "/"


# ── _detect_auth_methods ─────────────────────────────────────────────────


def test_detect_no_methods():
    """No config keys set → password always present."""
    with patch("routes.auth.load_config", return_value={}), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert methods == ["password"]


def test_detect_ma_method():
    """MA_API_URL + MA_API_TOKEN present → 'ma' in methods."""
    cfg = {"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "tok123"}
    with patch("routes.auth.load_config", return_value=cfg), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "ma" in methods


def test_detect_ha_method():
    """SUPERVISOR_TOKEN env → 'ha' in methods."""
    with (
        patch("routes.auth.load_config", return_value={}),
        patch.dict("os.environ", {"SUPERVISOR_TOKEN": "abc"}, clear=False),
    ):
        methods = _detect_auth_methods()
    assert "ha" in methods


def test_detect_password_always_present():
    """'password' is always in methods, even without hash."""
    with patch("routes.auth.load_config", return_value={}), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "password" in methods


def test_detect_multiple_methods():
    """In standalone mode all three methods can be available."""
    cfg = {
        "MA_API_URL": "http://ma:8095",
        "MA_API_TOKEN": "tok",
    }
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch.dict("os.environ", {}, clear=True),
    ):
        methods = _detect_auth_methods()
    assert methods == ["ma", "password"]


def test_detect_addon_mode_only_ha():
    """In addon mode only HA auth is offered."""
    cfg = {
        "MA_API_URL": "http://ma:8095",
        "MA_API_TOKEN": "tok",
    }
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch.dict("os.environ", {"SUPERVISOR_TOKEN": "x"}, clear=False),
    ):
        methods = _detect_auth_methods()
    assert methods == ["ha"]


def test_detect_ha_via_ma():
    """MA_AUTH_PROVIDER == 'ha' → 'ha_via_ma' instead of 'ma'."""
    cfg = {"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "tok", "MA_AUTH_PROVIDER": "ha"}
    with patch("routes.auth.load_config", return_value=cfg), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "ha_via_ma" in methods
    assert "ma" not in methods


def test_detect_ma_requires_both_url_and_token():
    """MA_API_URL without token → no 'ma' method."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with patch("routes.auth.load_config", return_value=cfg), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "ma" not in methods


# ── _ma_validate_credentials ─────────────────────────────────────────────


def test_ma_validate_no_url():
    """No MA_API_URL → fails with descriptive message."""
    with patch("routes.auth.load_config", return_value={}):
        ok, msg = _ma_validate_credentials("user", "pass")
    assert not ok
    assert "not connected" in msg.lower()


def test_ma_validate_success():
    """Successful MA login → True."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch("routes.ma_auth._ma_http_login", return_value="token123"),
    ):
        ok, msg = _ma_validate_credentials("user", "pass")
    assert ok
    assert msg == ""


def test_ma_validate_bad_credentials():
    """RuntimeError from _ma_http_login → invalid credentials."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch("routes.ma_auth._ma_http_login", side_effect=RuntimeError("Invalid username or password")),
    ):
        ok, msg = _ma_validate_credentials("user", "wrong")
    assert not ok
    assert "invalid" in msg.lower()


def test_ma_validate_unreachable():
    """ConnectionError → server unreachable message."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch("routes.ma_auth._ma_http_login", side_effect=ConnectionError("refused")),
    ):
        ok, msg = _ma_validate_credentials("user", "pass")
    assert not ok
    assert "unreachable" in msg.lower()


# ── CSRF protection ─────────────────────────────────────────────────────


@pytest.fixture()
def csrf_client(monkeypatch):
    """Flask test client with auth blueprint and proper template setup."""
    import os

    # CSRF guard short-circuits when global auth is off (Docker / no-auth
    # mode); these tests exercise the auth-on enforcement path, so simulate it.
    import web_interface as _web
    from routes.auth import auth_bp

    monkeypatch.setattr(_web, "_auth_enabled", True)

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, "templates"),
        static_folder=os.path.join(project_root, "static"),
    )
    app.secret_key = "test-csrf"
    app.register_blueprint(auth_bp)

    @app.context_processor
    def inject_version():
        return {"VERSION": "test"}

    @app.route("/static/v<version>/<path:filename>")
    def vstatic(version, filename):
        from flask import send_from_directory

        return send_from_directory(app.static_folder, filename)

    return app.test_client()


def test_csrf_post_without_token(csrf_client):
    """POST to /login without csrf_token → 403."""
    # GET to establish session and generate token
    csrf_client.get("/login")
    # POST without csrf_token
    resp = csrf_client.post("/login", data={"method": "password", "password": "test"})
    assert resp.status_code == 403
    assert b"Invalid session" in resp.data


def test_csrf_post_with_wrong_token(csrf_client):
    """POST to /login with wrong csrf_token → 403."""
    csrf_client.get("/login")
    resp = csrf_client.post(
        "/login",
        data={"method": "password", "password": "test", "csrf_token": "wrong-token"},
    )
    assert resp.status_code == 403
    assert b"Invalid session" in resp.data


def test_csrf_post_with_correct_token(csrf_client):
    """POST to /login with correct csrf_token → normal flow (not 403)."""
    # GET to establish session
    csrf_client.get("/login")
    # Extract csrf_token from session
    with csrf_client.session_transaction() as sess:
        token = sess.get("csrf_token")
    assert token is not None
    # POST with correct token — should proceed to password check (not 403)
    resp = csrf_client.post(
        "/login",
        data={"method": "password", "password": "test", "csrf_token": token},
    )
    # Should get 200 (login page with error) not 403 (CSRF rejection)
    assert resp.status_code == 200
    assert b"Invalid session" not in resp.data


def test_csrf_token_in_get_response(csrf_client):
    """GET /login includes csrf_token hidden input in the form."""
    resp = csrf_client.get("/login")
    assert resp.status_code == 200
    assert b'name="csrf_token"' in resp.data


def test_ha_direct_mfa_step_preserves_csrf_and_completes_login(csrf_client):
    """HA direct MFA flow should render a usable CSRF token for the TOTP step."""
    flow_id = "123e4567-e89b-12d3-a456-426614174000"
    with (
        patch.dict("os.environ", {"SUPERVISOR_TOKEN": "abc"}, clear=False),
        patch("routes.auth._ha_flow_start", return_value={"flow_id": flow_id}),
        patch(
            "routes.auth._ha_flow_step",
            side_effect=[
                {
                    "type": "form",
                    "step_id": "mfa",
                    "description_placeholders": {
                        "mfa_module_id": "totp",
                        "mfa_module_name": "Authenticator app",
                    },
                },
                {"type": "create_entry"},
            ],
        ),
    ):
        csrf_client.get("/login")
        with csrf_client.session_transaction() as sess:
            login_token = sess.get("csrf_token")
        assert login_token is not None

        resp = csrf_client.post(
            "/login",
            data={
                "method": "ha",
                "step": "credentials",
                "username": "user@example.com",
                "password": "secret",
                "csrf_token": login_token,
            },
        )
        assert resp.status_code == 200

        with csrf_client.session_transaction() as sess:
            mfa_token = sess.get("csrf_token")
            assert sess.get("_ha_login_user") == "user@example.com"

        assert mfa_token is not None
        assert f'value="{mfa_token}"'.encode() in resp.data
        assert b'name="flow_id" value="123e4567-e89b-12d3-a456-426614174000"' in resp.data

        resp = csrf_client.post(
            "/login?next=/",
            data={
                "method": "ha",
                "step": "mfa",
                "flow_id": flow_id,
                "mfa_module_id": "totp",
                "code": "123456",
                "csrf_token": mfa_token,
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"

        with csrf_client.session_transaction() as sess:
            assert sess.get("authenticated") is True
            assert sess.get("ha_user") == "user@example.com"
