"""Tests for MFA session variable lifecycle.

Verifies that ``_ha_login_user`` (set during MFA flows) is properly cleaned up
after authentication succeeds, on GET /login, and is never leaked between
different users.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask import Flask

from sendspin_bridge.web.routes.auth import auth_bp

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory so the app can start cleanly."""
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def app():
    """Minimal Flask app with the auth blueprint and supporting routes."""
    application = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    application.secret_key = "test-secret"
    application.config["TESTING"] = True
    application.register_blueprint(auth_bp)

    # login.html uses url_for('vstatic', ...) and VERSION context variable
    @application.route("/static/v<version>/<path:filename>")
    def vstatic(version, filename):
        from flask import send_from_directory

        return send_from_directory(application.static_folder, filename)

    @application.context_processor
    def _inject_version():
        return {"VERSION": "0.0.0-test"}

    return application


@pytest.fixture()
def client(app):
    return app.test_client()


def _post_with_csrf(client, url, data):
    """POST helper that injects a valid CSRF token from the session."""
    with client.session_transaction() as sess:
        if "csrf_token" not in sess:
            import secrets

            sess["csrf_token"] = secrets.token_hex(32)
        data["csrf_token"] = sess["csrf_token"]
    return client.post(url, data=data)


# ---------------------------------------------------------------------------
# 1. Session cleared after successful password auth
# ---------------------------------------------------------------------------


def test_ha_login_user_cleared_after_password_auth(client, app):
    """_ha_login_user must not remain in session after a successful local
    password login (simulates an abandoned MFA flow followed by password
    login)."""
    stored_hash = "pbkdf2:sha256:600000$salt$e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "stale_mfa_user"

    with (
        patch("sendspin_bridge.web.routes.auth.load_config", return_value={"AUTH_PASSWORD_HASH": stored_hash}),
        patch("sendspin_bridge.web.routes.auth.check_password", return_value=True),
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["password"]),
    ):
        _post_with_csrf(client, "/login", {"method": "password", "password": "correct"})

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess
        assert sess.get("authenticated") is True


# ---------------------------------------------------------------------------
# 2. Session cleared on GET /login
# ---------------------------------------------------------------------------


def test_ha_login_user_cleared_on_get_login(client, app):
    """GET /login must clear stale _ha_login_user from an abandoned MFA flow."""
    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "abandoned_user"

    with patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["password"]):
        resp = client.get("/login")
        assert resp.status_code == 200

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess


# ---------------------------------------------------------------------------
# 3. Session variable not leaked between requests (different user)
# ---------------------------------------------------------------------------


def test_ha_login_user_not_leaked_between_users(client, app):
    """If _ha_login_user was set for user A, then user B authenticates via
    password, the old value must be gone — ha_user should reflect user B (or
    be absent), never user A."""
    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "user_a"

    with (
        patch("sendspin_bridge.web.routes.auth.load_config", return_value={"AUTH_PASSWORD_HASH": "hash"}),
        patch("sendspin_bridge.web.routes.auth.check_password", return_value=True),
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["password"]),
    ):
        _post_with_csrf(client, "/login", {"method": "password", "password": "correct"})

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess
        assert sess.get("authenticated") is True
        # Local password auth does not set ha_user
        assert sess.get("ha_user") is None


def test_ha_login_user_not_leaked_across_ma_login(client, app):
    """If _ha_login_user was set for user A, then user B authenticates via MA,
    ha_user must reflect user B."""
    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "user_a"

    with (
        patch("sendspin_bridge.web.routes.auth._ma_validate_credentials", return_value=(True, "")),
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["ma", "password"]),
    ):
        _post_with_csrf(
            client,
            "/login",
            {
                "method": "ma",
                "username": "user_b",
                "password": "pass",
            },
        )

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess
        assert sess.get("authenticated") is True
        assert sess.get("ha_user") == "user_b"


# ---------------------------------------------------------------------------
# 4. MFA session keys cleaned after HA direct auth (create_entry)
# ---------------------------------------------------------------------------


def test_ha_login_user_cleared_after_ha_direct_auth(client, app):
    """After a successful HA login_flow (create_entry), _ha_login_user is
    popped into ha_user and no stale MFA keys remain."""
    flow_id = "a" * 32
    flow_start = {"flow_id": flow_id}
    create_entry = {"type": "create_entry"}

    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "ha_admin"

    with (
        patch("sendspin_bridge.web.routes.auth._is_ha_addon", return_value=True),
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["ha"]),
        patch("sendspin_bridge.web.routes.auth._ha_flow_start", return_value=flow_start),
        patch("sendspin_bridge.web.routes.auth._ha_flow_step", return_value=create_entry),
    ):
        _post_with_csrf(
            client,
            "/login",
            {
                "method": "ha",
                "username": "ha_admin",
                "password": "secret",
            },
        )

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess
        assert sess.get("authenticated") is True
        assert sess.get("ha_user") == "ha_admin"


