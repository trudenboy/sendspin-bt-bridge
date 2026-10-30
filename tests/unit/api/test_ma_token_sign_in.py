"""``POST /music-assistant/session/token`` — connect with a pasted Music Assistant token.

Music Assistant here is a fake WebSocket server answering the ``auth`` command
the way Music Assistant does: a ``result`` with ``authenticated`` for a known
token, an error message for any other.
"""

from __future__ import annotations

import json
import logging

import pytest

import sendspin_bridge.application.music_assistant.auth as ma_auth
import sendspin_bridge.bridge.state as state
from tests.support.api_client import make_client

MA_URL = "http://ma.local:8095"
GOOD = "eyJhbGciOi.good-long-lived-token.sig"
PATH = "/api/v1/music-assistant/session/token"


class FakeMaWebSocket:
    def __init__(self, tokens: dict[str, str]):
        self.tokens = tokens
        self.sent: list[dict] = []
        self._replies: list[str] = [json.dumps({"server_id": "fake", "server_version": "2.8.0"})]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, raw: str) -> None:
        msg = json.loads(raw)
        self.sent.append(msg)
        token = (msg.get("args") or {}).get("token")
        if msg.get("command") == "auth" and token in self.tokens:
            reply = {
                "message_id": msg["message_id"],
                "result": {"authenticated": True, "user": {"username": self.tokens[token]}},
            }
        else:
            reply = {"message_id": msg["message_id"], "error_code": 20, "details": "Invalid or expired token"}
        self._replies.append(json.dumps(reply))

    def recv(self, timeout=None) -> str:
        return self._replies.pop(0)


class FakeMa:
    def __init__(self, monkeypatch, *, reachable: bool = True):
        self.urls: list[str] = []
        self.reachable = reachable
        self.tokens = {GOOD: "renso"}
        monkeypatch.setattr(ma_auth, "_ws_connect", self._connect)

    def _connect(self, url, **_kw):
        self.urls.append(url)
        if not self.reachable:
            raise ConnectionRefusedError("refused")
        return FakeMaWebSocket(self.tokens)


@pytest.fixture(autouse=True)
def _clean(tmp_config, monkeypatch):
    monkeypatch.setattr(ma_auth, "is_safe_external_url", lambda _u: True)
    yield
    state.set_ma_api_credentials("", "")


@pytest.fixture
def reloads(monkeypatch, bridge_loop):
    """The monitor start/reload and group rediscovery every sign-in path triggers."""
    calls: list[tuple] = []
    # Sign-in also starts a monitor the bridge booted without (see
    # ma_monitor.ensure_monitor_started); the seam moved there.
    monkeypatch.setattr(ma_auth, "ensure_monitor_started", lambda loop, url, token: calls.append((url, token)))

    async def _no_rediscover(*_a):
        calls.append(("rediscover",))

    monkeypatch.setattr(ma_auth, "_rediscover_after_login", _no_rediscover)
    return calls


def test_a_valid_token_is_saved_and_the_monitor_reconnects(api_client, monkeypatch, tmp_config, reloads, caplog):
    ma = FakeMa(monkeypatch)
    caplog.set_level(logging.DEBUG)

    resp = api_client.post(PATH, json={"url": MA_URL, "token": f"  {GOOD}\n"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == MA_URL
    assert body["username"] == "renso"
    assert GOOD not in resp.text
    assert ma.urls == ["ws://ma.local:8095/ws"]
    saved = json.loads(tmp_config.read_text())
    assert saved["MA_API_URL"] == MA_URL
    assert saved["MA_API_TOKEN"] == GOOD
    assert saved["MA_USERNAME"] == "renso"
    assert saved["MA_AUTH_PROVIDER"] == "token"
    assert state.get_ma_api_credentials() == (MA_URL, GOOD)
    assert (MA_URL, GOOD) in reloads
    assert GOOD not in caplog.text


def test_an_unknown_token_is_400_and_nothing_is_saved(api_client, monkeypatch, tmp_config, reloads, caplog):
    FakeMa(monkeypatch)
    caplog.set_level(logging.DEBUG)

    resp = api_client.post(PATH, json={"url": MA_URL, "token": "not-a-real-token"})

    # Not 401: the SPA treats a 401 as its own session expiring and signs the user out.
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == "ma_token_invalid"
    assert "not-a-real-token" not in resp.text
    assert "not-a-real-token" not in caplog.text
    assert "MA_API_TOKEN" not in json.loads(tmp_config.read_text())
    assert reloads == []


def test_an_unreachable_server_is_503(api_client, monkeypatch, tmp_config, reloads):
    FakeMa(monkeypatch, reachable=False)

    resp = api_client.post(PATH, json={"url": MA_URL, "token": GOOD})

    assert resp.status_code == 503
    assert resp.json()["code"] == "ma_unreachable"
    assert GOOD not in resp.text
    assert "MA_API_TOKEN" not in json.loads(tmp_config.read_text())


def test_an_empty_token_is_400(api_client, monkeypatch, reloads):
    ma = FakeMa(monkeypatch)

    resp = api_client.post(PATH, json={"url": MA_URL, "token": "   "})

    assert resp.status_code == 400
    assert resp.json()["code"] == "token_required"
    assert ma.urls == []


def test_without_a_url_the_known_server_is_used(api_client, monkeypatch, tmp_config, reloads):
    ma = FakeMa(monkeypatch)
    monkeypatch.setattr(ma_auth, "_resolve_ma_url_for_login", lambda _loop: MA_URL)

    resp = api_client.post(PATH, json={"token": GOOD})

    assert resp.status_code == 200
    assert resp.json()["url"] == MA_URL
    assert ma.urls == ["ws://ma.local:8095/ws"]


def test_a_host_without_scheme_gets_http(api_client, monkeypatch, reloads):
    ma = FakeMa(monkeypatch)

    resp = api_client.post(PATH, json={"url": "ma.local:8095/", "token": GOOD})

    assert resp.status_code == 200
    assert resp.json()["url"] == MA_URL
    assert ma.urls == ["ws://ma.local:8095/ws"]


def test_a_disallowed_url_is_refused_before_any_connection(api_client, monkeypatch, reloads):
    ma = FakeMa(monkeypatch)
    monkeypatch.setattr(ma_auth, "is_safe_external_url", lambda _u: False)

    resp = api_client.post(PATH, json={"url": "http://169.254.169.254", "token": GOOD})

    assert resp.status_code == 400
    assert resp.json()["code"] == "url_not_allowed"
    assert ma.urls == []


def test_cross_site_browser_writes_are_refused(monkeypatch, reloads):
    ma = FakeMa(monkeypatch)

    resp = make_client().post(PATH, json={"url": MA_URL, "token": GOOD}, headers={"Origin": "http://evil.example"})

    assert resp.status_code == 403
    assert ma.urls == []


def test_requires_sign_in_when_auth_is_on(monkeypatch, tmp_config, reloads):
    ma = FakeMa(monkeypatch)

    resp = make_client({"AUTH_ENABLED": True}).post(PATH, json={"url": MA_URL, "token": GOOD})

    assert resp.status_code == 401
    assert ma.urls == []
