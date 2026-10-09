"""Artwork hosted outside Music Assistant, as radio station logos are.

Seen on HAOS: a radio logo on upload.wikimedia.org was requested every one
to three seconds and refused every time — 301 warnings in half an hour.
Three things were wrong: the URL arrived percent-encoded and the proxy
encoded it again (``%D0`` became ``%25D0``, a path that does not exist);
the request went out with urllib's default User-Agent, which Wikimedia
refuses; and nothing remembered the refusal, so every status refresh asked
again.
"""

from __future__ import annotations

import json
import sys
import urllib.error as _ue

import pytest

_MA_URL = "http://192.168.10.20:8095"
LOGO = "https://upload.wikimedia.org/wikipedia/ru/d/d4/%D0%9B%D0%BE%D0%B3%D0%BE%D1%82%D0%B8%D0%BF.png"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def ma_playback(monkeypatch):
    name = "sendspin_bridge.application.music_assistant.playback"
    if name in sys.modules and getattr(sys.modules[name], "__file__", None) is None:
        sys.modules.pop(name)
    import sendspin_bridge.application.music_assistant.playback as mod

    monkeypatch.setattr(mod, "get_ma_api_credentials", lambda: (_MA_URL, "ma-secret-token"))
    mod._reset_artwork_failures()
    return mod


@pytest.fixture()
def client(ma_playback):
    from tests.support.api_client import make_client

    return make_client()


class _FakeResponse:
    headers = {"Content-Type": "image/png", "Content-Length": "3"}

    def read(self, _n):
        return b"png"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fetch(client, url):
    from sendspin_bridge.services.music_assistant.ma_artwork import sign_artwork_url

    return client.get("/api/v1/music-assistant/artwork", params={"url": url, "sig": sign_artwork_url(url)})


def _opener(monkeypatch, ma_playback, respond):
    requests: list = []

    class _Opener:
        def open(self, req, timeout=None):
            requests.append(req)
            return respond(req)

    monkeypatch.setattr(ma_playback, "safe_build_opener", lambda *a, **k: _Opener())
    return requests


def test_an_encoded_url_is_fetched_as_given_with_a_named_user_agent(client, ma_playback, monkeypatch):
    requests = _opener(monkeypatch, ma_playback, lambda _req: _FakeResponse())

    resp = _fetch(client, LOGO)

    assert resp.status_code == 200
    assert requests[0].full_url == LOGO, "the already-encoded path was encoded again"
    agent = requests[0].get_header("User-agent") or ""
    assert agent.startswith("sendspin-bt-bridge/"), f"sent User-Agent {agent!r}"


def test_a_refused_image_is_not_asked_for_again_on_every_refresh(client, ma_playback, monkeypatch):
    def _refuse(req):
        raise _ue.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    requests = _opener(monkeypatch, ma_playback, _refuse)

    first = _fetch(client, LOGO)
    again = [_fetch(client, LOGO) for _ in range(5)]

    assert first.status_code == 403
    assert all(r.status_code == 403 for r in again)
    assert len(requests) == 1, f"the refused image was requested {len(requests)} times"
    assert "max-age" in (first.headers.get("Cache-Control") or ""), "the browser is not told to stop asking"
