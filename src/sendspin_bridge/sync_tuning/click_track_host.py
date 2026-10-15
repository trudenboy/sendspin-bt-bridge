"""Hosts the click track for Music Assistant under per-session, unguessable URLs."""

from __future__ import annotations

import hmac
import logging
import secrets
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from sendspin_bridge.config.network import get_local_ip, resolve_web_port
from sendspin_sync.click_track import render_wav

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def track_wav() -> bytes:
    return render_wav()


class ClickTrackTokens:
    """Maps a random 32-byte token to the session that owns it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: dict[str, str] = {}

    def issue(self, session_id: str) -> str:
        token = secrets.token_hex(32)
        with self._lock:
            self._by_session[session_id] = token
        return token

    def session_for(self, token: str) -> str | None:
        with self._lock:
            items = list(self._by_session.items())
        found: str | None = None
        for sid, known in items:  # compare against all, so timing does not reveal a match
            if hmac.compare_digest(known.encode(), token.encode()):
                found = sid
        return found

    def release(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)

    def any_live(self) -> bool:
        with self._lock:
            return bool(self._by_session)


tokens = ClickTrackTokens()


_announced_base_url: str | None = None


def default_base_url() -> str:
    """The bridge as Music Assistant reaches it; logged once per value, with a warning on the loopback fallback."""
    global _announced_base_url
    ip = get_local_ip()
    url = f"http://{ip or '127.0.0.1'}:{resolve_web_port()}"
    if url != _announced_base_url:
        _announced_base_url = url
        if ip:
            logger.info("Sync click track is served to Music Assistant from %s", url)
        else:
            logger.warning(
                "No LAN address found: the sync click track URL falls back to %s, "
                "which Music Assistant on another host cannot reach",
                url,
            )
    return url


class FastApiClickTrackHost:
    def __init__(
        self,
        tokens: ClickTrackTokens = tokens,
        base_url: Callable[[], str] = default_base_url,
    ) -> None:
        self._tokens = tokens
        self._base_url = base_url

    def url_for(self, session_id: str) -> str:
        return f"{self._base_url().rstrip('/')}/api/v1/sync/signal/{self._tokens.issue(session_id)}.wav"

    def release(self, session_id: str) -> None:
        self._tokens.release(session_id)
        if not self._tokens.any_live():
            track_wav.cache_clear()  # ~35 MB: keep it only while a session plays it
