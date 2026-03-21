"""Tests for MA Ingress JSONRPC silent auth helpers."""

import io
import json
import sys
from email.message import Message
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory so the web app can start."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def client():
    """Return a Flask test client with the api blueprint registered."""
    from flask import Flask

    _stashed = {}
    for mod_name in ["routes.api", "routes.api_ma", "routes.auth", "routes.views", "routes"]:
        cached = sys.modules.get(mod_name)
        if cached is not None and getattr(cached, "__file__", None) is None:
            _stashed[mod_name] = sys.modules.pop(mod_name)

    from routes.api import api_bp
    from routes.api_ma import ma_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)
    app.register_blueprint(ma_bp)

    yield app.test_client()

    for mod_name, mod in _stashed.items():
        sys.modules.setdefault(mod_name, mod)


# ---------------------------------------------------------------------------
# _get_ha_user_via_ws
# ---------------------------------------------------------------------------


class TestGetHaUserViaWs:
    @patch("websockets.sync.client.connect")
    def test_success_returns_user_info(self, mock_connect):
        ws = MagicMock()
        ws.recv = MagicMock(
            side_effect=[
                json.dumps({"type": "auth_required"}),
                json.dumps({"type": "auth_ok"}),
                json.dumps(
                    {
                        "id": 1,
                        "result": {
                            "id": "abc123",
                            "name": "admin",
                            "is_admin": True,
                        },
                    }
                ),
            ]
        )
        ws.__enter__ = MagicMock(return_value=ws)
        ws.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = ws

        from routes.api_ma import _get_ha_user_via_ws

        result = _get_ha_user_via_ws("test_token")
        assert result is not None
        assert result["id"] == "abc123"
        assert result["name"] == "admin"
        assert result["is_admin"] is True

    @patch("websockets.sync.client.connect")
    def test_auth_failure_returns_none(self, mock_connect):
        ws = MagicMock()
        ws.recv = MagicMock(
            side_effect=[
                json.dumps({"type": "auth_required"}),
                json.dumps({"type": "auth_invalid", "message": "bad token"}),
            ]
        )
        ws.__enter__ = MagicMock(return_value=ws)
        ws.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = ws

        from routes.api_ma import _get_ha_user_via_ws

        assert _get_ha_user_via_ws("bad_token") is None

    @patch("websockets.sync.client.connect", side_effect=ConnectionError("no HA"))
    def test_connection_error_returns_none(self, _mock_connect):
        from routes.api_ma import _get_ha_user_via_ws

        assert _get_ha_user_via_ws("any_token") is None


class TestGetHaSupervisorAddonInfoViaWs:
    @patch("websockets.sync.client.connect")
    def test_success_returns_addon_info(self, mock_connect):
        ws = MagicMock()
        ws.recv = MagicMock(
            side_effect=[
                json.dumps({"type": "auth_required"}),
                json.dumps({"type": "auth_ok"}),
                json.dumps(
                    {
                        "id": 1,
                        "type": "result",
                        "success": True,
                        "result": {
                            "slug": "d5369777_music_assistant_beta",
                            "state": "started",
                            "ingress_url": "/api/hassio_ingress/ma-token/",
                        },
                    }
                ),
            ]
        )
        ws.__enter__ = MagicMock(return_value=ws)
        ws.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = ws

        from routes.api_ma import _get_ha_supervisor_addon_info_via_ws

        result = _get_ha_supervisor_addon_info_via_ws(
            "ha-token",
            "d5369777_music_assistant_beta",
            ha_url="http://ha.local:8123",
        )

        assert result is not None
        assert result["slug"] == "d5369777_music_assistant_beta"
        sent = json.loads(ws.send.call_args_list[1][0][0])
        assert sent["type"] == "supervisor/api"
        assert sent["endpoint"] == "/addons/d5369777_music_assistant_beta/info"

    @patch("websockets.sync.client.connect", side_effect=ConnectionError("no HA"))
    def test_connection_error_returns_none(self, _mock_connect):
        from routes.api_ma import _get_ha_supervisor_addon_info_via_ws

        assert _get_ha_supervisor_addon_info_via_ws("ha-token", "slug", ha_url="http://ha.local:8123") is None


