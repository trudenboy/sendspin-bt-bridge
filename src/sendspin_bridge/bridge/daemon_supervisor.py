"""What to do when a speaker's daemon dies.

Restarting is not unconditional.  A daemon that died because Bluetooth went
away must wait for Bluetooth rather than spin; one that cannot bind its
listen port must stop trying; one that dies at the same moment on every
attempt is hitting a deterministic timeout — a handshake deadline, an
unreachable host — which is actionable for the operator in a way that "it
keeps restarting" is not.

All of that lived inside a 120-line branch of the status loop, tangled with
the status writes and log lines it produced, so none of it could be asked a
question without a running loop and a real subprocess.  Here the decisions
come back as data and the client carries them out.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "DaemonSupervisor",
    "Halted",
    "RestartAfter",
    "RestartDecision",
    "SpawnRecord",
    "WaitForBluetooth",
]

#: A restart delay short enough that a one-off crash looks instant.
BASE_BACKOFF_S = 1.0
#: …and a ceiling, so a daemon that cannot start stops hammering the host.
MAX_BACKOFF_S = 30.0
#: Spawns kept for the diagnostics bundle and the pattern check.
HISTORY = 10


@dataclass
class SpawnRecord:
    """One Sendspin daemon subprocess spawn — entry created on spawn, fields filled on exit.

    Captured per device to expose *why* a daemon exited (exit code, signal,
    lifetime, last stderr lines) so issue-#291-style debugging doesn't require
    correlating windows of MA logs and bridge logs by hand.
    """

    pid: int
    spawn_at: datetime
    exit_at: datetime | None = None
    exit_code: int | None = None
    signal: int | None = None
    lifetime_s: float | None = None
    stderr_tail: list[str] = field(default_factory=list)
    # ``unexpected`` distinguishes daemon deaths driven by ``stop_sendspin``
    # (graceful release / shutdown — False) from deaths that surprise the
    # parent (True).  Only unexpected deaths drive ``last_error`` updates and
    # repeating-interval pattern detection.
    unexpected: bool = True


@dataclass(frozen=True)
class RestartAfter:
    """Respawn once *delay_s* has passed."""

    delay_s: float


@dataclass(frozen=True)
class WaitForBluetooth:
    """Do nothing — the Bluetooth monitor will start it when the link returns."""


@dataclass(frozen=True)
class Halted:
    """Something said stop trying; sleep and check again."""

    delay_s: float


RestartDecision = RestartAfter | WaitForBluetooth | Halted


class DaemonSupervisor:
    """Decides whether a dead daemon should come back, and when."""

    def __init__(
        self, *, history: int = HISTORY, base_backoff_s: float = BASE_BACKOFF_S, max_backoff_s: float = MAX_BACKOFF_S
    ):
        self._base = base_backoff_s
        self._max = max_backoff_s
        self._backoff = base_backoff_s
        self._halted = False
        self._history: deque[SpawnRecord] = deque(maxlen=history)

    # -- restart policy ------------------------------------------------

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def restart_delay(self) -> float:
        """How long the next restart would wait, for diagnostics to report."""
        return self._backoff

    def halt(self) -> None:
        """Stop respawning until a daemon comes up again."""
        self._halted = True

    def on_alive(self) -> None:
        """A daemon is running: forget the backoff and any halt."""
        self._backoff = self._base
        self._halted = False

    def on_death(self, *, bt_connected: bool | None) -> RestartDecision:
        """Decide what a daemon death means.

        *bt_connected* is ``None`` for a bridge with no Bluetooth manager,
        where nothing else would ever restart the daemon.
        """
        if self._halted:
            return Halted(delay_s=self._backoff)
        if bt_connected is False:
            # The Bluetooth monitor calls start_sendspin when the link is
            # back, so the next restart should not inherit this backoff.
            self._backoff = self._base
            return WaitForBluetooth()
        delay = self._backoff
        self._backoff = min(self._backoff * 2, self._max)
        return RestartAfter(delay_s=delay)

    # -- spawn history -------------------------------------------------

    def record(self, spawn: SpawnRecord) -> None:
        self._history.append(spawn)

    def records(self) -> list[SpawnRecord]:
        return list(self._history)

    def repeating_lifetime(self, tolerance_s: float = 1.0) -> float | None:
        """Mean lifetime when the last 3 unexpected deaths landed within ±*tolerance_s*.

        ``None`` when fewer than three are recorded or when they spread wider
        than that.  A value means the daemon is hitting a deterministic
        deadline rather than failing at random, which is the difference
        between "check your server address" and "check your logs".
        """
        completed = [r.lifetime_s for r in self._history if r.lifetime_s is not None and r.unexpected]
        if len(completed) < 3:
            return None
        last3 = completed[-3:]
        if all(abs(x - last3[0]) <= tolerance_s for x in last3):
            return sum(last3) / 3
        return None

    def recent(self, n: int = 5) -> list[dict[str, Any]]:
        """A JSON-serialisable view of the last *n* spawns, oldest first.

        Consumed by the diagnostics bundle so an operator filing an issue
        brings the daemon lifetime pattern with them.
        """
        return [
            {
                "pid": r.pid,
                "spawn_at": r.spawn_at.isoformat(),
                "exit_at": r.exit_at.isoformat() if r.exit_at else None,
                "lifetime_s": r.lifetime_s,
                "exit_code": r.exit_code,
                "signal": r.signal,
                "unexpected": r.unexpected,
                "stderr_tail": list(r.stderr_tail),
            }
            for r in list(self._history)[-n:]
        ]
