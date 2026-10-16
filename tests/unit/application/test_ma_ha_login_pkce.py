"""Sign in to Music Assistant with Home Assistant when MA uses PKCE.

Music Assistant (dev, 2026-10) puts ``code_challenge`` +
``code_challenge_method=S256`` in the Home Assistant authorize URL and sends
the matching ``code_verifier`` when it exchanges the code. The bridge drives
Home Assistant's ``/auth/login_flow`` itself, so it must hand the challenge to
Home Assistant when it starts the flow — Home Assistant binds the code to the
challenge of the flow that issued it, and refuses MA's exchange otherwise.

Every MA/HA endpoint here is a fake answering in-process.
"""

from __future__ import annotations

import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.music_assistant import auth as ma_auth

MA_URL = "http://ma.local:8095"
HA_URL = "http://ha.local:8123"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def _authorize_url(*, challenge: str | None = CHALLENGE, method: str | None = "S256") -> str:
    url = (
        f"{HA_URL}/auth/authorize?client_id=http%3A%2F%2Fma.local%3A8095"
        "&redirect_uri=http%3A%2F%2Fma.local%3A8095%2Fauth%2Fcallback%3Fprovider_id%3Dhomeassistant"
        "&state=ma-state"
    )
    if challenge:
        url += f"&code_challenge={challenge}"
    if method:
        url += f"&code_challenge_method={method}"
    return url