class TestCreateHaIngressSessionViaWs:
    @patch("websockets.sync.client.connect")
    def test_success_returns_session(self, mock_connect):
        ws = MagicMock()
        ws.recv = MagicMock(
            side_effect=[
                json.dumps({"type": "auth_required"}),
                json.dumps({"type": "auth_ok"}),
                json.dumps(
                    {
                        "id": 1,
                        "type": "result",
                        "success": True,
                        "result": {"session": "ingress-session-token"},
                    }
                ),
            ]
        )
        ws.__enter__ = MagicMock(return_value=ws)
        ws.__exit__ = MagicMock(return_value=False)
        mock_connect.return_value = ws

        from routes.api_ma import _create_ha_ingress_session_via_ws

        result = _create_ha_ingress_session_via_ws("ha-token", ha_url="http://ha.local:8123")
        assert result == "ingress-session-token"
        sent = json.loads(ws.send.call_args_list[1][0][0])
        assert sent["type"] == "supervisor/api"
        assert sent["endpoint"] == "/ingress/session"

    @patch("websockets.sync.client.connect", side_effect=ConnectionError("no HA"))
    def test_connection_error_returns_none(self, _mock_connect):
        from routes.api_ma import _create_ha_ingress_session_via_ws

        assert _create_ha_ingress_session_via_ws("ha-token", ha_url="http://ha.local:8123") is None


# ---------------------------------------------------------------------------
# _create_ma_token_via_ingress
# ---------------------------------------------------------------------------


