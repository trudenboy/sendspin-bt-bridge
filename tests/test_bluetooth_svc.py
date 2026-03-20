"""Tests for services/bluetooth.py — BT helpers that don't require hardware."""

import json
from unittest.mock import MagicMock, patch

from services.bluetooth import bt_remove_device, is_audio_device, persist_device_enabled, persist_device_released

# ---------------------------------------------------------------------------
# bt_remove_device
# ---------------------------------------------------------------------------


def test_bt_remove_device_invalid_mac():
    """Invalid MAC should be rejected without spawning a subprocess."""
    with (
        patch("services.bluetooth.subprocess.run") as mock_run,
        patch("services.bluetooth.threading.Thread") as mock_thread,
    ):
        bt_remove_device("invalid")
        mock_run.assert_not_called()
        mock_thread.assert_not_called()


def test_bt_remove_device_valid_mac():
    """Valid MAC should spawn a daemon thread that calls bluetoothctl."""
    with patch("services.bluetooth.threading.Thread") as mock_thread:
        bt_remove_device("AA:BB:CC:DD:EE:FF")
        mock_thread.assert_called_once()
        _, kwargs = mock_thread.call_args
        assert kwargs["daemon"] is True

        # Execute the target function and verify bluetoothctl is called
        target_fn = kwargs["target"]
        with patch("services.bluetooth.subprocess.run") as mock_run:
            target_fn()
            mock_run.assert_called_once()
            args = mock_run.call_args
            assert args[0][0] == ["bluetoothctl"]
            assert "AA:BB:CC:DD:EE:FF" in args[1]["input"]


# ---------------------------------------------------------------------------
# is_audio_device
# ---------------------------------------------------------------------------


def test_is_audio_device_invalid_mac():
    """Invalid MAC should return False immediately."""
    assert is_audio_device("not-a-mac") is False


def test_is_audio_device_valid():
    """bluetoothctl output with audio major class should return True."""
    bt_output = (
        "Device AA:BB:CC:DD:EE:FF Speaker\n"
        "\tName: Speaker\n"
        "\tClass: 0x240404\n"  # major class 4 = Audio/Video
        "\tPaired: yes\n"
    )
    with patch("services.bluetooth.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=bt_output)
        assert is_audio_device("AA:BB:CC:DD:EE:FF") is True


def test_is_audio_device_not_audio():
    """bluetoothctl output without audio class or UUID should return False."""
    bt_output = (
        "Device AA:BB:CC:DD:EE:FF Keyboard\n"
        "\tName: Keyboard\n"
        "\tClass: 0x000540\n"  # major class 5 = Peripheral
        "\tUUID: Human Interface Device (00001124-0000-1000-8000-00805f9b34fb)\n"
    )
    with patch("services.bluetooth.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=bt_output)
        assert is_audio_device("AA:BB:CC:DD:EE:FF") is False


# ---------------------------------------------------------------------------
# persist_device_enabled
# ---------------------------------------------------------------------------


def test_persist_device_enabled(tmp_config, monkeypatch):
    """persist_device_enabled should update the enabled flag in config.json."""

    import services.bluetooth as _bt_mod

    # _CONFIG_FILE is bound at import time; redirect it to the tmp file.
    monkeypatch.setattr(_bt_mod, "_CONFIG_FILE", tmp_config)

    data = {
        "BLUETOOTH_DEVICES": [
            {"player_name": "Speaker1", "enabled": True},
            {"player_name": "Speaker2", "enabled": True},
        ]
    }
    tmp_config.write_text(json.dumps(data))

    persist_device_enabled("Speaker1", False)

    result = json.loads(tmp_config.read_text())
    devs = result["BLUETOOTH_DEVICES"]
    assert devs[0]["enabled"] is False
    assert devs[1]["enabled"] is True


def test_persist_device_released(tmp_config, monkeypatch):
    """persist_device_released should update the released flag in config.json."""

    import services.bluetooth as _bt_mod

    monkeypatch.setattr(_bt_mod, "_CONFIG_FILE", tmp_config)

    data = {
        "BLUETOOTH_DEVICES": [
            {"player_name": "Speaker1", "released": False},
            {"player_name": "Speaker2", "released": False},
        ]
    }
    tmp_config.write_text(json.dumps(data))

    persist_device_released("Speaker1", True)

    result = json.loads(tmp_config.read_text())
    devs = result["BLUETOOTH_DEVICES"]
    assert devs[0]["released"] is True
    assert devs[1]["released"] is False
