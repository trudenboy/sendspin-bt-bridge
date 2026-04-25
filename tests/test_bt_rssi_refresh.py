"""Tests for the periodic RSSI background refresh in BluetoothManager (rc.3).

Connected device cards need a fresh RSSI value to render the dBm
badge.  rc.2 wired the data path (``DeviceStatus.rssi_dbm`` /
``rssi_at_ts``) and the scan-time extraction; rc.3 adds the actual
refresh loop — every 60 s the BluetoothManager runs a brief
``bluetoothctl scan bredr``-style burst, parses any ``[CHG] Device <mac>
RSSI: <dB>`` lines for its own MAC, and pushes the value into the host
status dict.

The loop yields to user-triggered scans (``services.async_job_state.
is_scan_running()``) so the two paths don't fight over the BlueZ
discovery state machine.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from bluetooth_manager import BluetoothManager, _parse_own_rssi_from_burst

# ── Pure-function parser ─────────────────────────────────────────────────


def test_parse_own_rssi_extracts_value_for_target_mac():
    """Parser picks the most recent ``[CHG] Device <MAC> RSSI: <dB>``
    line for the requested MAC, ignoring lines for other devices."""
    stdout = (
        "[CHG] Device 11:22:33:44:55:66 RSSI: -90\n"
        "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -55\n"
        "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -42\n"
    )
    assert _parse_own_rssi_from_burst(stdout, "AA:BB:CC:DD:EE:FF") == -42


def test_parse_own_rssi_handles_parenthesised_hex_form():
    """Older bluetoothctl emits ``RSSI: 0xff... (-43)``; parser must
    pull the parenthesised decimal."""
    stdout = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: 0xffffffd5 (-43)\n"
    assert _parse_own_rssi_from_burst(stdout, "AA:BB:CC:DD:EE:FF") == -43


def test_parse_own_rssi_returns_none_when_mac_not_seen():
    """Burst window may not include any RSSI event for our MAC if the
    speaker advertises slowly — returning ``None`` lets the caller
    leave the cached ``rssi_at_ts`` untouched (so the UI eventually
    flips to grey via the staleness check)."""
    stdout = "[CHG] Device 11:22:33:44:55:66 RSSI: -55\n"
    assert _parse_own_rssi_from_burst(stdout, "AA:BB:CC:DD:EE:FF") is None


def test_parse_own_rssi_is_mac_case_insensitive():
    stdout = "[CHG] Device aa:bb:cc:dd:ee:ff RSSI: -71\n"
    assert _parse_own_rssi_from_burst(stdout, "AA:BB:CC:DD:EE:FF") == -71


def test_parse_own_rssi_handles_empty_input():
    assert _parse_own_rssi_from_burst("", "AA:BB:CC:DD:EE:FF") is None


# ── BluetoothManager.run_rssi_refresh ────────────────────────────────────


@pytest.fixture
def bt_manager():
    with patch("subprocess.check_output", return_value=""):
        return BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", device_name="TestSpeaker")


def test_run_rssi_refresh_pushes_value_to_host_status(bt_manager):
    """A successful refresh must push ``rssi_dbm`` + ``rssi_at_ts``
    onto the host's status dict (the same dict surfaced via
    ``GET /api/status``)."""
    host = MagicMock()
    bt_manager.host = host

    burst_out = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -58\n"
    with patch.object(bt_manager, "_run_rssi_burst", return_value=burst_out):
        bt_manager.run_rssi_refresh()

    host.update_status.assert_called_once()
    update = host.update_status.call_args.args[0]
    assert update["rssi_dbm"] == -58
    assert isinstance(update["rssi_at_ts"], float)
    assert update["rssi_at_ts"] >= time.time() - 5


def test_run_rssi_refresh_no_value_does_not_touch_status(bt_manager):
    """If the burst window saw no RSSI event for our MAC, leave the
    cached value alone — the UI's stale check will eventually flip to
    grey, but transient burst gaps shouldn't.  Also avoids spamming
    the SSE / WS notify path with no-op updates."""
    host = MagicMock()
    bt_manager.host = host

    with patch.object(bt_manager, "_run_rssi_burst", return_value=""):
        bt_manager.run_rssi_refresh()

    host.update_status.assert_not_called()


def test_run_rssi_refresh_skips_when_user_scan_active(bt_manager):
    """User-triggered scans (``POST /api/bt/scan``) own the BlueZ
    discovery state machine for their duration.  The background
    refresh must skip its tick rather than fight them, otherwise we'd
    cancel the user scan mid-flight or get garbled output."""
    host = MagicMock()
    bt_manager.host = host

    with (
        patch("bluetooth_manager.is_scan_running", return_value=True),
        patch.object(bt_manager, "_run_rssi_burst") as run_burst,
    ):
        bt_manager.run_rssi_refresh()

    run_burst.assert_not_called()
    host.update_status.assert_not_called()


def test_run_rssi_refresh_swallows_burst_failure(bt_manager):
    """A failing burst must not propagate — the loop runs every 60 s,
    one bad tick should just be retried later."""
    host = MagicMock()
    bt_manager.host = host

    with patch.object(bt_manager, "_run_rssi_burst", side_effect=RuntimeError("boom")):
        # Must not raise.
        bt_manager.run_rssi_refresh()

    host.update_status.assert_not_called()


def test_run_rssi_refresh_skips_when_bt_operation_lock_held(bt_manager):
    """Regression for Copilot review on PR #197: the background RSSI
    burst must yield to the shared bluetoothctl operation lock, not
    just user scan jobs.  Pair / Reset & Reconnect / Standalone Pair
    all hold the lock for the duration of their (long) bluetoothctl
    sessions; a parallel RSSI burst would corrupt their stdout / flip
    BlueZ into an inconsistent state.
    """
    from services.bt_operation_lock import release_bt_operation, try_acquire_bt_operation

    host = MagicMock()
    bt_manager.host = host

    assert try_acquire_bt_operation() is True
    try:
        with patch.object(bt_manager, "_run_rssi_burst") as run_burst:
            bt_manager.run_rssi_refresh()
        run_burst.assert_not_called()
        host.update_status.assert_not_called()
    finally:
        release_bt_operation()


def test_run_rssi_refresh_releases_bt_operation_lock_after_burst(bt_manager):
    """After a successful (or failed) refresh the shared lock must be
    released — otherwise a single RSSI tick could permanently block all
    subsequent pair / reconnect attempts on the bridge."""
    from services.bt_operation_lock import try_acquire_bt_operation

    host = MagicMock()
    bt_manager.host = host

    burst_out = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -60\n"
    with patch.object(bt_manager, "_run_rssi_burst", return_value=burst_out):
        bt_manager.run_rssi_refresh()

    # Lock must be free now.
    assert try_acquire_bt_operation() is True
    from services.bt_operation_lock import release_bt_operation

    release_bt_operation()
