"""Tests for the auto-disable path on never-paired devices (#263).

When a configured device has accumulated BT_MAX_RECONNECT_FAILS failed
reconnect attempts AND BlueZ has no record of it (paired is None,
_has_ever_paired_since_start is False), the manager flips `enabled=False`
to stop the reconnect storm. The flip is persisted via the existing
`persist_device_enabled` helper so it survives a bridge restart AND is
mirrored to options.json for HA addons.
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
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    posted_updates: list[dict] = []

    def _update_status(payload):
        posted_updates.append(dict(payload))

    host = SimpleNamespace(
        update_status=_update_status,
        bluetooth_sink_name=None,
        bt_management_enabled=True,
        enabled=True,
    )

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="Kitchen",
            enable_a2dp_dance=False,
            enable_pa_module_reload=False,
            max_reconnect_fails=5,
        )
    mgr.host = host
    return mgr, posted_updates


def test_auto_disable_triggers_when_never_paired_and_threshold_reached(
    bt_manager_with_host,
):
    """5/5 attempts on a never-paired device flips enabled=False + persists."""
    mgr, posted = bt_manager_with_host
    mgr.paired = None
    mgr._has_ever_paired_since_start = False

    with patch(
        "sendspin_bridge.services.bluetooth.persist_device_enabled",
    ) as persist_mock:
        released = mgr._handle_reconnect_failure(5)

    assert released is True, "auto-disable path must return True so the monitor stops"
    persist_mock.assert_called_once_with("Kitchen", False)

    enabled_updates = [u for u in posted if "enabled" in u]
    assert enabled_updates, f"expected an update_status with enabled key, got: {posted}"
    last = enabled_updates[-1]
    assert last["enabled"] is False
    assert "never been paired" in (last.get("last_error") or "").lower()
    assert last.get("reconnecting") is False
    # #263 Copilot follow-up: management_enabled must flip to False so the
    # polling/D-Bus monitor loops actually stop ticking. Without this the
    # auto-disable warning would re-fire every check_interval.
    assert last.get("bt_management_enabled") is False
    assert mgr.management_enabled is False


def test_auto_disable_does_not_trigger_for_previously_paired_device(bt_manager_with_host):
    """A device that connected successfully earlier this session must NOT be
    auto-disabled — that's the auto-release path's job."""
    mgr, posted = bt_manager_with_host
    mgr.paired = None
    mgr._has_ever_paired_since_start = True  # connected at least once

    with (
        patch("sendspin_bridge.services.bluetooth.persist_device_enabled") as persist_mock,
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
    ):
        mgr._handle_reconnect_failure(5)

    persist_mock.assert_not_called()
    # No enabled=False update emitted
    assert not any(u.get("enabled") is False for u in posted), (
        f"expected no auto-disable when previously paired, got: {posted}"
    )


def test_auto_disable_does_not_trigger_when_paired_is_true(bt_manager_with_host):
    """Devices BlueZ knows about (paired=True) take the regular auto-release
    path, not the never-paired auto-disable."""
    mgr, posted = bt_manager_with_host
    mgr.paired = True
    mgr._has_ever_paired_since_start = False  # edge: BlueZ knows but bridge hasn't seen Connected yet

    with (
        patch("sendspin_bridge.services.bluetooth.persist_device_enabled") as persist_mock,
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
    ):
        mgr._handle_reconnect_failure(5)

    persist_mock.assert_not_called()
    assert not any(u.get("enabled") is False for u in posted)


def test_auto_disable_threshold_respects_config_value(bt_manager_with_host):
    """attempt < max_reconnect_fails must not fire auto-disable."""
    mgr, _ = bt_manager_with_host
    mgr.paired = None
    mgr._has_ever_paired_since_start = False
    mgr.max_reconnect_fails = 5

    with patch("sendspin_bridge.services.bluetooth.persist_device_enabled") as persist_mock:
        released = mgr._handle_reconnect_failure(4)  # below threshold

    persist_mock.assert_not_called()
    assert released is False


def test_auto_disable_skipped_when_max_reconnect_fails_zero(bt_manager_with_host):
    """Opt-out: setting max_reconnect_fails=0 disables the auto-disable path
    (and the auto-release path)."""
    mgr, _ = bt_manager_with_host
    mgr.paired = None
    mgr._has_ever_paired_since_start = False
    mgr.max_reconnect_fails = 0

    with patch("sendspin_bridge.services.bluetooth.persist_device_enabled") as persist_mock:
        released = mgr._handle_reconnect_failure(100)

    persist_mock.assert_not_called()
    assert released is False
