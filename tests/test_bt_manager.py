"""Tests for bluetooth_manager.py — BluetoothManager class.

bluetooth_manager.py imports services.pulse (which gracefully handles missing
pulsectl_asyncio) and only imports ``dbus`` inside function bodies.  No
module-level sys.modules stubbing is needed for Python 3.9 compatibility.
"""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture()
def bt_manager():
    """Create a BluetoothManager with reasonable defaults for testing."""
    from bluetooth_manager import BluetoothManager

    # Mock subprocess calls that happen in __init__ (adapter resolution)
    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="TestSpeaker",
        )
    return mgr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bt_executor_pool_size():
    """The module-level thread pool must have at least 4 workers."""
    from bluetooth_manager import _bt_executor

    assert _bt_executor._max_workers >= 4


def test_running_flag_default(bt_manager):
    """BluetoothManager instances must start with _running = True."""
    assert bt_manager._running is True


def test_shutdown_sets_running_false(bt_manager):
    """shutdown() must set _running to False."""
    bt_manager.shutdown()
    assert bt_manager._running is False


def test_configure_bluetooth_audio_pipewire_pattern(bt_manager):
    """Finds a PipeWire-format sink (bluez_output.MAC.1)."""
    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_output.{pa_mac}.1"

    fake_sinks = [{"name": sink_name, "description": "BT Speaker"}]
    with (
        patch("bluetooth_manager.list_sinks", return_value=fake_sinks),
        patch("bluetooth_manager.get_sink_volume", return_value=50),
        patch("bluetooth_manager.set_sink_mute", return_value=True),
        patch("bluetooth_manager.set_sink_volume", return_value=True),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True


def test_configure_bluetooth_audio_pulseaudio_pattern(bt_manager):
    """Finds a PulseAudio-format sink (bluez_sink.MAC.a2dp_sink)."""
    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_sink.{pa_mac}.a2dp_sink"

    fake_sinks = [{"name": sink_name, "description": "BT Speaker"}]
    with (
        patch("bluetooth_manager.list_sinks", return_value=fake_sinks),
        patch("bluetooth_manager.get_sink_volume", return_value=50),
        patch("bluetooth_manager.set_sink_mute", return_value=True),
        patch("bluetooth_manager.set_sink_volume", return_value=True),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True


def test_configure_bluetooth_audio_no_sink(bt_manager):
    """Returns False when no matching sink is found."""
    with (
        patch("bluetooth_manager.list_sinks", return_value=[]),
        patch("bluetooth_manager.get_sink_volume", return_value=None),
        patch("bluetooth_manager.set_sink_mute", return_value=True),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is False


def test_connected_default(bt_manager):
    """BluetoothManager instances must start with connected = False."""
    assert bt_manager.connected is False


def test_device_name_fallback():
    """When no device_name is given, it falls back to the MAC address."""
    from bluetooth_manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(mac_address="11:22:33:44:55:66")

    assert mgr.device_name == "11:22:33:44:55:66"


def test_unresolved_adapter_disables_dbus_path():
    """When adapter resolution fails, D-Bus path should remain unavailable."""
    from bluetooth_manager import BluetoothManager

    with (
        patch.object(BluetoothManager, "_detect_default_adapter_mac", return_value=""),
        patch("subprocess.check_output", return_value=""),
    ):
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", device_name="TestSpeaker")

    assert mgr.adapter_hci_name == ""
    assert mgr._dbus_device_path is None


@pytest.mark.asyncio
async def test_monitor_dbus_raises_when_device_path_unavailable(bt_manager):
    bt_manager._dbus_device_path = None

    with pytest.raises(RuntimeError, match="adapter resolution failed"):
        await bt_manager._monitor_dbus(None, None)


def test_record_reconnect_prunes_old_entries(bt_manager):
    """Only reconnects inside the churn window should be retained."""
    bt_manager._CHURN_WINDOW = 10
    with patch("bluetooth_manager.time.monotonic", side_effect=[100.0, 111.0]):
        bt_manager._record_reconnect()
        bt_manager._record_reconnect()

    assert bt_manager._reconnect_timestamps == [111.0]


def test_check_reconnect_churn_disables_management(bt_manager):
    """Churn threshold should auto-disable management and update client status."""
    bt_manager._CHURN_THRESHOLD = 2
    bt_manager._CHURN_WINDOW = 30
    bt_manager._reconnect_timestamps = [90.0, 99.0]
    bt_manager.client = MagicMock()
    bt_manager.client.bt_management_enabled = True

    with (
        patch("bluetooth_manager.time.monotonic", return_value=100.0),
        patch("services.bluetooth.persist_device_released") as persist_released,
    ):
        assert bt_manager._check_reconnect_churn() is True

    assert bt_manager.management_enabled is False
    assert bt_manager.client.bt_management_enabled is False
    bt_manager.client._update_status.assert_called_once()
    persist_released.assert_called_once_with("TestSpeaker", True)


def test_cancel_reconnect_clears_runtime_reconnect_status(bt_manager):
    bt_manager.client = MagicMock()
    bt_manager.client.status = {"reconnecting": True}

    bt_manager.cancel_reconnect()

    assert bt_manager.management_enabled is False
    assert bt_manager._cancel_reconnect.is_set() is True
    bt_manager.client._update_status.assert_called_once_with({"reconnecting": False, "reconnect_attempt": 0})


def test_connect_device_aborts_when_release_cancels_active_reconnect(bt_manager):
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "disconnect_device", return_value=True) as disconnect_device,
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):

        def _run_side_effect(commands):
            if commands == [f"connect {bt_manager.mac_address}"]:
                bt_manager.cancel_reconnect()
            return True, ""

        bt_manager._run_bluetoothctl = MagicMock(side_effect=_run_side_effect)

        assert bt_manager.connect_device() is False

    disconnect_device.assert_called_once()
