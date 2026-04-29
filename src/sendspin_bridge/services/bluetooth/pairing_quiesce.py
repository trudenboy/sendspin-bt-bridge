"""Pair-time peer quiesce for single-adapter multi-speaker setups.

Some BT adapters (plus the BlueZ 5.78→5.86 legacy-pair regression band) refuse
to admit a second device while the adapter already holds an active A2DP ACL.
This module provides an opt-in context manager that briefly pauses every other
active client on the target adapter during pairing and restores them after,
without unpairing anything.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

import sendspin_bridge.bridge.state as state

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

_QUIESCE_SETTLE_SECONDS = 1.5


@contextmanager
def quiesce_adapter_peers(
    adapter_mac: str,
    *,
    exclude_mac: str | None = None,
) -> Iterator[list]:
    """Pause reconnect + disconnect all active peers on ``adapter_mac``.

    Peers are restored in reverse order inside ``finally`` — even if the
    wrapped pair flow raises. Bonds are preserved (``disconnect_device`` drops
    the ACL but does not unpair).
    """
    paused: list = []
    target = exclude_mac.upper() if exclude_mac else None

    snapshot = state.get_clients_snapshot()

    for client in snapshot:
        mgr = getattr(client, "bt_manager", None)
        if mgr is None:
            continue
        peer_adapter = getattr(mgr, "effective_adapter_mac", "") or ""
        if not peer_adapter:
            continue
        if peer_adapter.upper() != adapter_mac.upper():
            continue
        if target and getattr(mgr, "mac_address", "").upper() == target:
            continue
        if not getattr(mgr, "connected", False):
            continue
        reconnect_cancelled = False
        try:
            mgr.cancel_reconnect()
            reconnect_cancelled = True
            mgr.disconnect_device()
        except Exception:
            logger.exception("[pair-quiesce] failed to pause %s", getattr(mgr, "mac_address", "?"))
            # If cancel_reconnect already succeeded, a failing disconnect would
            # otherwise leave this peer stuck with reconnect disabled. Roll back.
            if reconnect_cancelled:
                try:
                    mgr.allow_reconnect()
                except Exception:
                    logger.exception(
                        "[pair-quiesce] failed to restore reconnect for %s",
                        getattr(mgr, "mac_address", "?"),
                    )
            continue
        paused.append(client)
        logger.info(
            "[pair-quiesce] paused peer %s on %s (bond retained)",
            getattr(mgr, "device_name", "?"),
            adapter_mac,
        )

    if paused:
        time.sleep(_QUIESCE_SETTLE_SECONDS)

    try:
        yield paused
    finally:
        for client in reversed(paused):
            mgr = getattr(client, "bt_manager", None)
            if mgr is None:
                continue
            try:
                mgr.allow_reconnect()
                mgr.signal_standby_wake()
                logger.info(
                    "[pair-quiesce] resumed peer %s",
                    getattr(mgr, "device_name", "?"),
                )
            except Exception:
                logger.exception(
                    "[pair-quiesce] failed to resume %s",
                    getattr(mgr, "mac_address", "?"),
                )
