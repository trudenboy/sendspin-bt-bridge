"""One address, however it is spelled.

The same speaker address is written three ways here.  BlueZ and the config
use colons.  Audio sink names, D-Bus object paths and MPRIS paths use
underscores.  The sysfs adapter map is keyed bare.  Fifteen call sites did
that conversion by hand — ``mac.upper().replace(":", "_")`` and its cousins —
and each one had to remember on its own to fold case before comparing.

A type that knows all three spellings takes the remembering out of it: parse
once at the edge, ask for the spelling the consumer needs, and let equality be
equality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["DeviceAddress"]

#: Six hex pairs, separated by colons, underscores, or nothing at all.
_ADDRESS_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}([:_]?)(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2})$")


@dataclass(frozen=True, slots=True)
class DeviceAddress:
    """A Bluetooth device address, canonical and comparable.

    Built through :meth:`parse` or :meth:`require` — the constructor takes the
    canonical bare form and does not check it, so nothing but those two ways
    in can produce one.
    """

    bare: str

    # -- coming in ------------------------------------------------------

    @classmethod
    def parse(cls, text: object) -> DeviceAddress | None:
        """The address *text* names, or ``None`` when it names none."""
        if isinstance(text, DeviceAddress):
            return text
        if not isinstance(text, str):
            return None
        candidate = text.strip()
        if not _ADDRESS_RE.match(candidate):
            return None
        return cls(candidate.replace(":", "").replace("_", "").upper())

    @classmethod
    def require(cls, text: object) -> DeviceAddress:
        """Like :meth:`parse`, for callers that cannot continue without one."""
        address = cls.parse(text)
        if address is None:
            raise ValueError(f"{text!r} is not a Bluetooth device address")
        return address

    # -- going out ------------------------------------------------------

    @property
    def colons(self) -> str:
        """``AA:BB:CC:DD:EE:FF`` — BlueZ, bluetoothctl, the config file."""
        return self._grouped(":")

    @property
    def underscores(self) -> str:
        """``AA_BB_CC_DD_EE_FF`` — audio sink names and D-Bus paths."""
        return self._grouped("_")

    @property
    def dbus_node(self) -> str:
        """``dev_AA_BB_CC_DD_EE_FF`` — the leaf of a BlueZ object path."""
        return f"dev_{self.underscores}"

    def _grouped(self, separator: str) -> str:
        return separator.join(self.bare[i : i + 2] for i in range(0, 12, 2))

    def __str__(self) -> str:
        return self.colons
