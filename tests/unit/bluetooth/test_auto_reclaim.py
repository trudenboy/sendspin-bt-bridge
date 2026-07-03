"""Auto-reclaim after auto-release (issues #349/#350).

When a device is auto-released ("N consecutive failed reconnects" or BT
churn), the speaker later re-establishing the link on its own is a safe
signal to reclaim management: a device stuck in a reconnect loop never
presents a stable Connected=yes.  Manual (operator) releases must stay
released.  A quiet period after the release keeps a churn-released
speaker from flapping management on/off while it is still bouncing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture()
def released_manager():
    """A manager in the auto-released state with a connected speaker."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", device_name="TestSpeaker")
    mgr.management_enabled = False
    mgr.connected = True
    mgr._auto_released_at = 0.0  # released long ago (monotonic origin)
    mgr.host = MagicMock()
    mgr.host.bt_management_enabled = False
    mgr.host.get_status_value = lambda key: {"bt_released_by": "auto"}.get(key)
    return mgr


def test_reclaims_auto_released_device_on_external_connect(released_manager):
    mgr = released_manager
    with (
        patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=1000.0),
        patch("sendspin_bridge.services.bluetooth.persist_device_released") as persist_released,
    ):
        assert mgr.maybe_auto_reclaim() is True

    assert mgr.management_enabled is True
    assert mgr.host.bt_management_enabled is True
    status_update = mgr.host.update_status.call_args[0][0]
    assert status_update["bt_management_enabled"] is True
    assert status_update["bt_released_by"] is None
    persist_released.assert_called_once_with("TestSpeaker", False)


def test_user_release_is_never_auto_reclaimed(released_manager):
    mgr = released_manager
    mgr.host.get_status_value = lambda key: {"bt_released_by": "user"}.get(key)
    with patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=1000.0):
        assert mgr.maybe_auto_reclaim() is False
    assert mgr.management_enabled is False


def test_no_reclaim_while_disconnected(released_manager):
    mgr = released_manager
    mgr.connected = False
    with patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=1000.0):
        assert mgr.maybe_auto_reclaim() is False
    assert mgr.management_enabled is False


def test_explicit_connected_argument_overrides_cached_state(released_manager):
    mgr = released_manager
    mgr.connected = False  # polling monitor passes live poll results instead
    with (
        patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=1000.0),
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
    ):
        assert mgr.maybe_auto_reclaim(connected=True) is True


def test_quiet_period_damps_churn_flapping(released_manager):
    mgr = released_manager
    mgr._auto_released_at = 990.0  # released 10 s ago
    with patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=1000.0):
        assert mgr.maybe_auto_reclaim() is False
    assert mgr.management_enabled is False

    # …but reclaim works once the quiet period has elapsed.
    with (
        patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=1100.0),
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
    ):
        assert mgr.maybe_auto_reclaim() is True


def test_reclaim_resets_churn_window(released_manager):
    mgr = released_manager
    mgr._reconnect_timestamps = [1.0, 2.0, 3.0]
    with (
        patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=1000.0),
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
    ):
        assert mgr.maybe_auto_reclaim() is True
    assert mgr._reconnect_timestamps == []


def test_no_reclaim_when_management_already_enabled(released_manager):
    mgr = released_manager
    mgr.management_enabled = True
    assert mgr.maybe_auto_reclaim() is False


def test_auto_release_stamps_quiet_period_origin(released_manager):
    """Both auto-release paths must stamp _auto_released_at and persist
    released_by="auto" so the reclaim gate can distinguish them from a
    manual release across the round-trip."""
    mgr = released_manager
    mgr.management_enabled = True
    mgr._auto_released_at = None
    mgr.max_reconnect_fails = 2
    mgr.paired = True
    mgr._has_ever_paired_since_start = True
    with (
        patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=555.0),
        patch("sendspin_bridge.services.bluetooth.persist_device_released") as persist_released,
    ):
        assert mgr._handle_reconnect_failure(3) is True
    assert mgr._auto_released_at == 555.0
    persist_released.assert_called_once_with("TestSpeaker", True, released_by="auto")


def test_churn_release_stamps_quiet_period_origin(released_manager):
    mgr = released_manager
    mgr.management_enabled = True
    mgr._auto_released_at = None
    mgr._CHURN_THRESHOLD = 2
    mgr._CHURN_WINDOW = 30
    mgr._reconnect_timestamps = [90.0, 99.0]
    with (
        patch("sendspin_bridge.bluetooth.manager.time.monotonic", return_value=100.0),
        patch("sendspin_bridge.services.bluetooth.persist_device_released") as persist_released,
    ):
        assert mgr._check_reconnect_churn() is True
    assert mgr._auto_released_at == 100.0
    persist_released.assert_called_once_with("TestSpeaker", True, released_by="auto")
