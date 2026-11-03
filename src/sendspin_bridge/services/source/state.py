"""One typed snapshot of the source's runtime state.

The source client runs on the bridge's event loop; readers live in API worker threads and
in the hardware read model, so every writer publishes an immutable snapshot under a lock
and every reader takes a whole one.

The keys of :meth:`SourceState.status_dict` are the wire contract
(``SendspinSourceStatus``); do not rename them without updating the models and the SPA.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Literal

__all__ = ["SourceDevice", "SourceState", "publish", "reset", "snapshot"]

SourceStateName = Literal["off", "pairing", "waiting", "streaming", "error"]


@dataclass(frozen=True, slots=True)
class SourceDevice:
    """A paired Bluetooth device that can stream into the bridge."""

    mac: str
    name: str
    connected: bool = False
    active: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"mac": self.mac, "name": self.name, "connected": self.connected, "active": self.active}


@dataclass(frozen=True, slots=True)
class SourceState:
    """What the source is doing right now, as one consistent snapshot."""

    enabled: bool = False
    state: SourceStateName = "off"
    phone: str | None = None
    phone_mac: str | None = None
    paired_phone_mac: str | None = None
    streaming: bool = False
    server: str | None = None
    ma_connected: bool = False
    ma_paired: bool = False
    ma_pairing_code: str | None = None
    ma_pairing_until: float = 0.0
    ma_error: str | None = None
    pairing_until: float = 0.0
    pairing_adapters: tuple[str, ...] = ()
    pairing_error: str | None = None
    error: str | None = None
    devices: tuple[SourceDevice, ...] = ()

    def pairing_seconds_left(self, *, now: float | None = None) -> int:
        """Seconds the phone's pairing window stays open (0 when it is closed)."""
        return _seconds_left(self.pairing_until, now)

    def ma_pairing_seconds_left(self, *, now: float | None = None) -> int:
        """Seconds the Music Assistant pairing window stays open (0 when it is closed)."""
        return _seconds_left(self.ma_pairing_until, now)

    def status_dict(self) -> dict[str, Any]:
        """The bridge-status shape (the keys ``SendspinSourceStatus`` reads)."""
        return {
            "enabled": self.enabled,
            "state": self.state,
            "phone": self.phone,
            "phone_mac": self.phone_mac,
            "paired_phone_mac": self.paired_phone_mac,
            "streaming": self.streaming,
            "server": self.server,
            "ma_connected": self.ma_connected,
            "ma_paired": self.ma_paired,
            "ma_pairing_required": self.enabled and not self.ma_paired,
            "ma_pairing_code": self.ma_pairing_code,
            "ma_pairing_seconds_left": self.ma_pairing_seconds_left(),
            "ma_error": self.ma_error,
            "pairing_seconds_left": self.pairing_seconds_left(),
            "pairing_adapters": list(self.pairing_adapters),
            "devices": [device.as_dict() for device in self.devices],
            "error": self.error,
        }


def _seconds_left(until: float, now: float | None) -> int:
    if not until:
        return 0
    current = time.time() if now is None else now
    return max(0, int(until - current))


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
