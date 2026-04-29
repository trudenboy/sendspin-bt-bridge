"""Tests for MA OAuth token exchange helpers in routes/api_ma.py."""

import json
import re
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 1. Regex fix — ensure #/ is not captured in token
# ---------------------------------------------------------------------------
# Test the regex pattern directly (no need to import routes/api.py)
_TOKEN_RE = re.compile(r'[?&]code=([^&#"\'<>\s]+)')


class TestCallbackTokenRegex:
    """Verify the regex used in _ma_callback_exchange correctly extracts JWT."""

    def test_simple_token(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.dGVzdA"
        html = f'<a href="/?code={token}">Redirect</a>'
        m = _TOKEN_RE.search(html)
        assert m and m.group(1) == token

    def test_token_with_hash_fragment(self):
        """Vue Router hash #/ must not leak into token."""
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.dGVzdA"
        html = f'<a href="/?code={token}#/">Redirect</a>'
        m = _TOKEN_RE.search(html)
        assert m and m.group(1) == token

    def test_token_with_hash_and_path(self):
        token = "eyJhbGciOiJIUzI1NiJ9.payload.sig"
        html = f'<a href="http://ma:8095/?code={token}#/login">Redirect</a>'
        m = _TOKEN_RE.search(html)
        assert m and m.group(1) == token

    def test_token_with_ampersand_after(self):
        token = "eyJhbGciOiJIUzI1NiJ9.payload.sig"
        html = f'<a href="/?code={token}&other=1">Redirect</a>'
        m = _TOKEN_RE.search(html)
        assert m and m.group(1) == token

    def test_token_with_underscore_and_hyphen(self):
        """JWT base64url uses - and _ characters."""
        token = "eyJhbGciOi-IUzI1NiJ9.pay_load.s-i_g"
        html = f'<a href="/?code={token}">Redirect</a>'
        m = _TOKEN_RE.search(html)
        assert m and m.group(1) == token

    def test_no_code_param(self):
        html = '<a href="/?state=abc">Redirect</a>'
        assert _TOKEN_RE.search(html) is None

    def test_code_in_query_with_hash(self):
        """Token in complex URL with both query params and hash."""
        token = "eyJ0b2tlbiI6InRlc3QifQ.sig.nonce123"
        html = f'href="http://localhost:8095/?code={token}&#x2F;"'
        m = _TOKEN_RE.search(html)
        assert m and m.group(1) == token


# ---------------------------------------------------------------------------
# 2. _validate_ma_token — tests with mocked websocket
# ---------------------------------------------------------------------------
class TestValidateMaToken:
    def _make_ws_mock(self, auth_result):
        ws = MagicMock()
        ws.__enter__ = MagicMock(return_value=ws)
        ws.__exit__ = MagicMock(return_value=False)
        ws.recv.side_effect = [
            '{"server_id": "test", "server_version": "2.7.10"}',
            json.dumps({"result": auth_result, "message_id": 1}),
        ]
        return ws

    def test_valid_token_returns_true(self):
        ws = self._make_ws_mock({"authenticated": True})
        mock_mod = types.ModuleType("websockets.sync.client")
        mock_mod.connect = MagicMock(return_value=ws)

        with patch.dict(sys.modules, {"websockets.sync.client": mock_mod}):
            from sendspin_bridge.web.routes.ma_auth import _validate_ma_token

            assert _validate_ma_token("http://ma:8095", "good_token") is True

        sent = json.loads(ws.send.call_args[0][0])
        assert sent["command"] == "auth"
        assert sent["args"]["token"] == "good_token"

    def test_invalid_token_returns_false(self):
        ws = self._make_ws_mock({"authenticated": False})
        mock_mod = types.ModuleType("websockets.sync.client")
        mock_mod.connect = MagicMock(return_value=ws)

        with patch.dict(sys.modules, {"websockets.sync.client": mock_mod}):
            from sendspin_bridge.web.routes.ma_auth import _validate_ma_token

            assert _validate_ma_token("http://ma:8095", "bad_token") is False

    def test_connection_error_returns_false(self):
        mock_mod = types.ModuleType("websockets.sync.client")
        mock_mod.connect = MagicMock(side_effect=ConnectionRefusedError("refused"))

        with patch.dict(sys.modules, {"websockets.sync.client": mock_mod}):
            from sendspin_bridge.web.routes.ma_auth import _validate_ma_token

            assert _validate_ma_token("http://ma:8095", "any") is False


# ---------------------------------------------------------------------------
# 3. _exchange_for_long_lived_token — tests with mocked websocket
# ---------------------------------------------------------------------------
class TestExchangeForLongLivedToken:
    def _make_ws_mock(self, auth_ok, token_result=None):
        ws = MagicMock()
        ws.__enter__ = MagicMock(return_value=ws)
        ws.__exit__ = MagicMock(return_value=False)
        responses = [
            '{"server_id": "test"}',
            json.dumps({"result": {"authenticated": auth_ok}, "message_id": 1}),
        ]
        if auth_ok and token_result is not None:
            responses.append(json.dumps({"result": token_result, "message_id": 2}))
        ws.recv.side_effect = responses
        return ws

    @patch("sendspin_bridge.web.routes.ma_auth.socket.gethostname", return_value="bridge-host")
    def test_success_returns_long_lived(self, _mock_hostname):
        long_jwt = "eyJ_long_lived_token"
        ws = self._make_ws_mock(True, long_jwt)
        mock_mod = types.ModuleType("websockets.sync.client")
        mock_mod.connect = MagicMock(return_value=ws)

        with patch.dict(sys.modules, {"websockets.sync.client": mock_mod}):
            from sendspin_bridge.web.routes.ma_auth import _exchange_for_long_lived_token

            result = _exchange_for_long_lived_token("http://ma:8095", "session_tok")

        assert result == long_jwt
        # Verify auth/token/create was sent
        calls = [json.loads(c[0][0]) for c in ws.send.call_args_list]
        create_call = next(c for c in calls if c["command"] == "auth/token/create")
        assert create_call["args"]["name"] == "Sendspin BT Bridge (bridge-host)"

    def test_auth_failure_returns_session_token(self):
        ws = self._make_ws_mock(False)
        mock_mod = types.ModuleType("websockets.sync.client")
        mock_mod.connect = MagicMock(return_value=ws)

        with patch.dict(sys.modules, {"websockets.sync.client": mock_mod}):
            from sendspin_bridge.web.routes.ma_auth import _exchange_for_long_lived_token

            result = _exchange_for_long_lived_token("http://ma:8095", "session_tok")

        assert result == "session_tok"

    def test_connection_error_returns_session_token(self):
        mock_mod = types.ModuleType("websockets.sync.client")
        mock_mod.connect = MagicMock(side_effect=OSError("connection failed"))

        with patch.dict(sys.modules, {"websockets.sync.client": mock_mod}):
            from sendspin_bridge.web.routes.ma_auth import _exchange_for_long_lived_token

            result = _exchange_for_long_lived_token("http://ma:8095", "session_tok")

        assert result == "session_tok"

    def test_token_create_returns_non_string(self):
        """If auth/token/create returns unexpected type, fall back to session token."""
        ws = self._make_ws_mock(True, {"error": "something"})
        mock_mod = types.ModuleType("websockets.sync.client")
        mock_mod.connect = MagicMock(return_value=ws)

        with patch.dict(sys.modules, {"websockets.sync.client": mock_mod}):
            from sendspin_bridge.web.routes.ma_auth import _exchange_for_long_lived_token

            result = _exchange_for_long_lived_token("http://ma:8095", "session_tok")

        assert result == "session_tok"