def test_ha_login_user_cleared_after_mfa_step(client, app):
    """After a successful MFA step (create_entry in step 2), _ha_login_user is
    moved to ha_user."""
    flow_id = "b" * 32

    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "mfa_user"

    with (
        patch("sendspin_bridge.web.routes.auth._is_ha_addon", return_value=True),
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["ha"]),
        patch("sendspin_bridge.web.routes.auth._ha_flow_step", return_value={"type": "create_entry"}),
    ):
        _post_with_csrf(
            client,
            "/login",
            {
                "method": "ha",
                "step": "mfa",
                "flow_id": flow_id,
                "code": "123456",
            },
        )

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess
        assert sess.get("authenticated") is True
        assert sess.get("ha_user") == "mfa_user"


def test_ha_login_user_set_during_mfa_prompt(client, app):
    """When HA returns an MFA prompt (step_id == 'mfa'), _ha_login_user is set
    in the session to carry the username across the MFA form submission."""
    flow_id = "c" * 32
    flow_start = {"flow_id": flow_id}
    mfa_prompt = {
        "type": "form",
        "step_id": "mfa",
        "description_placeholders": {"mfa_module_id": "totp"},
    }

    with (
        patch("sendspin_bridge.web.routes.auth._is_ha_addon", return_value=True),
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["ha"]),
        patch("sendspin_bridge.web.routes.auth._ha_flow_start", return_value=flow_start),
        patch("sendspin_bridge.web.routes.auth._ha_flow_step", return_value=mfa_prompt),
    ):
        _post_with_csrf(
            client,
            "/login",
            {
                "method": "ha",
                "username": "mfa_user",
                "password": "secret",
            },
        )

    with client.session_transaction() as sess:
        assert sess.get("_ha_login_user") == "mfa_user"
        assert "authenticated" not in sess


def test_ha_via_ma_login_user_cleared_after_auth(client, app):
    """ha_via_ma flow also clears _ha_login_user after create_entry."""
    flow_id = "d" * 32
    flow_start = {"flow_id": flow_id}
    create_entry = {"type": "create_entry"}

    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "remote_ha_user"

    with (
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["ha_via_ma", "password"]),
        patch("sendspin_bridge.web.routes.auth._get_ha_core_url_from_ma", return_value="http://ha:8123"),
        patch("sendspin_bridge.web.routes.auth._ha_remote_flow_start", return_value=flow_start),
        patch("sendspin_bridge.web.routes.auth._ha_remote_flow_step", return_value=create_entry),
    ):
        _post_with_csrf(
            client,
            "/login",
            {
                "method": "ha_via_ma",
                "username": "remote_ha_user",
                "password": "pass",
            },
        )

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess
        assert sess.get("authenticated") is True
        assert sess.get("ha_user") == "remote_ha_user"


# ---------------------------------------------------------------------------
# 5. Supervisor fallback also clears _ha_login_user
# ---------------------------------------------------------------------------


def test_supervisor_fallback_clears_ha_login_user(client, app, monkeypatch):
    """When HA Core is unreachable and the Supervisor fallback is explicitly
    enabled via ``ALLOW_SUPERVISOR_FALLBACK=1``, ``_ha_login_user`` is still
    cleaned up after a successful fallback sign-in.
    """
    monkeypatch.setenv("ALLOW_SUPERVISOR_FALLBACK", "1")
    with client.session_transaction() as sess:
        sess["_ha_login_user"] = "stale"

    with (
        patch("sendspin_bridge.web.routes.auth._is_ha_addon", return_value=True),
        patch("sendspin_bridge.web.routes.auth._detect_auth_methods", return_value=["ha"]),
        patch("sendspin_bridge.web.routes.auth._ha_flow_start", return_value=None),
        patch("sendspin_bridge.web.routes.auth._supervisor_auth", return_value=True),
    ):
        _post_with_csrf(
            client,
            "/login",
            {
                "method": "ha",
                "username": "admin",
                "password": "pass",
            },
        )

    with client.session_transaction() as sess:
        assert "_ha_login_user" not in sess
        assert sess.get("authenticated") is True
