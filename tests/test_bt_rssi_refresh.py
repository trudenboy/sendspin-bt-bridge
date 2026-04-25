"""Tests for ``BluetoothManager``'s periodic RSSI refresh.

The refresh path:

    asyncio loop ──tick every ~30 s──► _rssi_refresh_tick
        ├── short-circuit if not currently connected
        ├── try-acquire the shared BT-operation lock (skip if pair/scan running)
        ├── delegate to ``services.bt_rssi_mgmt.read_conn_info`` in the BT executor
        └── on a fresh int → invoke the host-supplied ``on_rssi_update`` callback

These tests pin every short-circuit so the refresh can never:
- race a pair/scan/reconnect that's already holding the BT lock,
- emit a stale callback when the link dropped between ticks,
- propagate a None into the UI as a number, or
- attempt an mgmt-socket read against a controller index we never resolved.

The shared lock is reset autouse — these tests share global state with
the rest of the suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services import bt_operation_lock


@pytest.fixture(autouse=True)
def _reset_bt_operation_lock():
    """Defensive: ensure the singleton lock starts free in every test
    (other tests may have leaked it under failure)."""
    while True:
        try:
            bt_operation_lock._bt_operation_lock.release()
        except RuntimeError:
            break
    yield
    while True:
        try:
            bt_operation_lock._bt_operation_lock.release()
        except RuntimeError:
            break


def _make_manager(adapter_hci_name: str = "hci0"):
    from bluetooth_manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="TestSpeaker",
        )
    # Force the resolved adapter name regardless of what sysfs returned in CI.
    mgr.adapter_hci_name = adapter_hci_name
    return mgr


# ── _resolve_adapter_index ──────────────────────────────────────────


def test_resolve_adapter_index_parses_hciN_to_int():
    """``hci0`` → 0, ``hci3`` → 3.  This is the controller index the
    kernel mgmt socket addresses in opcode 0x0031 — get it wrong and
    we read RSSI from the wrong controller (or get ENODEV)."""
    mgr = _make_manager(adapter_hci_name="hci3")

    assert mgr._resolve_adapter_index() == 3


def test_resolve_adapter_index_returns_minus_one_when_unresolved():
    """Empty ``adapter_hci_name`` happens when sysfs lookup failed at
    startup (LXC without /sys/class/bluetooth, mid-recovery, etc.).
    Returning -1 lets ``read_conn_info`` short-circuit instead of
    asking btsocket to address controller 0xFFFF."""
    mgr = _make_manager(adapter_hci_name="")

    assert mgr._resolve_adapter_index() == -1


def test_resolve_adapter_index_returns_minus_one_for_garbage_name():
    """Defensive: ``adapter_hci_name`` is sourced from sysfs which has
    historically been bypassed by the bluetoothctl-list fallback;
    anything that isn't ``hci<digits>`` must not crash the tick."""
    mgr = _make_manager(adapter_hci_name="bogus")

    assert mgr._resolve_adapter_index() == -1


# ── _rssi_refresh_tick ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tick_writes_rssi_via_callback_on_success():
    """Happy path: connected + lock free + mgmt returns -50 → the
    host-supplied callback fires once with that value."""
    mgr = _make_manager()
    mgr.connected = True
    cb = MagicMock()
    mgr.on_rssi_update = cb

    with patch("services.bt_rssi_mgmt.read_conn_info", return_value=-50) as read:
        await mgr._rssi_refresh_tick()

    read.assert_called_once_with(0, "AA:BB:CC:DD:EE:FF")
    cb.assert_called_once_with(-50)


@pytest.mark.asyncio
async def test_tick_skips_read_when_not_connected():
    """No connected ACL → no link to read RSSI from.  Skip the syscall
    entirely so we don't waste a mgmt round-trip every 30 s for a
    speaker that's been off all day."""
    mgr = _make_manager()
    mgr.connected = False
    cb = MagicMock()
    mgr.on_rssi_update = cb

    with patch("services.bt_rssi_mgmt.read_conn_info") as read:
        await mgr._rssi_refresh_tick()

    read.assert_not_called()
    cb.assert_not_called()


@pytest.mark.asyncio
async def test_tick_skips_when_bt_operation_lock_busy():
    """If pair / scan / reconnect is currently holding the lock, do
    NOT issue a mgmt read concurrently — bluetoothd serialises mgmt
    commands per controller and a long-running pair must not be
    starved by an RSSI refresh.  This is the exact pattern that
    motivated promoting ``bt_operation_lock`` to a shared singleton."""
    mgr = _make_manager()
    mgr.connected = True
    cb = MagicMock()
    mgr.on_rssi_update = cb

    assert bt_operation_lock.try_acquire_bt_operation()  # simulate pair in flight

    with patch("services.bt_rssi_mgmt.read_conn_info") as read:
        await mgr._rssi_refresh_tick()

    read.assert_not_called()
    cb.assert_not_called()


@pytest.mark.asyncio
async def test_tick_does_not_invoke_callback_when_read_returns_none():
    """``None`` from the wrapper means "no fresh value, keep last
    known"; firing the callback with None would clobber the displayed
    RSSI to "—" every time the speaker briefly stuttered."""
    mgr = _make_manager()
    mgr.connected = True
    cb = MagicMock()
    mgr.on_rssi_update = cb

    with patch("services.bt_rssi_mgmt.read_conn_info", return_value=None):
        await mgr._rssi_refresh_tick()

    cb.assert_not_called()


@pytest.mark.asyncio
async def test_tick_releases_lock_even_when_read_raises():
    """Defensive: ``read_conn_info`` is documented to never raise but
    if a future refactor breaks that promise, the lock must still be
    released or the next pair attempt deadlocks the BT stack."""
    mgr = _make_manager()
    mgr.connected = True
    mgr.on_rssi_update = MagicMock()

    with patch("services.bt_rssi_mgmt.read_conn_info", side_effect=RuntimeError("boom")):
        await mgr._rssi_refresh_tick()

    # Lock is free again — verify by re-acquiring without blocking.
    assert bt_operation_lock.try_acquire_bt_operation()


@pytest.mark.asyncio
async def test_tick_swallows_callback_exceptions():
    """The callback runs on the asyncio loop thread; an unhandled
    exception there would tear down the entire refresh task and we'd
    lose the periodic refresh until next process restart.  Log and
    continue."""
    mgr = _make_manager()
    mgr.connected = True
    mgr.on_rssi_update = MagicMock(side_effect=ValueError("ui state corrupt"))

    with patch("services.bt_rssi_mgmt.read_conn_info", return_value=-60):
        # Must not raise.
        await mgr._rssi_refresh_tick()
