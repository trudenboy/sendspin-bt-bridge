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
    ``GET /api/status``).

    v2.63.0-rc.5: refresh now reads RSSI from ``bluetoothctl info``
    instead of ``scan bredr``.  Already-connected BR/EDR peers stop
    advertising so the scan window never produces RSSI events for
    them; ``info <MAC>`` returns the live RSSI of the active link
    instead.
    """
    host = MagicMock()
    bt_manager.host = host

    info_out = "Name: ENEBY20\nConnected: yes\nRSSI: -58\n"
    with patch.object(bt_manager, "_run_bluetoothctl", return_value=(True, info_out)):
        bt_manager.run_rssi_refresh()

    host.update_status.assert_called_once()
    update = host.update_status.call_args.args[0]
    assert update["rssi_dbm"] == -58
    assert isinstance(update["rssi_at_ts"], float)
    assert update["rssi_at_ts"] >= time.time() - 5


def test_run_rssi_refresh_no_value_does_not_touch_status(bt_manager):
    """If ``bluetoothctl info`` returns no RSSI line (some adapters /
    BlueZ versions omit it for slow links), leave the cached value
    alone so the UI's 90 s stale check governs the fade to grey
    rather than sporadic gaps."""
    host = MagicMock()
    bt_manager.host = host

    with patch.object(bt_manager, "_run_bluetoothctl", return_value=(True, "Name: x\nConnected: yes\n")):
        bt_manager.run_rssi_refresh()

    host.update_status.assert_not_called()


def test_run_rssi_refresh_skips_when_user_scan_active(bt_manager):
    """User-triggered scans (``POST /api/bt/scan``) own the BlueZ
    discovery state machine for their duration.  The background
    refresh must skip its tick rather than fight them."""
    host = MagicMock()
    bt_manager.host = host

    with (
        patch("bluetooth_manager.is_scan_running", return_value=True),
        patch.object(bt_manager, "_run_bluetoothctl") as run_bt,
    ):
        bt_manager.run_rssi_refresh()

    run_bt.assert_not_called()
    host.update_status.assert_not_called()


def test_run_rssi_refresh_swallows_subprocess_failure(bt_manager):
    """A failing bluetoothctl call must not propagate — the loop
    runs every 60 s, one bad tick should just be retried later."""
    host = MagicMock()
    bt_manager.host = host

    with patch.object(bt_manager, "_run_bluetoothctl", side_effect=RuntimeError("boom")):
        # Must not raise.
        bt_manager.run_rssi_refresh()

    host.update_status.assert_not_called()


def test_run_rssi_refresh_calls_bluetoothctl_info_with_target_mac(bt_manager):
    """Regression for v2.63.0-rc.5: the refresh must specifically run
    ``bluetoothctl info <MAC>`` for the manager's MAC, not the legacy
    ``scan bredr`` path that yields no events for connected peers."""
    host = MagicMock()
    bt_manager.host = host

    with patch.object(bt_manager, "_run_bluetoothctl", return_value=(True, "RSSI: -42\n")) as run_bt:
        bt_manager.run_rssi_refresh()

    # The first call in the chain must be ``info <MAC>``.  We accept
    # either ["info AA:..."] or a list whose last command starts with "info".
    call_cmds = run_bt.call_args.args[0]
    joined = " ".join(call_cmds) if isinstance(call_cmds, list) else str(call_cmds)
    assert "info AA:BB:CC:DD:EE:FF" in joined or "info aa:bb:cc:dd:ee:ff" in joined.lower()


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
        with patch.object(bt_manager, "_run_bluetoothctl") as run_bt:
            bt_manager.run_rssi_refresh()
        run_bt.assert_not_called()
        host.update_status.assert_not_called()
    finally:
        release_bt_operation()


def test_run_rssi_refresh_releases_bt_operation_lock_after_call(bt_manager):
    """After a successful (or failed) refresh the shared lock must be
    released — otherwise a single RSSI tick could permanently block all
    subsequent pair / reconnect attempts on the bridge."""
    from services.bt_operation_lock import try_acquire_bt_operation

    host = MagicMock()
    bt_manager.host = host

    info_out = "Connected: yes\nRSSI: -60\n"
    with patch.object(bt_manager, "_run_bluetoothctl", return_value=(True, info_out)):
        bt_manager.run_rssi_refresh()

    # Lock must be free now.
    assert try_acquire_bt_operation() is True
    from services.bt_operation_lock import release_bt_operation

    release_bt_operation()
