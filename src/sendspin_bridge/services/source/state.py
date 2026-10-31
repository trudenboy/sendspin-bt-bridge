"""One typed snapshot of the source's runtime state.

The Sendspin source runs on the bridge's event loop; its state was two
module-level dicts updated key by key and read from API worker threads, so a
reader could see half of a transition.  Every writer now publishes an immutable
snapshot under a lock, and every reader takes a whole one.

The keys of :meth:`SourceState.status_dict` are the wire contract
(``SendspinSourceStatus``); do not rename them.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any

__all__ = ["SourceState", "publish", "reset", "snapshot"]


@dataclass(frozen=True, slots=True)
class SourceState:
    """What the source is doing right now, as one consistent snapshot."""

    enabled: bool = False
    port: int | None = None
    phone: str | None = None
    players: int = 0
    streaming: bool = False
    error: str | None = None
    pairing_until: float = 0.0
    pairing_adapters: tuple[str, ...] = ()
    pairing_error: str | None = None

    def pairing_seconds_left(self, *, now: float | None = None) -> int:
        """Seconds the pairing window stays open (0 when it is closed)."""
        current = time.time() if now is None else now
        return max(0, int(self.pairing_until - current)) if self.pairing_until else 0

    def status_dict(self) -> dict[str, Any]:
        """The bridge-status shape: the same keys ``SendspinSourceStatus`` reads.

        ``attached_devices`` is owned by the relay service rather than this
        snapshot, so it defaults to none here; the bridge status joins the
        armed relays in.  The key is present either way, which keeps
        ``SendspinSourceStatus(**status_dict())`` valid on its own.

        ``error`` is the *source's* error.  A pairing failure is a separate
        field (``pairing_error``) and is reported by the pairing window, not
        merged in here: a failed window does not stop the source streaming.
        """
        return {
            "enabled": self.enabled,
            "port": self.port,
            "phone": self.phone,
            "players": self.players,
            "streaming": self.streaming,
            "error": self.error,
            "pairing_seconds_left": self.pairing_seconds_left(),
            "pairing_adapters": list(self.pairing_adapters),
            "attached_devices": [],
        }


_lock = threading.Lock()
_state = SourceState()


def publish(**changes: Any) -> None:
    """Replace the snapshot with a copy carrying *changes* (unknown fields raise)."""
    global _state
    with _lock:
        _state = replace(_state, **changes)


def snapshot() -> SourceState:
    """The current snapshot; immutable, so it cannot change while it is read."""
    with _lock:
        return _state


def reset() -> None:
    """Forget everything (tests, and a cold start that never had a source)."""
    global _state
    with _lock:
        _state = SourceState()
