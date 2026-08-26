"""The facts about one device, named once.

A device snapshot carries its facts two ways: as typed fields, and as
``extra`` — the whole runtime status dict plus three dozen keys added by
hand.  Everything that describes a device to an operator reads from that bag
by string: the normalised state, the capability builder, the guidance.  Every
one of those strings is a chance to ask for a key nobody writes, and the
answer to such a question is not an error but a ``None`` that travels to the
screen.  Two of them shipped: the audio sink name and the reconnect limit,
each reported as missing on devices that had one.

This is that bag with names on it.  Ask it a question it does not know and
you get an ``AttributeError`` where the mistake is, instead of a blank field
somewhere else.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DeviceFacts"]


class DeviceFacts:
    """A read-only view of one device snapshot's facts.

    Typed fields on the snapshot win over the bag: the bag is a copy of the
    runtime status made at snapshot time, and where the snapshot resolved a
    fact of its own — the sink it actually found, for instance — that is the
    answer.
    """

    __slots__ = ("_extra", "_snapshot")

    def __init__(self, snapshot: Any):
        self._snapshot = snapshot
        extra = getattr(snapshot, "extra", None)
        if not isinstance(extra, dict) and isinstance(snapshot, dict):
            extra = snapshot.get("extra")
        self._extra = extra if isinstance(extra, dict) else {}

    # -- how a fact is found -------------------------------------------

    def _field(self, name: str, default: Any = None) -> Any:
        value = getattr(self._snapshot, name, None)
        if value is None and isinstance(self._snapshot, dict):
            value = self._snapshot.get(name)
        return default if value is None else value

    def _fact(self, name: str, default: Any = None) -> Any:
        value = self._extra.get(name)
        return default if value is None else value

    # -- identity -------------------------------------------------------

    @property
    def player_name(self) -> str:
        return str(self._field("player_name", "") or "")

    @property
    def enabled(self) -> bool:
        return bool(self._field("enabled", True))

    # -- bluetooth ------------------------------------------------------

    @property
    def bluetooth_mac(self) -> str | None:
        return self._field("bluetooth_mac")

    @property
    def bluetooth_connected(self) -> bool:
        return bool(self._field("bluetooth_connected", False))

    @property
    def bluetooth_paired(self) -> bool | None:
        return self._fact("bluetooth_paired")

    @property
    def bluetooth_adapter(self) -> str | None:
        return self._fact("bluetooth_adapter")

    @property
    def bluetooth_adapter_hci(self) -> str | None:
        return self._fact("bluetooth_adapter_hci")

    @property
    def bluetooth_adapter_name(self) -> str | None:
        return self._fact("bluetooth_adapter_name")

    @property
    def standby(self) -> bool:
        return bool(self._fact("bt_standby", False))

    @property
    def released_by(self) -> str | None:
        return self._fact("bt_released_by")

    @property
    def never_paired(self) -> bool:
        return bool(self._fact("never_paired", False))

    @property
    def never_paired_since(self) -> Any:
        return self._fact("never_paired_since")

    @property
    def pair_failure_kind(self) -> str | None:
        return self._fact("pair_failure_kind")

    @property
    def pair_failure_adapter_mac(self) -> str | None:
        return self._fact("pair_failure_adapter_mac")

    @property
    def pair_failure_at(self) -> Any:
        return self._fact("pair_failure_at")

    # -- reconnect ------------------------------------------------------

    @property
    def reconnecting(self) -> bool:
        return bool(self._fact("reconnecting", False))

    @property
    def reconnect_attempt(self) -> int | None:
        value = self._fact("reconnect_attempt")
        return value if isinstance(value, int) else None

    @property
    def max_reconnect_fails(self) -> int | None:
        value = self._fact("max_reconnect_fails")
        return value if isinstance(value, int) else None

    @property
    def reconnect_attempts_remaining(self) -> int | None:
        """Derived, not stored — two places used to compute it separately."""
        attempt, limit = self.reconnect_attempt, self.max_reconnect_fails
        if attempt is None or limit is None:
            return None
        return max(limit - attempt, 0)

    # -- audio ----------------------------------------------------------

    @property
    def has_sink(self) -> bool:
        return bool(self._field("has_sink", False))

    @property
    def sink_name(self) -> str | None:
        return self._field("sink_name") or self._fact("sink_name")

    @property
    def audio_streaming(self) -> bool:
        return bool(self._fact("audio_streaming", False))

    # -- runtime --------------------------------------------------------

    @property
    def stopping(self) -> bool:
        return bool(self._fact("stopping", False))

    @property
    def reanchoring(self) -> bool:
        return bool(self._fact("reanchoring", False))

    @property
    def ma_reconnecting(self) -> bool:
        return bool(self._fact("ma_reconnecting", False))

    @property
    def ma_connected(self) -> bool:
        return bool(self._fact("ma_connected", False))
