"""Tests for BluetoothManager `never_paired` signal (#260, #263).

The flag is the shared backend signal that drives the recovery banner
branch, the Start pairing device-card button, the bug-report classifier,
and the auto-disable threshold. It MUST be set by ``_purge_stale_bluez_entry``
(after _PAIRED_UNKNOWN_THRESHOLD consecutive observations of paired==None)
and cleared on a successful connect transition.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture()
def bt_manager_with_host():
    """BluetoothManager wired to a host that captures every ``update_status``."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    posted_updates: list[dict] = []

    def _update_status(payload):
        posted_updates.append(dict(payload))

    host = SimpleNamespace(
        update_status=_update_status,
        bluetooth_sink_name=None,
        bt_management_enabled=True,
    )

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="TestSpeaker",
            enable_a2dp_dance=False,
            enable_pa_module_reload=False,
        )
    mgr.host = host
    return mgr, posted_updates


def test_manager_starts_with_has_ever_paired_false(bt_manager_with_host):
    """A freshly-constructed manager has never observed a pair in this session."""
    mgr, _ = bt_manager_with_host
    assert mgr._has_ever_paired_since_start is False


def test_purge_stale_bluez_entry_sets_never_paired(bt_manager_with_host):
    """When BlueZ has no record after the threshold of unknown observations,
    the purge path must surface `never_paired=True` to the host status so the
    recovery banner can switch from "is disconnected" to "has never been paired"
    (#260)."""
    mgr, posted = bt_manager_with_host
    # Stub subprocess so the bluetoothctl cleanup is a no-op without raising
    with patch("subprocess.run") as run_mock:
        run_mock.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        mgr._purge_stale_bluez_entry()

    # The host.update_status payload should now carry never_paired=True
    # alongside the existing last_error / last_error_at fields.
    never_paired_updates = [u for u in posted if "never_paired" in u]
    assert never_paired_updates, f"Expected an update_status with never_paired key, got: {posted}"
    last = never_paired_updates[-1]
    assert last["never_paired"] is True
    assert isinstance(last.get("never_paired_since"), str) and last["never_paired_since"]
    # The existing last_error remediation message must still be there so
    # downstream consumers (bug-report classifier) can match the substring.
    assert "BlueZ has no record" in last.get("last_error", "")


def test_apply_connected_state_true_clears_never_paired(bt_manager_with_host):
    """A successful connect transition must flip _has_ever_paired_since_start
    and push never_paired=False so the banner returns to normal once the
    speaker comes back online (#260)."""
    mgr, posted = bt_manager_with_host
    # Seed the "previously purged" state so we can observe the clearing
    mgr._has_ever_paired_since_start = False
    # Trigger False -> True transition (idempotency guard otherwise no-ops)
    mgr._apply_connected_state(False)
    posted.clear()
    mgr._apply_connected_state(True)

    assert mgr._has_ever_paired_since_start is True
    clearing_updates = [u for u in posted if "never_paired" in u]
    assert clearing_updates, f"Expected update_status({{'never_paired': False}}), got: {posted}"
    assert clearing_updates[-1]["never_paired"] is False
    assert clearing_updates[-1].get("never_paired_since") is None


def test_apply_connected_state_idempotent_does_not_emit_clear(bt_manager_with_host):
    """No-op transitions must not flood the SSE stream with redundant
    never_paired=False updates. Regression guard."""
    mgr, posted = bt_manager_with_host
    mgr._apply_connected_state(True)  # False -> True (real transition)
    posted.clear()
    mgr._apply_connected_state(True)  # True -> True (idempotent)

    assert posted == [], f"Idempotent transition should not emit updates, got: {posted}"
