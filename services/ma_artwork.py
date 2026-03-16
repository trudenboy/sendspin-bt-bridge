"""Shared helpers for safe Music Assistant artwork proxy URLs."""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse as _up
from functools import lru_cache

import state
from config import ensure_secret_key, load_config


@lru_cache(maxsize=1)
def _fallback_artwork_proxy_secret() -> str:
    """Return a process-stable fallback secret when MA token is unavailable."""
    return ensure_secret_key(load_config())


def _artwork_proxy_secret() -> str:
    """Return the secret used to sign artwork URLs."""
    _ma_url, ma_token = state.get_ma_api_credentials()
    if ma_token:
        return ma_token
    return _fallback_artwork_proxy_secret()


def sign_artwork_url(raw_url: str) -> str:
    """Return a stable signature for a raw artwork URL/path."""
    trimmed = raw_url.strip()
    secret = _artwork_proxy_secret().encode()
    return hmac.new(secret, trimmed.encode(), hashlib.sha256).hexdigest()


def has_valid_artwork_signature(raw_url: str, signature: str) -> bool:
    """Check whether an artwork signature matches the raw URL/path."""
    if not raw_url or not signature:
        return False
    expected = sign_artwork_url(raw_url)
    return hmac.compare_digest(expected, signature)


def build_artwork_proxy_url(image_url: str) -> str:
    """Wrap a raw artwork URL/path in a signed same-origin proxy route."""
    if not image_url or not isinstance(image_url, str):
        return ""
    trimmed = image_url.strip()
    if not trimmed:
        return ""
    quoted_url = _up.quote(trimmed, safe="")
    signature = sign_artwork_url(trimmed)
    return f"/api/ma/artwork?url={quoted_url}&sig={signature}"
