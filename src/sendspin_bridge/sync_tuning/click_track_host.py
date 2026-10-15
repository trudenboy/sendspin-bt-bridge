"""Hosts the click track for Music Assistant under per-session, unguessable URLs."""

from __future__ import annotations

import hmac
import secrets
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from sendspin_bridge.config.network import get_local_ip, resolve_web_port
from sendspin_sync.click_track import render_wav

if TYPE_CHECKING:
    from collections.abc import Callable


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


tokens = ClickTrackTokens()


def default_base_url() -> str:
    return f"http://{get_local_ip() or '127.0.0.1'}:{resolve_web_port()}"


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