class TestCreateMaTokenViaIngress:
    @patch("routes.api_ma._find_ma_ingress_url", return_value="http://localhost:8094")
    @patch("routes.api_ma.socket.gethostname", return_value="bridge-host")
    @patch("urllib.request.urlopen")
    def test_success_returns_token(self, mock_urlopen, _mock_hostname, _mock_find):
        resp_body = json.dumps({"result": "long_lived_token_123"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from routes.api_ma import _create_ma_token_via_ingress

        result = _create_ma_token_via_ingress("user123", "admin", "Admin User")
        assert result == "long_lived_token_123"

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-remote-user-id") == "user123"
        assert req.get_header("X-remote-user-name") == "admin"
        assert json.loads(req.data.decode())["args"]["name"] == "Sendspin BT Bridge (bridge-host)"

    @patch("routes.api_ma._find_ma_ingress_url", return_value="http://localhost:8094")
    @patch("urllib.request.urlopen")
    def test_error_response_returns_none(self, mock_urlopen, _mock_find):
        resp_body = json.dumps({"error": {"code": 403, "message": "Insufficient permissions"}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from routes.api_ma import _create_ma_token_via_ingress

        assert _create_ma_token_via_ingress("user123", "user") is None

    @patch("routes.api_ma._find_ma_ingress_url", return_value="http://localhost:8094")
    @patch("urllib.request.urlopen", side_effect=ConnectionError("refused"))
    def test_connection_error_returns_none(self, _mock, _mock_find):
        from routes.api_ma import _create_ma_token_via_ingress

        assert _create_ma_token_via_ingress("user123", "user") is None


class TestCreateMaTokenViaHaProxy:
    @patch("routes.api_ma._get_ha_supervisor_addon_info_via_ws", return_value=None)
    @patch("routes.api_ma._create_ha_ingress_session_via_ws", return_value="ingress-session-token")
    @patch("urllib.request.urlopen")
    def test_success_returns_token_for_nested_addon_payload(self, mock_urlopen, _mock_session, _mock_ws_lookup):
        addon_resp = MagicMock()
        addon_resp.read.return_value = json.dumps(
            {
                "data": {
                    "state": "started",
                    "ingress_url": "/api/hassio_ingress/ma-token",
                }
            }
        ).encode()
        addon_resp.__enter__ = MagicMock(return_value=addon_resp)
        addon_resp.__exit__ = MagicMock(return_value=False)

        token_resp = MagicMock()
        token_resp.read.return_value = json.dumps({"result": "ma_long_lived_token"}).encode()
        token_resp.__enter__ = MagicMock(return_value=token_resp)
        token_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [addon_resp, token_resp]

        from routes.api_ma import _create_ma_token_via_ha_proxy

        result = _create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-access-token")
        assert result == "ma_long_lived_token"

        info_req = mock_urlopen.call_args_list[0][0][0]
        assert info_req.full_url == "http://ha.local:8123/api/hassio/addons/d5369777_music_assistant/info"
        assert info_req.get_header("Authorization") == "Bearer ha-access-token"

        token_req = mock_urlopen.call_args_list[1][0][0]
        assert token_req.full_url == "http://ha.local:8123/api/hassio_ingress/ma-token/api"
        assert token_req.get_header("Cookie") == "ingress_session=ingress-session-token"

    @patch(
        "routes.api_ma._get_ha_supervisor_addon_info_via_ws",
        return_value={
            "slug": "d5369777_music_assistant_beta",
            "state": "started",
            "ingress_url": "/api/hassio_ingress/ma-token/",
        },
    )
    @patch("routes.api_ma._create_ha_ingress_session_via_ws", return_value="ingress-session-token")
    @patch("urllib.request.urlopen")
    def test_success_returns_token_for_ws_supervisor_payload(self, mock_urlopen, _mock_session, _mock_ws_lookup):
        token_resp = MagicMock()
        token_resp.read.return_value = json.dumps({"result": "ma_long_lived_token"}).encode()
        token_resp.__enter__ = MagicMock(return_value=token_resp)
        token_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.return_value = token_resp

        from routes.api_ma import _create_ma_token_via_ha_proxy

        result = _create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-access-token")
        assert result == "ma_long_lived_token"

        token_req = mock_urlopen.call_args_list[0][0][0]
        assert token_req.full_url == "http://ha.local:8123/api/hassio_ingress/ma-token/api"
        assert token_req.get_header("Cookie") == "ingress_session=ingress-session-token"

    @patch("routes.api_ma._get_ha_supervisor_addon_info_via_ws", return_value=None)
    @patch("routes.api_ma._create_ha_ingress_session_via_ws", return_value="ingress-session-token")
    @patch("urllib.request.urlopen")
    def test_returns_none_when_ingress_url_missing(self, mock_urlopen, _mock_session, _mock_ws_lookup):
        addon_resp = MagicMock()
        addon_resp.read.return_value = json.dumps({"data": {"state": "stopped"}}).encode()
        addon_resp.__enter__ = MagicMock(return_value=addon_resp)
        addon_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = addon_resp

        from routes.api_ma import _create_ma_token_via_ha_proxy

        assert _create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-access-token") is None

    @patch(
        "routes.api_ma._get_ha_supervisor_addon_info_via_ws",
        return_value={
            "slug": "d5369777_music_assistant_beta",
            "state": "started",
            "ingress_url": "/api/hassio_ingress/ma-token/",
        },
    )
    @patch("routes.api_ma._create_ha_ingress_session_via_ws", return_value=None)
    def test_returns_none_when_ingress_session_unavailable(self, _mock_session, _mock_ws_lookup):
        from routes.api_ma import _create_ma_token_via_ha_proxy

        assert _create_ma_token_via_ha_proxy("http://ha.local:8123", "ha-access-token") is None


# ---------------------------------------------------------------------------
# /api/ma/ha-silent-auth endpoint
# ---------------------------------------------------------------------------


def test_missing_params_returns_400(client):
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok"},
    )
    assert resp.status_code == 400


@patch("routes.api_ma.get_ma_api_credentials", return_value=("http://localhost:8095", "existing_token"))
@patch("routes.api_ma._validate_ma_token", return_value=True)
def test_idempotent_reuse(_mock_validate, _mock_get_ma_api_credentials, client):
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok", "ma_url": "http://localhost:8095"},
    )
    data = resp.get_json()
    assert data["success"] is True
    assert "Already connected" in data["message"]


@patch("routes.api_ma.socket.gethostname", return_value="current-host")
@patch("routes.api_ma.get_ma_api_credentials", return_value=("http://localhost:8095", "existing_token"))
@patch("routes.api_ma._save_ma_token_and_rediscover")
@patch("routes.api_ma._validate_ma_token", return_value=True)
@patch("routes.api_ma._create_ma_token_via_ingress", return_value="new_ma_token")
@patch(
    "routes.api_ma._get_ha_user_via_ws",
    return_value={"id": "u1", "name": "admin", "is_admin": True},
)
@patch(
    "routes.api_ma.load_config",
    return_value={
        "MA_API_URL": "http://localhost:8095",
        "MA_API_TOKEN": "existing_token",
        "MA_TOKEN_INSTANCE_HOSTNAME": "other-host",
    },
)
def test_silent_auth_does_not_reuse_foreign_instance_token(
    _mock_load,
    _mock_ws,
    _mock_ingress,
    _mock_validate,
    _mock_save,
    _mock_get_ma_api_credentials,
    _mock_hostname,
    client,
):
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok", "ma_url": "http://localhost:8095"},
    )
    data = resp.get_json()
    assert data["success"] is True
    assert "Already connected" not in data["message"]
    _mock_ingress.assert_called_once()
    _mock_save.assert_called_once_with("http://localhost:8095", "new_ma_token", "admin", auth_provider="ha")


@patch("routes.api_ma.get_ma_api_credentials", return_value=("", ""))
@patch("routes.api_ma._get_ha_user_via_ws", return_value=None)
def test_ha_ws_failure_returns_401(_mock_ws, _mock_get_ma_api_credentials, client):
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "bad", "ma_url": "http://localhost:8095"},
    )
    assert resp.status_code == 401


