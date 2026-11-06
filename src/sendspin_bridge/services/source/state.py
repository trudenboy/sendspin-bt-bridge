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

__all__ = ["SourceDevice", "SourceState", "attach_event_hub", "publish", "publish_level", "reset", "snapshot"]

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
    target_player: str | None = None
    active_input: str | None = None
    target_error: str | None = None
    devices: tuple[SourceDevice, ...] = ()
    #: Smoothed peak of the frames the capture is feeding, 0..1.  Silence is 0.0,
    #: never "unknown" — but it is not part of the status payload (see publish_level).
    level: float = 0.0

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
            "target_player": self.target_player,
            "active_input": self.active_input,
            "target_error": self.target_error,
        }


def _seconds_left(until: float, now: float | None) -> int:
    if not until:
        return 0
    current = time.time() if now is None else now
    return max(0, int(until - current))


_lock = threading.Lock()
_state = SourceState()
_hub: Any = None


#: Fields the bridge status exposes, so a change to one is worth a status frame.
#: ``devices`` and the pairing clocks are included: a bond that closes a window
#: and a window that opens both have to reach the SPA on their own.
_VISIBLE = (
    "enabled",
    "state",
    "phone",
    "phone_mac",
    "paired_phone_mac",
    "streaming",
    "server",
    "ma_connected",
    "ma_paired",
    "ma_pairing_code",
    "ma_pairing_until",
    "ma_error",
    "pairing_until",
    "pairing_adapters",
    "pairing_error",
    "error",
    "target_player",
    "target_error",
    "active_input",
    "devices",
)


def publish(**changes: Any) -> None:
    """Replace the snapshot with a copy carrying *changes* (unknown fields raise).

    A change the status payload shows is announced to the event stream, because
    nothing else will: the source owns no heartbeat of its own, so a window that
    closed or a phone that bonded would otherwise wait for an unrelated device's
    status tick.  A publish that changes nothing visible stays silent.
    """
    global _state
    with _lock:
        previous = _state
        _state = replace(_state, **changes)
        announced = any(getattr(previous, name) != getattr(_state, name) for name in _VISIBLE)
    if announced:
        from sendspin_bridge.services.lifecycle.bridge_runtime_state import notify_status_changed

        notify_status_changed()


def attach_event_hub(hub: Any) -> None:
    """Attach the API's EventHub (or a test double); ``None`` detaches it."""
    global _hub
    _hub = hub


def publish_level(level: float, *, input_id: str | None, muted: bool) -> None:
    """Record the smoothed input level and publish one ``source.level`` event.

    The level is deliberately not a status-visible field and not part of the
    status payload: it changes ten times a second, and the status event rebuilds
    the whole bridge document (about 60 ms) on every change.  Only listeners
    that asked for ``source.level`` receive this.
    """
    publish(level=float(level))
    hub = _hub
    if hub is None:
        return
    from sendspin_bridge.api.events import envelope

    hub.publish_threadsafe(envelope("source.level", {"id": input_id, "level": float(level), "muted": bool(muted)}))


def snapshot() -> SourceState:
    """The current snapshot; immutable, so it cannot change while it is read."""
    with _lock:
        return _state


def reset() -> None:
    """Forget everything (tests, and a cold start that never had a source)."""
    global _state, _hub
    with _lock:
        _state = SourceState()
        _hub = None