class _Resp:
    def __init__(self, payload: object):
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNetwork:
    """MA's authorize endpoint plus HA's login_flow, recording what HA receives."""

    def __init__(self, *, authorize_url: str = "", jsonrpc_url: str = "", flow_results: list[dict] | None = None):
        self.authorize_url = authorize_url
        self.jsonrpc_url = jsonrpc_url
        self.flow_results = list(flow_results or [])
        self.flow_starts: list[dict] = []
        self.flow_steps: list[tuple[str, dict]] = []

    def build_opener(self, *_handlers):
        network = self

        class _Opener:
            def open(self, req, timeout=None):
                headers = Message()
                if network.authorize_url:
                    headers["Location"] = network.authorize_url
                raise HTTPError(req.full_url, 302, "Found", headers, io.BytesIO(b""))

        return _Opener()

    def urlopen(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if url == f"{MA_URL}/api":
            if not self.jsonrpc_url:
                return _Resp({"error": "unknown command"})
            return _Resp({"result": {"authorization_url": self.jsonrpc_url}})
        if url == f"{HA_URL}/auth/login_flow":
            self.flow_starts.append(json.loads(req.data))
            return _Resp({"type": "form", "flow_id": "flow-1", "step_id": "init"})
        if url.startswith(f"{HA_URL}/auth/login_flow/"):
            self.flow_steps.append((url.rsplit("/", 1)[-1], json.loads(req.data)))
            return _Resp(self.flow_results.pop(0))
        raise AssertionError(f"unexpected request to {url}")


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def network(monkeypatch):
    net = FakeNetwork()
    monkeypatch.setattr(ma_auth, "is_safe_external_url", lambda _u: True)
    monkeypatch.setattr(ma_auth, "safe_build_opener", net.build_opener)
    monkeypatch.setattr(ma_auth, "safe_urlopen", net.urlopen)
    return net


@pytest.fixture()
def ma_exchange(monkeypatch):
    """MA's callback + token endpoints: record which HA code MA was given."""
    seen: dict[str, object] = {}

    def _callback(ma_url, code, state):
        seen["callback"] = (ma_url, code, state)
        return "ma-session"

    monkeypatch.setattr(ma_auth, "_ma_callback_exchange", _callback)
    monkeypatch.setattr(ma_auth, "_exchange_for_long_lived_token", lambda _u, _t: "ma-long-lived")
    monkeypatch.setattr(ma_auth, "_save_ma_token_and_rediscover", lambda *a, **kw: seen.setdefault("saved", (a, kw)))
    return seen


class TestOAuthParamsCarryTheChallenge:
    def test_redirect_authorize_url_keeps_code_challenge(self, network):
        network.authorize_url = _authorize_url()

        info, err = ma_auth._get_ma_oauth_bootstrap(MA_URL)

        assert err == ""
        assert info is not None
        assert info.code_challenge == CHALLENGE
        assert info.code_challenge_method == "S256"

    def test_jsonrpc_fallback_keeps_code_challenge(self, network):
        network.jsonrpc_url = _authorize_url()

        info, _err = ma_auth._get_ma_oauth_bootstrap(MA_URL)

        assert info is not None
        assert info.state == "ma-state"
        assert info.code_challenge == CHALLENGE
        assert info.code_challenge_method == "S256"

    def test_older_ma_without_challenge_has_none(self, network):
        network.authorize_url = _authorize_url(challenge=None, method=None)

        info, _err = ma_auth._get_ma_oauth_bootstrap(MA_URL)

        assert info is not None
        assert info.code_challenge == ""
        assert info.code_challenge_method == ""


class TestLoginFlowStartBody:
    def test_password_step_starts_flow_with_challenge(self, network, ma_exchange):
        network.authorize_url = _authorize_url()
        network.flow_results = [{"type": "create_entry", "result": "ha-code"}]

        result = ma_auth.ha_login_start(MA_URL, "user", "pw")

        assert result["step"] == "done"
        assert network.flow_starts == [
            {
                "client_id": "http://ma.local:8095",
                "handler": ["homeassistant", None],
                "redirect_uri": "http://ma.local:8095/auth/callback?provider_id=homeassistant",
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            }
        ]
        assert ma_exchange["callback"] == (MA_URL, "ha-code", "ma-state")

    def test_challenge_without_method_is_sent_as_s256(self, network, ma_exchange):
        # MA's challenge is always S256; HA refuses a challenge without a method.
        network.authorize_url = _authorize_url(method=None)
        network.flow_results = [{"type": "create_entry", "result": "ha-code"}]

        ma_auth.ha_login_start(MA_URL, "user", "pw")

        assert network.flow_starts[0]["code_challenge"] == CHALLENGE
        assert network.flow_starts[0]["code_challenge_method"] == "S256"

    def test_older_ma_body_is_unchanged(self, network, ma_exchange):
        network.authorize_url = _authorize_url(challenge=None, method=None)
        network.flow_results = [{"type": "create_entry", "result": "ha-code"}]

        ma_auth.ha_login_start(MA_URL, "user", "pw")

        assert network.flow_starts == [
            {
                "client_id": "http://ma.local:8095",
                "handler": ["homeassistant", None],
                "redirect_uri": "http://ma.local:8095/auth/callback?provider_id=homeassistant",
            }
        ]

    def test_jsonrpc_fallback_starts_flow_with_challenge(self, network, ma_exchange):
        network.jsonrpc_url = _authorize_url()
        network.flow_results = [{"type": "create_entry", "result": "ha-code"}]

        ma_auth.ha_login_start(MA_URL, "user", "pw")

        assert network.flow_starts[0]["code_challenge"] == CHALLENGE
        assert network.flow_starts[0]["code_challenge_method"] == "S256"


class TestTwoFactorKeepsTheChallenge:
    def test_mfa_context_keeps_challenge_and_code_reaches_ma(self, network, ma_exchange):
        network.authorize_url = _authorize_url()
        network.flow_results = [
            {
                "type": "form",
                "step_id": "mfa",
                "flow_id": "flow-1",
                "description_placeholders": {"mfa_module_id": "totp", "mfa_module_name": "Authenticator app"},
            },
            {"type": "create_entry", "result": "ha-code-after-2fa"},
        ]

        first = ma_auth.ha_login_start(MA_URL, "user", "pw")
        assert first["step"] == "mfa"
        context = first["_context"]
        assert context["code_challenge"] == CHALLENGE
        assert context["code_challenge_method"] == "S256"
        # Only one flow is started: the one bound to MA's challenge.
        assert len(network.flow_starts) == 1
        assert network.flow_starts[0]["code_challenge"] == CHALLENGE

        done = ma_auth.ha_login_mfa(MA_URL, "123 456", context)

        assert done["step"] == "done"
        # The 2FA step continues the same challenged flow and does not start another.
        assert len(network.flow_starts) == 1
        assert network.flow_steps[-1] == ("flow-1", {"client_id": "http://ma.local:8095", "code": "123456"})
        assert ma_exchange["callback"] == (MA_URL, "ha-code-after-2fa", "ma-state")

    def test_mfa_context_without_challenge_still_signs_in(self, network, ma_exchange):
        network.authorize_url = _authorize_url(challenge=None, method=None)
        network.flow_results = [
            {"type": "form", "step_id": "mfa", "flow_id": "flow-1"},
            {"type": "create_entry", "result": "ha-code"},
        ]

        first = ma_auth.ha_login_start(MA_URL, "user", "pw")
        assert "code_challenge" not in network.flow_starts[0]
        assert not first["_context"].get("code_challenge")

        done = ma_auth.ha_login_mfa(MA_URL, "123456", first["_context"])

        assert done["step"] == "done"


def test_bad_session_mfa_context_still_expires():
    with pytest.raises(UseCaseError) as exc:
        ma_auth.ha_login_mfa(MA_URL, "123456", {"code_challenge": CHALLENGE})
    assert exc.value.code == "flow_expired"


def test_the_home_assistant_code_never_reaches_the_log(monkeypatch, caplog):
    """The code redeems a Music Assistant session: a refused or failed exchange logs the host only."""
    import logging

    from sendspin_bridge.application.music_assistant import auth

    monkeypatch.setattr(auth, "is_safe_external_url", lambda url: False)
    with caplog.at_level(logging.DEBUG):
        assert auth._ma_callback_exchange("http://ma.example:8095", "SECRET-HA-CODE", "STATE-123") is None
    assert "SECRET-HA-CODE" not in caplog.text
    assert "STATE-123" not in caplog.text
    assert "ma.example" in caplog.text
