"""The artwork proxy must not hand the MA bearer token to a redirect target.

``urllib``'s default redirect handler copies request headers onto the
redirected request, so a signed MA-origin URL that 302s off-origin used to
ship ``Authorization: Bearer <MA token>`` to whatever host the redirect named
— the HMAC gate only ever validates the *initial* URL.  The fetch also has to
go through the SSRF-safe opener so every hop re-verifies the peer address.
"""

from __future__ import annotations

import json
import sys
import urllib.request as _ur

import pytest
from flask import Flask

_MA_URL = "http://192.168.10.20:8095"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def ma_playback(monkeypatch):
    for name in ("sendspin_bridge.web.routes.ma_playback",):
        if name in sys.modules and getattr(sys.modules[name], "__file__", None) is None:
            sys.modules.pop(name)

    import sendspin_bridge.web.routes.ma_playback as mod

    monkeypatch.setattr(mod, "get_ma_api_credentials", lambda: (_MA_URL, "ma-secret-token"))
    return mod


@pytest.fixture()
def client(ma_playback):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(ma_playback.ma_bp)
    return app.test_client()


class _FakeResponse:
    headers = {"Content-Type": "image/png", "Content-Length": "3"}

    def read(self, _n):
        return b"png"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_artwork_fetch_goes_through_the_ssrf_safe_opener(client, ma_playback, monkeypatch):
    from sendspin_bridge.services.music_assistant.ma_artwork import sign_artwork_url

    opened: list[object] = []

    class _FakeOpener:
        def open(self, req, timeout=None):
            opened.append(req)
            return _FakeResponse()

    built: list[tuple] = []

    def _fake_build_opener(*handlers, **kwargs):
        built.append(handlers)
        return _FakeOpener()

    monkeypatch.setattr(ma_playback, "safe_build_opener", _fake_build_opener, raising=False)

    raw = "/imageproxy/cover.png"
    resp = client.get(f"/api/ma/artwork?url={raw}&sig={sign_artwork_url(raw)}")

    assert resp.status_code == 200
    assert resp.data == b"png"
    assert built, "artwork proxy did not use the SSRF-safe opener"
    assert opened and opened[0].get_header("Authorization") == "Bearer ma-secret-token"


def test_cross_origin_redirect_drops_the_bearer_token(ma_playback):
    handler = ma_playback._ArtworkRedirectHandler(_MA_URL)
    req = _ur.Request(f"{_MA_URL}/imageproxy/cover.png", headers={"Authorization": "Bearer ma-secret-token"})

    redirected = handler.redirect_request(
        req, None, 302, "Found", {"location": "http://cdn.example.com/cover.png"}, "http://cdn.example.com/cover.png"
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def test_same_origin_redirect_keeps_the_bearer_token(ma_playback):
    handler = ma_playback._ArtworkRedirectHandler(_MA_URL)
    req = _ur.Request(f"{_MA_URL}/imageproxy/cover.png", headers={"Authorization": "Bearer ma-secret-token"})

    redirected = handler.redirect_request(
        req, None, 302, "Found", {"location": f"{_MA_URL}/imageproxy/cover-2.png"}, f"{_MA_URL}/imageproxy/cover-2.png"
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") == "Bearer ma-secret-token"
