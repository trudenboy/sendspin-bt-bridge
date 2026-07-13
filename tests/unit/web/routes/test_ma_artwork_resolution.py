"""Tests for ``_resolve_ma_artwork_url`` origin classification.

A scheme-relative URL (``//host/path``) carries a foreign netloc while
lacking a scheme; it must never be classified as MA-origin, or the artwork
proxy would HMAC-sign a foreign host as if it were Music Assistant.
"""

from __future__ import annotations

import pytest

import sendspin_bridge.web.routes.ma_playback as M


@pytest.fixture
def ma_creds(monkeypatch):
    monkeypatch.setattr(M, "get_ma_api_credentials", lambda: ("http://ma.local:8095", "tok"))


def test_scheme_relative_url_is_not_ma_origin(ma_creds):
    resolved, is_ma = M._resolve_ma_artwork_url("//evil.com/cover.jpg")
    assert "evil.com" in resolved
    assert is_ma is False


def test_relative_path_is_ma_origin(ma_creds):
    resolved, is_ma = M._resolve_ma_artwork_url("/imageproxy/cover.jpg")
    assert resolved == "http://ma.local:8095/imageproxy/cover.jpg"
    assert is_ma is True


def test_absolute_same_origin_is_ma_origin(ma_creds):
    resolved, is_ma = M._resolve_ma_artwork_url("http://ma.local:8095/x.jpg")
    assert resolved == "http://ma.local:8095/x.jpg"
    assert is_ma is True


def test_absolute_foreign_origin_is_not_ma_origin(ma_creds):
    resolved, is_ma = M._resolve_ma_artwork_url("http://evil.com/x.jpg")
    assert resolved == "http://evil.com/x.jpg"
    assert is_ma is False


def test_non_http_scheme_rejected(ma_creds):
    with pytest.raises(ValueError):
        M._resolve_ma_artwork_url("javascript:alert(1)")
