"""The manager executes reconnect decisions; it no longer makes them.

Backoff, churn and the release ladder are the policy's arithmetic.  The
manager's job is the part the policy deliberately does not do: status
updates, event publication and persistence.  These tests drive the manager
with a policy on a fake clock — impossible while the counters and the
timestamps lived on the manager itself.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.manager import BluetoothManager
from sendspin_bridge.bluetooth.reconnect_policy import ReconnectPolicy

MAC = "AA:BB:CC:DD:EE:FF"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Host:
    def __init__(self):
        self.status: dict = {}
        self.bt_management_enabled = True

    def get_status_value(self, key, default=None):
        return self.status.get(key, default)

    def update_status(self, updates):
        self.status.update(updates)


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)


@pytest.fixture()
def clock():
    return _Clock()


@pytest.fixture()
def bt_manager(installed_bluez, clock):
    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(
        mac_address=MAC,
        device_name="TestSpeaker",
        check_interval=10,
        max_reconnect_fails=3,
        churn_threshold=3,
        churn_window=300.0,
    )
    mgr.host = _Host()
    mgr.policy = ReconnectPolicy(
        check_interval=10,
        max_fails=3,
        churn_threshold=3,
        churn_window=300.0,
        clock=clock,
    )
    return mgr


def test_the_manager_exposes_its_policy(installed_bluez):
    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address=MAC, device_name="TestSpeaker", check_interval=7)

    assert isinstance(mgr.policy, ReconnectPolicy)
    assert mgr.policy.check_interval == 7


def test_backoff_comes_from_the_policy(bt_manager):
    assert bt_manager._reconnect_delay(4) == bt_manager.policy.delay_for(4)


def test_recorded_reconnects_land_in_the_policy(bt_manager, clock):
    bt_manager._record_reconnect()
    bt_manager._record_reconnect()

    assert bt_manager.policy.reconnect_count() == 2

    clock.advance(301)
    assert bt_manager.policy.reconnect_count() == 0


def test_churn_release_updates_status_and_stops_the_ladder(bt_manager):
    for _ in range(3):
        bt_manager._record_reconnect()

    released = bt_manager._handle_reconnect_failure(1)

    assert released is True
    assert bt_manager.management_enabled is False
    assert bt_manager.host.status["bt_released_by"] == "auto"
    assert bt_manager.host.bt_management_enabled is False


def test_failures_below_the_threshold_do_not_release(bt_manager):
    assert bt_manager._handle_reconnect_failure(2) is False
    assert bt_manager.management_enabled is True


def test_reclaim_waits_out_the_policy_quiet_period(bt_manager, clock):
    bt_manager.paired = True
    bt_manager._has_ever_paired_since_start = True

    assert bt_manager._handle_reconnect_failure(3) is True
    assert bt_manager.management_enabled is False
    bt_manager.host.status["bt_released_by"] = "auto"

    assert bt_manager.maybe_auto_reclaim(connected=True) is False

    clock.advance(61)
    assert bt_manager.maybe_auto_reclaim(connected=True) is True
    assert bt_manager.management_enabled is True
    assert bt_manager.host.status["bt_released_by"] is None


def test_reclaim_starts_the_churn_window_fresh(bt_manager, clock):
    bt_manager.paired = True
    bt_manager._has_ever_paired_since_start = True
    for _ in range(2):
        bt_manager._record_reconnect()
    bt_manager._handle_reconnect_failure(3)
    bt_manager.host.status["bt_released_by"] = "auto"
    clock.advance(61)

    assert bt_manager.maybe_auto_reclaim(connected=True) is True
    assert bt_manager.policy.reconnect_count() == 0


def test_an_unknown_link_state_never_reclaims(bt_manager, clock):
    bt_manager.paired = True
    bt_manager._has_ever_paired_since_start = True
    bt_manager._handle_reconnect_failure(3)
    bt_manager.host.status["bt_released_by"] = "auto"
    clock.advance(61)

    assert bt_manager.maybe_auto_reclaim(connected=None) is False
    assert bt_manager.management_enabled is False
