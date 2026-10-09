"""Brute-force lockout, with the two windows kept apart.

There are two durations here and they mean different things: the *window* is
how long failed attempts keep accumulating, and the *lockout* is how long a
client waits once they have accumulated enough.

The previous implementation recorded one timestamp — the first failure — and
measured both from it.  So the lockout ran from the first failure rather than
from the moment it began: with the defaults (5 attempts in a minute, five
minute lockout) a user who spent 50 seconds burning their attempts served
4m10s.  Slower attempts bought a shorter sentence, which is backwards.

The state also lived in a module-level dict, so the only way to test any of
it was to reach in and mutate that dict.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["LockoutSettings", "LoginRateLimiter"]

#: Above this many tracked clients, sweep the expired ones.
_SWEEP_THRESHOLD = 200
#: Hard ceiling, so a flood of distinct identifiers cannot exhaust memory.
_MAX_TRACKED = 1000


@dataclass(frozen=True)
class LockoutSettings:
    """What counts as too many attempts, and for how long."""

    enabled: bool = True
    max_attempts: int = 5
    #: Failures within this many seconds of each other accumulate.
    window_s: float = 60.0
    #: Once locked out, how long the client waits.
    lockout_s: float = 300.0


@dataclass
class _Record:
    count: int
    first_failure_at: float
    #: When the threshold was crossed — ``None`` while still counting.
    locked_at: float | None = None
    last_seen_at: float = field(default=0.0)


class LoginRateLimiter:
    """Counts failed logins per client and locks the persistent ones out."""

    def __init__(
        self,
        *,
        settings_provider: Callable[[], LockoutSettings],
        clock: Callable[[], float] | None = None,
    ):
        self._settings = settings_provider
        # Resolved per call rather than captured, so the default follows
        # ``time.monotonic`` wherever it points at the moment of asking.
        self._clock = clock if clock is not None else (lambda: time.monotonic())
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}

    # -- queries -------------------------------------------------------

    def _lock_start(self, record: _Record, settings: LockoutSettings) -> float | None:
        """When this client's lockout began, or ``None`` if it has not.

        The threshold is evaluated here rather than only at record time, so
        lowering ``max_attempts`` takes effect for clients already part-way
        through — which is what the previous implementation did by comparing
        the count on every check.  For those the last failure stands in as
        the start, since that is when they would have crossed the new line.
        """
        if record.locked_at is not None:
            return record.locked_at
        if record.count >= settings.max_attempts:
            return record.last_seen_at
        return None

    def is_locked_out(self, client_id: str) -> bool:
        """True while *client_id* must be refused."""
        settings = self._settings()
        if not settings.enabled:
            return False
        now = self._clock()
        with self._lock:
            record = self._records.get(client_id)
            if record is None:
                return False
            started = self._lock_start(record, settings)
            if started is None:
                return False
            if now - started >= settings.lockout_s:
                del self._records[client_id]
                return False
            return True

    def retry_after(self, client_id: str) -> float:
        """Seconds left on the lockout, or ``0`` when there is none."""
        settings = self._settings()
        if not settings.enabled:
            return 0.0
        now = self._clock()
        with self._lock:
            record = self._records.get(client_id)
            if record is None:
                return 0.0
            started = self._lock_start(record, settings)
            if started is None:
                return 0.0
            return max(0.0, settings.lockout_s - (now - started))

    def tracked(self) -> int:
        """How many clients are currently remembered (for tests and metrics)."""
        with self._lock:
            return len(self._records)

    # -- updates -------------------------------------------------------

    def record_failure(self, client_id: str) -> None:
        """Note one failed attempt."""
        settings = self._settings()
        if not settings.enabled:
            return
        now = self._clock()
        with self._lock:
            record = self._records.get(client_id)

            if record is not None and record.locked_at is not None:
                if now - record.locked_at >= settings.lockout_s:
                    # The sentence was served; this failure starts a fresh count
                    # rather than extending a lockout that already expired.
                    record = None
                else:
                    # Already locked out.  Count it, but do not move
                    # ``locked_at`` — otherwise anyone hammering the door
                    # keeps the real user shut out indefinitely.
                    record.count += 1
                    record.last_seen_at = now
                    return

            if record is None or now - record.first_failure_at > settings.window_s:
                record = _Record(count=1, first_failure_at=now, last_seen_at=now)
            else:
                record.count += 1
                record.last_seen_at = now

            if record.count >= settings.max_attempts:
                record.locked_at = now

            self._records[client_id] = record
            self._prune_locked(now, settings)

    def clear(self, client_id: str) -> None:
        """Forget a client — called on a successful login."""
        with self._lock:
            self._records.pop(client_id, None)

    # -- housekeeping --------------------------------------------------

    def _prune_locked(self, now: float, settings: LockoutSettings) -> None:
        """Drop records nobody is waiting on. Caller holds the lock."""
        if len(self._records) <= _SWEEP_THRESHOLD:
            return
        max_age = max(settings.window_s, settings.lockout_s)
        expired = [key for key, rec in self._records.items() if now - rec.last_seen_at > max_age]
        for key in expired:
            del self._records[key]
        while len(self._records) > _MAX_TRACKED:
            oldest = min(self._records, key=lambda key: self._records[key].last_seen_at)
            del self._records[oldest]
