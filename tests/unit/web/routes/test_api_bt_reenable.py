"""Tests for the re-enable side-effect on never-paired auto-disabled devices.

The /api/device/enabled POST handler clears in-session state via
``_clear_never_paired_state_on_reenable`` when an operator flips a device
back to enabled. The state to clear:

- DeviceStatus.never_paired / never_paired_since
- DeviceStatus.reconnect_attempt
- DeviceStatus.last_error / last_error_at
- BluetoothManager._has_ever_paired_since_start (next session gate)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from sendspin_bridge.web.routes import api_bt


class _FakeClient:
    """Minimal SendspinClient stand-in capturing update_status payloads."""

    def __init__(self):
        self.posted: list[dict] = []
        self.bt_manager = SimpleNamespace(_has_ever_paired_since_start=True)

    def update_status(self, payload: dict) -> None:
        self.posted.append(dict(payload))


def test_clear_never_paired_state_resets_status_and_manager_flag():
    """Re-enable from the recovery card must wipe the never_paired evidence
    so the next reconnect cycle is treated as fresh (#263)."""
    client = _FakeClient()
    with patch.object(api_bt, "get_client_or_error", return_value=(client, None)):
        api_bt._clear_never_paired_state_on_reenable("Kitchen")

    assert client.posted, "expected an update_status call"
    payload = client.posted[-1]
    assert payload["never_paired"] is False
    assert payload["never_paired_since"] is None
    assert payload["reconnect_attempt"] == 0
    assert payload["last_error"] is None
    assert client.bt_manager._has_ever_paired_since_start is False


def test_clear_never_paired_state_no_op_when_client_missing():
    """If the SendspinClient was torn down between auto-disable and
    re-enable, the helper must be a silent no-op — the next bridge start
    will pick up the new enabled=true value cleanly."""
    with patch.object(api_bt, "get_client_or_error", return_value=(None, None)):
        # Must not raise
        api_bt._clear_never_paired_state_on_reenable("Kitchen")


def test_clear_never_paired_state_tolerates_missing_bt_manager():
    """Some test fixtures have a client without a bt_manager attribute;
    the helper must still clear DeviceStatus fields and not crash."""
    client = _FakeClient()
    client.bt_manager = None
    with patch.object(api_bt, "get_client_or_error", return_value=(client, None)):
        api_bt._clear_never_paired_state_on_reenable("Kitchen")
    assert client.posted, "DeviceStatus clearing must still happen"
    assert client.posted[-1]["never_paired"] is False