@patch("routes.api_ma.get_ma_api_credentials", return_value=("", ""))
@patch("routes.api_ma._save_ma_token_and_rediscover")
@patch("routes.api_ma._validate_ma_token", return_value=True)
@patch("routes.api_ma._create_ma_token_via_ingress", return_value="new_ma_token")
@patch(
    "routes.api_ma._get_ha_user_via_ws",
    return_value={"id": "u1", "name": "admin", "is_admin": True},
)
def test_full_flow_success(_ws, _ingress, _validate, _save, _mock_get_ma_api_credentials, client):
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok", "ma_url": "http://localhost:8095"},
    )
    data = resp.get_json()
    assert data["success"] is True
    assert data["username"] == "admin"
    _save.assert_called_once()


@patch("routes.api_ma.get_ma_api_credentials", return_value=("", ""))
@patch("routes.api_ma._create_ma_token_via_ingress", return_value=None)
@patch(
    "routes.api_ma._get_ha_user_via_ws",
    return_value={"id": "u1", "name": "user", "is_admin": False},
)
def test_ingress_failure_returns_502(_ws, _ingress, _mock_get_ma_api_credentials, client):
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok", "ma_url": "http://localhost:8095"},
    )
    assert resp.status_code == 502


class TestGetMaOauthParams:
    @patch("routes.api_ma._ur.urlopen")
    @patch("routes.api_ma._ur.build_opener")
    def test_parses_redirect_based_auth_authorize(self, mock_build_opener, mock_urlopen):
        auth_url = (
            "http://ha.local:8123/auth/authorize?"
            "client_id=http%3A%2F%2Fma.local%3A8095&"
            "redirect_uri=http%3A%2F%2Fma.local%3A8095%2Fauth%2Fcallback%3Fprovider_id%3Dhomeassistant&"
            "state=test-state"
        )
        opener = MagicMock()
        opener.open.side_effect = _http_error_with_location("http://ma.local:8095/auth/authorize", auth_url)
        mock_build_opener.return_value = opener

        from routes.api_ma import _get_ma_oauth_params

        result = _get_ma_oauth_params("http://ma.local:8095")
        assert result == (
            "http://ha.local:8123",
            "http://ma.local:8095",
            "http://ma.local:8095/auth/callback?provider_id=homeassistant",
            "test-state",
        )
        mock_urlopen.assert_not_called()

    @patch("routes.api_ma._ur.urlopen")
    @patch("routes.api_ma._ur.build_opener")
    def test_parses_jsonrpc_authorization_url_result_dict(self, mock_build_opener, mock_urlopen):
        opener = MagicMock()
        opener.open.side_effect = _http_error_with_location("http://ma.local:8095/auth/authorize", "")
        mock_build_opener.return_value = opener

        auth_url = (
            "http://ha.local:8123/auth/authorize?"
            "client_id=http%3A%2F%2Fma.local%3A8095&"
            "redirect_uri=http%3A%2F%2Fma.local%3A8095%2Fauth%2Fcallback%3Fprovider_id%3Dhomeassistant&"
            "state=jsonrpc-state"
        )
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"result": {"authorization_url": auth_url}}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from routes.api_ma import _get_ma_oauth_params

        result = _get_ma_oauth_params("http://ma.local:8095")
        assert result == (
            "http://ha.local:8123",
            "http://ma.local:8095",
            "http://ma.local:8095/auth/callback?provider_id=homeassistant",
            "jsonrpc-state",
        )
        req = mock_urlopen.call_args[0][0]
        assert json.loads(req.data.decode())["args"]["return_url"] == "http://ma.local:8095"


