"""Wrapper around ``bluetooth-auto-recovery`` for recovering a stuck BT
adapter via a progressive ladder (HCI mgmt reset → rfkill unblock →
USB unbind/rebind). The library is Linux-only; we fail soft if it
isn't installed (dev machines, non-Linux platforms) or if the running
user lacks the capabilities for the recovery steps it tries.

Entry point is ``recover_adapter_blocking(hci_index, adapter_mac)`` —
synchronous, safe to call from the BT reconnect-loop thread. A
per-adapter-MAC cooldown prevents the reconnect loop from thrashing
the library when threshold-triggered recoveries fail back-to-back.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

# Cooldown in seconds. Recovery can unbind/rebind the USB device, which
# briefly disconnects every device on that controller — so we don't
# want two threshold hits (e.g. two devices on the same adapter going
# silent simultaneously) to trigger recovery twice in rapid succession.
_COOLDOWN_SECONDS: float = 60.0

_MAC_RE = re.compile(r"^[\dA-Fa-f]{2}(:[\dA-Fa-f]{2}){5}$")

_last_attempt_lock = threading.Lock()
_last_attempt: dict[str, float] = {}

try:
    from bluetooth_auto_recovery import recover_adapter as _lib_recover_adapter
except Exception as _e:  # pragma: no cover — only hit when lib missing at import time
    logger.debug("bluetooth-auto-recovery unavailable: %s", _e)
    _lib_recover_adapter = None  # type: ignore[assignment]


def _reset_state_for_tests() -> None:
    """Clear cooldown tracking. Test-only hook."""
    with _last_attempt_lock:
        _last_attempt.clear()


def _is_cooldown_active(adapter_mac: str, *, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _last_attempt_lock:
        last = _last_attempt.get(adapter_mac)
        if last is None:
            return False
        return (now - last) < _COOLDOWN_SECONDS


def _mark_attempted(adapter_mac: str) -> None:
    with _last_attempt_lock:
        _last_attempt[adapter_mac] = time.monotonic()


def recover_adapter_blocking(*, hci_index: int, adapter_mac: str) -> bool:
    """Run the ``bluetooth-auto-recovery`` ladder on *adapter_mac*
    (identified to the library by *hci_index*). Returns ``True`` only
    when the library itself reports success. Returns ``False`` on:

    - invalid MAC
    - library not installed
    - another recovery attempt for this adapter happened within the
      last ``_COOLDOWN_SECONDS``
    - the library raised (permissions, netlink unavailable, etc.)
    - the library returned ``False``
    """
    if not _MAC_RE.match(adapter_mac):
        logger.warning("adapter_recovery: invalid adapter MAC %r — skipping", adapter_mac)
        return False

    if _lib_recover_adapter is None:
        logger.debug("adapter_recovery: bluetooth-auto-recovery not installed — skipping %s", adapter_mac)
        return False

    if _is_cooldown_active(adapter_mac):
        logger.info(
            "adapter_recovery: skipping %s (hci%d) — attempted within last %ds",
            adapter_mac,
            hci_index,
            int(_COOLDOWN_SECONDS),
        )
        return False

    _mark_attempted(adapter_mac)
    logger.warning(
        "adapter_recovery: running recovery ladder on %s (hci%d) via bluetooth-auto-recovery",
        adapter_mac,
        hci_index,
    )
    try:
        result = asyncio.run(_lib_recover_adapter(hci_index, adapter_mac))
    except Exception as e:
        logger.warning("adapter_recovery: recovery raised on %s (hci%d): %s", adapter_mac, hci_index, e)
        return False
    if result:
        logger.info("adapter_recovery: recovered %s (hci%d)", adapter_mac, hci_index)
    else:
        logger.warning("adapter_recovery: library reported failure on %s (hci%d)", adapter_mac, hci_index)
    return bool(result)
