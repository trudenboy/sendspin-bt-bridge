"""Thin wrapper around the sendspin CLI daemon.

Monkey-patches SendspinDaemon._create_client() to register a group-update
listener so that MA player group info is emitted to stdout and can be
parsed by the bridge.

This wrapper is a temporary shim until upstream sendspin-cli adds native
group logging in daemon mode (see: https://github.com/Sendspin/sendspin-cli).
Once the upstream PR is merged, this file can be removed and the bridge can
run 'sendspin daemon' directly again.

Usage (via sendspin_client.py):
    python3 /app/services/sendspin_group_daemon.py daemon --name ... --port ...
"""

from __future__ import annotations

import logging
import sys

import sendspin.daemon.daemon as _daemon_mod
from aiosendspin.models.core import GroupUpdateServerPayload

_logger = logging.getLogger(__name__)

# ---- Patch ----------------------------------------------------------------

_orig_create = _daemon_mod.SendspinDaemon._create_client


def _patched_create_client(self, static_delay_ms: float = 0.0):  # type: ignore[override]
    """Create client and attach a group-update listener."""
    client = _orig_create(self, static_delay_ms)
    client.add_group_update_listener(self._handle_group_update)  # type: ignore[attr-defined]
    return client


def _handle_group_update(self, payload: GroupUpdateServerPayload) -> None:
    """Log group update so the bridge can parse it from stdout."""
    if payload.group_id is not None:
        _logger.info("Group ID: %s", payload.group_id)
    # Always log group_name (empty string signals "left group")
    _logger.info("Group name: %s", payload.group_name or "")


_daemon_mod.SendspinDaemon._create_client = _patched_create_client  # type: ignore[method-assign]
_daemon_mod.SendspinDaemon._handle_group_update = _handle_group_update  # type: ignore[attr-defined]

# ---- Entry point ----------------------------------------------------------

from sendspin.cli import main  # noqa: E402  (import after patch)

if __name__ == "__main__":
    sys.exit(main())
