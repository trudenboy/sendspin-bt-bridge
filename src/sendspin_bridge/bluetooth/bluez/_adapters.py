"""Adapter scoping directives and hciN ↔ MAC resolution.

``Adapter`` tells :class:`~.BluezControl` how to scope a command:

* ``Adapter.DEFAULT`` — no ``select`` line; the default controller handles it.
* ``Adapter.select(ident)`` — resolve ``ident`` (``hciN`` or MAC) to a
  controller MAC and emit ``select <MAC>`` first.
* ``Adapter.addressed(mac)`` — **never** emits ``select``; the MAC becomes
  a verb argument (``show <MAC>``).  This is the caller-visible hatch for
  issue #193: ``select <MAC>; show`` is unreliable in piped-stdin mode
  (the D-Bus select may not propagate before ``show`` runs, and the
  stdout then often carries the *default* controller's ``Alias:`` first),
  so alias reads go through the addressed form instead.

Stdlib only — see ``tests/unit/bluetooth/test_bluez_import_rule.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_SYSFS_BLUETOOTH_DIR = Path("/sys/class/bluetooth")
_HCI_RE = re.compile(r"^hci(\d+)$")
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


@dataclass(frozen=True, slots=True)
class Adapter:
    """Scoping directive for a bluetoothctl invocation."""

    mode: str  # "default" | "select" | "addressed"
    ident: str = ""

    DEFAULT: ClassVar[Adapter]

    @classmethod
    def select(cls, ident: str) -> Adapter:
        """Scope via ``select <MAC>``; ``ident`` may be ``hciN`` or a MAC."""
        ident = ident.strip()
        if not ident:
            raise ValueError("Adapter.select() requires an hciN name or MAC")
        return cls(mode="select", ident=ident)

    @classmethod
    def addressed(cls, mac: str) -> Adapter:
        """Pass the controller MAC as a verb argument; never emits select."""
        mac = mac.strip().upper()
        if not mac:
            raise ValueError("Adapter.addressed() requires a controller MAC")
        return cls(mode="addressed", ident=mac)

    @classmethod
    def of(cls, ident: str) -> Adapter:
        """Smart constructor: blank → DEFAULT; anything else → select()."""
        ident = (ident or "").strip()
        if not ident:
            return cls.DEFAULT
        return cls.select(ident)

    @property
    def is_default(self) -> bool:
        return self.mode == "default"


Adapter.DEFAULT = Adapter(mode="default")


def resolve_select_mac(
    ident: str,
    *,
    sysfs_dir: Path = _SYSFS_BLUETOOTH_DIR,
    hci_resolver: Callable[[str], str] | None = None,
    list_macs: Callable[[], list[str]] | None = None,
) -> tuple[str, bool]:
    """Resolve an ``hciN``/MAC select identifier to a controller MAC.

    Resolution order: sysfs (``/sys/class/bluetooth/hciN/address`` — the
    canonical kernel mapping) → injected ``hci_resolver`` (the D-Bus
    object-path lookup, injected by ``bluetooth.manager``) → positional
    index into ``bluetoothctl list`` output (BlueZ registration order —
    last resort, known to diverge from kernel numbering on hot-plug) →
    pass ``hciN`` through unchanged with ``unresolved=True`` so the
    caller surfaces a loud ``select`` failure instead of silently running
    against the wrong controller.

    MAC input passes straight through.  Returns ``(mac, unresolved)``.
    """
    if _MAC_RE.match(ident):
        return ident.upper(), False

    match = _HCI_RE.match(ident)
    index: int | None = None
    if match:
        try:
            index = int(match.group(1))
        except ValueError:  # pragma: no cover - regex guarantees digits
            index = None

    if index is not None:
        try:
            address_file = sysfs_dir / ident / "address"
            addr = address_file.read_text().strip().upper()
            if _MAC_RE.match(addr):
                return addr, False
        except OSError as exc:
            logger.debug("sysfs adapter lookup failed for %s: %s", ident, exc)

        if hci_resolver is not None:
            try:
                resolved = (hci_resolver(ident) or "").strip().upper()
            except Exception as exc:
                logger.debug("injected hci resolver failed for %s: %s", ident, exc)
                resolved = ""
            if _MAC_RE.match(resolved):
                return resolved, False

        if list_macs is not None:
            try:
                macs = list_macs()
            except Exception as exc:
                logger.debug("bluetoothctl list fallback failed for %s: %s", ident, exc)
                macs = []
            if 0 <= index < len(macs):
                return macs[index].upper(), False

    # Deliberate pass-through: a failed ``select hciN`` surfaces loudly in
    # the transcript, whereas dropping the prefix would silently run the
    # command against whichever controller is default.
    return ident, True


def enumerate_sysfs_adapters(sysfs_dir: Path) -> dict[str, str]:
    """Walk *sysfs_dir* (``/sys/class/bluetooth``) → ``{MAC: hciN}``.

    Keys are uppercase, colon-stripped MACs.  This is the canonical kernel
    mapping BlueZ itself honours — ``bluetoothctl list`` enumerates
    controllers in BlueZ registration order, which can diverge from the
    kernel ``hciN`` numbering after a hot-plug (issue #193).  Returns an
    empty dict when the directory is unreadable (non-Linux host, container
    without ``/sys`` mounted).
    """
    mapping: dict[str, str] = {}
    try:
        entries = sorted(sysfs_dir.iterdir())
    except OSError as exc:
        logger.debug("sysfs adapter lookup failed: %s", exc)
        return mapping
    for hci in entries:
        addr_file = hci / "address"
        if not addr_file.exists():
            continue
        try:
            addr = addr_file.read_text().strip().upper().replace(":", "")
        except OSError:
            continue
        if addr:
            mapping[addr] = hci.name
    return mapping


def parse_hciconfig(stdout: str) -> dict[str, str]:
    """Parse ``hciconfig -a`` output → ``{MAC: hciN}`` (same key shape as
    :func:`enumerate_sysfs_adapters`).

    Fallback for hosts where ``/sys/class/bluetooth`` is not mounted into
    the process (the typical Docker case without ``-v /sys:/sys:ro``):
    ``hciconfig`` talks to the kernel via the BlueZ control socket and
    prints the canonical ``hciN`` labels with their MACs.
    """
    mapping: dict[str, str] = {}
    current: str | None = None
    for line in stdout.splitlines():
        head = line.split("\t", 1)[0].strip()
        if head.endswith(":") and head[:-1].startswith("hci") and head[:-1][3:].isdigit():
            current = head[:-1]
            continue
        if current and "BD Address:" in line:
            # "    BD Address: AA:BB:CC:DD:EE:FF  ACL MTU: ..."
            tail = line.split("BD Address:", 1)[1].strip()
            mac = tail.split()[0] if tail else ""
            if _MAC_RE.match(mac):
                mapping[mac.upper().replace(":", "")] = current
            current = None
    return mapping
