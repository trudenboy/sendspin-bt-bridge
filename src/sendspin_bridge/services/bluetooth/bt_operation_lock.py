"""Process-wide lock serialising blocking ``bluetoothctl`` operations.

Multiple parts of the bridge invoke ``bluetoothctl`` synchronously
(pair / reconnect / standalone scan / RSSI background refresh) and
they all share BlueZ's single discovery / agent state machine.
Running two of those at the same time corrupts the captured stdout
and can flip BlueZ into an inconsistent state (e.g. one operation's
``scan off`` cancelling the other's discovery window mid-flight).

This module owns the single ``threading.Lock`` that guards them all.
The HTTP layer (``routes/api_bt.py``) acquires it at the top of pair
/ reset / scan / re-pair endpoints and returns a 409 conflict when
already held; the periodic RSSI refresh in
``BluetoothManager.run_rssi_refresh`` acquires it non-blocking and
just skips the burst when held — its 60 s cadence makes a missed
tick cheap.

Kept transport-agnostic so it can be reused by future async
``bluetoothctl`` callers without coupling to Flask.
"""

from __future__ import annotations

import threading

# Singleton lock shared across all blocking bluetoothctl callers.
_bt_operation_lock = threading.Lock()


def try_acquire_bt_operation() -> bool:
    """Try to acquire the shared BT operation lock without blocking.

    Returns ``True`` when the caller now holds the lock (must call
    :func:`release_bt_operation` to release), ``False`` when another
    operation is already running.
    """
    return _bt_operation_lock.acquire(blocking=False)


def release_bt_operation() -> None:
    """Release the shared BT operation lock.

    No-op when the current thread doesn't hold it (e.g. a previous
    acquire failed and the caller still ran the cleanup path) — the
    underlying ``RuntimeError`` is swallowed to keep finally blocks
    minimal.
    """
    try:
        _bt_operation_lock.release()
    except RuntimeError:
        pass
