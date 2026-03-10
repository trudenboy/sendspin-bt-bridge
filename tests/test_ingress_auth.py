"""Tests for MA Ingress JSONRPC silent auth helpers."""

import json
import sys
from unittest.mock import MagicMock, patch

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
    for mod_name in ["routes.api", "routes.auth", "routes.views", "routes"]:
        cached = sys.modules.get(mod_name)
        if cached is not None and getattr(cached, "__file__", None) is None:
            _stashed[mod_name] = sys.modules.pop(mod_name)

    from routes.api import api_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)

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

        from routes.api import _get_ha_user_via_ws

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

        from routes.api import _get_ha_user_via_ws

        assert _get_ha_user_via_ws("bad_token") is None

    @patch("websockets.sync.client.connect", side_effect=ConnectionError("no HA"))
    def test_connection_error_returns_none(self, _mock_connect):
        from routes.api import _get_ha_user_via_ws

        assert _get_ha_user_via_ws("any_token") is None


# ---------------------------------------------------------------------------
# _create_ma_token_via_ingress
# ---------------------------------------------------------------------------


class TestCreateMaTokenViaIngress:
    @patch("urllib.request.urlopen")
    def test_success_returns_token(self, mock_urlopen):
        resp_body = json.dumps({"result": "long_lived_token_123"}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from routes.api import _create_ma_token_via_ingress

        result = _create_ma_token_via_ingress("user123", "admin", "Admin User")
        assert result == "long_lived_token_123"

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("X-remote-user-id") == "user123"
        assert req.get_header("X-remote-user-name") == "admin"

    @patch("urllib.request.urlopen")
    def test_error_response_returns_none(self, mock_urlopen):
        resp_body = json.dumps({"error": {"code": 403, "message": "Insufficient permissions"}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from routes.api import _create_ma_token_via_ingress

        assert _create_ma_token_via_ingress("user123", "user") is None

    @patch("urllib.request.urlopen", side_effect=ConnectionError("refused"))
    def test_connection_error_returns_none(self, _mock):
        from routes.api import _create_ma_token_via_ingress

        assert _create_ma_token_via_ingress("user123", "user") is None


# ---------------------------------------------------------------------------
# /api/ma/ha-silent-auth endpoint
# ---------------------------------------------------------------------------


def test_missing_params_returns_400(client):
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok"},
    )
    assert resp.status_code == 400


@patch("routes.api.state")
@patch("routes.api._validate_ma_token", return_value=True)
def test_idempotent_reuse(_mock_validate, mock_state, client):
    mock_state.get_ma_api_credentials.return_value = (
        "http://localhost:8095",
        "existing_token",
    )
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok", "ma_url": "http://localhost:8095"},
    )
    data = resp.get_json()
    assert data["success"] is True
    assert "Already connected" in data["message"]


@patch("routes.api.state")
@patch("routes.api._get_ha_user_via_ws", return_value=None)
def test_ha_ws_failure_returns_401(_mock_ws, mock_state, client):
    mock_state.get_ma_api_credentials.return_value = ("", "")
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "bad", "ma_url": "http://localhost:8095"},
    )
    assert resp.status_code == 401


@patch("routes.api.state")
@patch("routes.api._save_ma_token_and_rediscover")
@patch("routes.api._validate_ma_token", return_value=True)
@patch("routes.api._create_ma_token_via_ingress", return_value="new_ma_token")
@patch(
    "routes.api._get_ha_user_via_ws",
    return_value={"id": "u1", "name": "admin", "is_admin": True},
)
def test_full_flow_success(_ws, _ingress, _validate, _save, mock_state, client):
    mock_state.get_ma_api_credentials.return_value = ("", "")
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok", "ma_url": "http://localhost:8095"},
    )
    data = resp.get_json()
    assert data["success"] is True
    assert data["username"] == "admin"
    _save.assert_called_once()


@patch("routes.api.state")
@patch("routes.api._create_ma_token_via_ingress", return_value=None)
@patch(
    "routes.api._get_ha_user_via_ws",
    return_value={"id": "u1", "name": "user", "is_admin": False},
)
def test_ingress_failure_returns_502(_ws, _ingress, mock_state, client):
    mock_state.get_ma_api_credentials.return_value = ("", "")
    resp = client.post(
        "/api/ma/ha-silent-auth",
        json={"ha_token": "tok", "ma_url": "http://localhost:8095"},
    )
    assert resp.status_code == 502
