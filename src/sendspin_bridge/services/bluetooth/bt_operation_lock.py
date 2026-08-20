"""Deprecated shim over the adapter lease.

The exclusive right to drive a Bluetooth controller now belongs to
:mod:`sendspin_bridge.bluetooth.adapter_session`: a caller takes a
:class:`~sendspin_bridge.bluetooth.adapter_session.Lease` from an
``AdapterHandle`` and reaches the mutating verbs through the session that
lease carries, so "forgot to take the lock" cannot be expressed.

These two functions remain only while the call sites migrate.  They operate
on the very same lock object, so a half-migrated bridge still serialises
correctly, and they track their own token — a legacy release can no longer
free a lease taken through the object API.
"""

from __future__ import annotations

from sendspin_bridge.bluetooth.adapter_session import (
    _bt_operation_lock,  # noqa: F401  — re-exported for existing test fixtures
    release_bt_operation,
    try_acquire_bt_operation,
)

__all__ = ["release_bt_operation", "try_acquire_bt_operation"]
