"""What to do when a Bluetooth reconnect fails.

Backoff, churn detection, the never-paired auto-disable and the auto-reclaim
quiet period are arithmetic over counters and a clock — no BlueZ, no D-Bus, no
config writes.  They used to live inside :class:`BluetoothManager`, tangled
with the status updates and persistence that carry them out, so the only way
to reach a decision was to stand up a manager and mock its world.

This module owns the decisions and nothing else.  ``on_failure`` returns one
of the decision types below and the manager executes it: releasing
management, disabling the device, running the adapter-recovery ladder.  The
split is what makes the decisions table-testable, and it keeps the recovery
ladder's one-shot rule ("try it before releasing, but only once") in the same
place as the rule it guards.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "Churned",
    "DisableNeverPaired",
    "KeepTrying",
    "ReconnectDecision",
    "ReconnectPolicy",
    "ReleaseManagement",
    "TryAdapterRecovery",
]

#: Backoff ceiling: five minutes between attempts for a speaker that is
#: simply switched off.
MAX_RECONNECT_DELAY_S = 300.0

#: How long a device must stay auto-released before an external connect may
#: reclaim management (issues #349/#350).  Keeps a churn-released speaker
#: from flapping management on and off while it is still bouncing.
AUTO_RECLAIM_QUIET_S = 60.0


@dataclass(frozen=True)
class KeepTrying:
    """Nothing to do — keep reconnecting on the usual cadence."""


@dataclass(frozen=True)
class Churned:
    """Too many reconnects inside the window: release to protect the group."""

    reconnect_count: int
    window_s: float


@dataclass(frozen=True)
class TryAdapterRecovery:
    """Run the adapter-recovery ladder, then ask again."""

    attempt: int


@dataclass(frozen=True)
class DisableNeverPaired:
    """BlueZ never knew this device and it never connected: disable it."""

    attempt: int


@dataclass(frozen=True)
class ReleaseManagement:
    """Too many consecutive failures: hand the adapter back to the host."""

    attempt: int
    threshold: int


ReconnectDecision = KeepTrying | Churned | TryAdapterRecovery | DisableNeverPaired | ReleaseManagement


class ReconnectPolicy:
    """Decides what a failed reconnect means. Holds no BlueZ state."""

    def __init__(
        self,
        *,
        check_interval: float,
        max_fails: int,
        churn_threshold: int,
        churn_window: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.check_interval = check_interval
        self.max_fails = max_fails
        self.churn_threshold = churn_threshold
        self.churn_window = churn_window
        self._clock = clock
        self._lock = threading.Lock()
        self._reconnects: list[float] = []
        self._released_at: float | None = None

    # -- backoff -------------------------------------------------------

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait before the next attempt.

        Attempts 1–3 use the poll interval; it doubles from there, capped at
        :data:`MAX_RECONNECT_DELAY_S`.  The point is to stop hammering the
        radio — and disrupting other speakers on the same controller — for a
        speaker that is simply switched off.
        """
        return min(self.check_interval * (2 ** max(0, attempt - 3)), MAX_RECONNECT_DELAY_S)

    # -- churn window --------------------------------------------------

    def record_reconnect(self) -> None:
        """Note a reconnect for churn detection."""
        now = self._clock()
        with self._lock:
            self._reconnects.append(now)
            self._prune(now)

    def reconnect_count(self) -> int:
        """Reconnects inside the current window."""
        with self._lock:
            self._prune(self._clock())
            return len(self._reconnects)

    def clear_reconnects(self) -> None:
        with self._lock:
            self._reconnects.clear()

    def _prune(self, now: float) -> None:
        cutoff = now - self.churn_window
        self._reconnects = [t for t in self._reconnects if t > cutoff]

    # -- decisions -----------------------------------------------------

    def on_failure(
        self,
        attempt: int,
        *,
        paired: bool | None,
        ever_paired: bool,
        recovery_available: bool = False,
        recovery_attempted: bool = False,
    ) -> ReconnectDecision:
        """Decide what this failed attempt means.

        *paired* is the tri-state pairing answer — ``None`` means BlueZ has no
        record of the device, which is different from "not paired".
        *ever_paired* is whether this bridge session ever saw the speaker
        connected; the pair distinguishes "misconfigured from the start" from
        "was working and went away".
        """
        if self.churn_threshold > 0:
            count = self.reconnect_count()
            if count >= self.churn_threshold:
                return Churned(reconnect_count=count, window_s=self.churn_window)

        if self.max_fails <= 0 or attempt < self.max_fails:
            return KeepTrying()

        if recovery_available and not recovery_attempted:
            return TryAdapterRecovery(attempt=attempt)

        if paired is None and not ever_paired:
            return DisableNeverPaired(attempt=attempt)

        return ReleaseManagement(attempt=attempt, threshold=self.max_fails)

    # -- auto-release / reclaim ----------------------------------------

    def mark_released(self) -> None:
        """Start the quiet period after an automatic release."""
        self._released_at = self._clock()

    def mark_reclaimed(self) -> None:
        """Management is back: forget the release and the churn history."""
        self._released_at = None
        self.clear_reconnects()

    def may_reclaim(self, *, connected: bool | None) -> bool:
        """True when an established link may reclaim management.

        Requires an actual link — ``None`` ("BlueZ could not say") is not one
        — and a quiet period since *this run's* automatic release, so a
        speaker that is still bouncing cannot flap management on and off.

        A release carried over from a previous run has no quiet period to
        observe: the speaker connecting is the only evidence available, and
        the churn that caused the release ended with the process.
        """
        if connected is not True:
            return False
        if self._released_at is None:
            return True
        return (self._clock() - self._released_at) >= AUTO_RECLAIM_QUIET_S