@patch(
    "routes.api_ma._get_ma_oauth_bootstrap",
    return_value=(
        None,
        "Music Assistant Home Assistant auth is unavailable: Provider does not support OAuth or is not configured. "
        "If Home Assistant login is not configured in Music Assistant, switch to Music Assistant authentication.",
    ),
)
@patch("routes.api_ma._ma_reports_homeassistant_addon", return_value=False)
def test_ha_login_returns_specific_ma_oauth_error(_mock_addon, _mock_oauth, client):
    resp = client.post(
        "/api/ma/ha-login",
        json={
            "step": "init",
            "ma_url": "http://localhost:8095",
            "username": "user",
            "password": "pass",
        },
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "Provider does not support OAuth or is not configured" in data["error"]
    assert "switch to Music Assistant authentication" in data["error"]


@patch("routes.api_ma._save_ma_token_and_rediscover")
@patch("routes.api_ma._validate_ma_token", return_value=True)
@patch("routes.api_ma._create_ma_token_via_ha_proxy", return_value="ma-token")
@patch("routes.api_ma._get_ha_user_via_ws", return_value={"id": "u1", "name": "admin", "is_admin": True})
@patch("routes.api_ma._exchange_ha_auth_code", return_value={"access_token": "ha-access"})
@patch(
    "routes.api_ma._ha_login_flow_step",
    return_value={"type": "create_entry", "result": "ha-auth-code"},
)
@patch("routes.api_ma._ha_login_flow_start", return_value={"flow_id": "flow123"})
@patch("routes.api_ma._derive_ha_urls_from_ma", return_value=["http://ha.local:8123"])
@patch("routes.api_ma._ma_reports_homeassistant_addon", return_value=True)
@patch(
    "routes.api_ma._get_ma_oauth_bootstrap",
    return_value=(None, "Music Assistant Home Assistant auth is unavailable"),
)
def test_ha_login_falls_back_to_direct_ha_flow_for_addon_ma(
    _mock_oauth,
    _mock_addon,
    _mock_urls,
    _mock_flow_start,
    _mock_flow_step,
    _mock_exchange,
    _mock_ws_user,
    _mock_create,
    _mock_validate,
    _mock_save,
    client,
):
    resp = client.post(
        "/api/ma/ha-login",
        json={
            "step": "init",
            "ma_url": "http://localhost:8095",
            "username": "user",
            "password": "pass",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["step"] == "done"
    assert data["username"] == "admin"
    assert "Connected to Music Assistant via Home Assistant." in data["message"]
    _mock_save.assert_called_once_with("http://localhost:8095", "ma-token", "admin", auth_provider="ha")


def _http_error_with_location(url: str, location: str):
    headers = Message()
    if location:
        headers["Location"] = location
    return HTTPError(url, 302, "Found", headers, io.BytesIO(b""))
