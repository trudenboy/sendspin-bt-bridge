"""Short-lived, unguessable tokens for the phone capture page.

A token stands for one measuring session and allows exactly two things, through the public
``/api/v1/sync/capture/{token}`` endpoints: reading that session's capture state (which speaker
plays now) and uploading a recording for it. It is not a credential anywhere else (the rest of the
API never looks at it). One token per session: issuing a new link revokes the old one; a token
expires CAPTURE_TTL_S after it was issued and is revoked when its session ends.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: How long a capture link works: long enough to measure a room, short enough to be worthless later.
CAPTURE_TTL_S = 30 * 60.0


class CaptureTokens:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._by_session: dict[str, tuple[str, float]] = {}

    def issue(self, session_id: str, ttl_s: float = CAPTURE_TTL_S) -> str:
        """A new token for *session_id* (256 bits, URL-safe); the session's previous one stops working."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._by_session[session_id] = (token, self._clock() + ttl_s)
        return token

    def expires_in(self, session_id: str) -> float | None:
        with self._lock:
            entry = self._by_session.get(session_id)
        return None if entry is None else max(0.0, entry[1] - self._clock())

    def session_for(self, token: str) -> str | None:
        """The session a live token stands for; None when unknown, expired or revoked. Compares
        against every token, so the time taken does not reveal a partial match."""
        now = self._clock()
        with self._lock:
            items = list(self._by_session.items())
        found: str | None = None
        for sid, (known, expires) in items:
            if hmac.compare_digest(known.encode(), token.encode()) and now <= expires:
                found = sid
        return found

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)


tokens = CaptureTokens()


def current() -> CaptureTokens:
    return tokens  # looked up per call, so tests can swap the module attribute
