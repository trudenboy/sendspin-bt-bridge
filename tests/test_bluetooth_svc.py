"""Tests for services/bluetooth.py — BT helpers that don't require hardware."""

import json
from unittest.mock import MagicMock, patch

from services.bluetooth import (
    bt_remove_device,
    describe_pair_failure,
    extract_pair_failure_reason,
    is_audio_device,
    persist_device_enabled,
    persist_device_released,
)

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


def test_extract_pair_failure_reason_prefers_failed_to_pair_line():
    output = """
    [CHG] Device BC:7F:7B:C4:C2:56 Connected: yes
    Failed to pair: org.bluez.Error.ConnectionAttemptFailed
    [bluetooth]#
    """

    assert extract_pair_failure_reason(output) == "Failed to pair: org.bluez.Error.ConnectionAttemptFailed"


def test_extract_pair_failure_reason_falls_back_to_last_errorish_line():
    output = """
    [CHG] Device BC:7F:7B:C4:C2:56 Connected: yes
    AuthenticationFailed
    [bluetooth]#
    """

    assert extract_pair_failure_reason(output) == "AuthenticationFailed"


# ---------------------------------------------------------------------------
# describe_pair_failure — annotates PIN rejection cases
# ---------------------------------------------------------------------------


def test_describe_pair_failure_annotates_auth_fail_when_pin_attempted():
    """When the bridge auto-entered a PIN and pairing failed with an
    authentication error, the log message must explicitly call out PIN
    rejection so operators don't have to grep output for the cause.
    """
    output = """
    [agent] Enter PIN code: 0000
    [CHG] Device AA:BB:CC:DD:EE:FF Connected: no
    Failed to pair: org.bluez.Error.AuthenticationFailed
    """
    reason = describe_pair_failure(output, pin_attempted=True, pin_used="0000")
    assert "AuthenticationFailed" in reason
    assert "PIN" in reason
    assert "0000" in reason


def test_describe_pair_failure_does_not_annotate_when_no_pin_attempted():
    """If no PIN prompt was observed during the attempt, auth failures
    must not be misattributed to PIN rejection — the device may have
    cancelled authentication for other reasons (unsupported agent, etc.).
    """
    output = """
    [CHG] Device AA:BB:CC:DD:EE:FF Connected: no
    Failed to pair: org.bluez.Error.AuthenticationFailed
    """
    reason = describe_pair_failure(output, pin_attempted=False)
    assert reason == "Failed to pair: org.bluez.Error.AuthenticationFailed"
    assert "PIN" not in reason


def test_describe_pair_failure_does_not_annotate_non_auth_failure_even_with_pin():
    """A PIN was entered but the device failed for a non-auth reason
    (timeout, connection attempt failed): no PIN hint — PIN wasn't the
    problem."""
    output = """
    [agent] Enter PIN code: 0000
    Failed to pair: org.bluez.Error.ConnectionAttemptFailed
    """
    reason = describe_pair_failure(output, pin_attempted=True, pin_used="0000")
    assert "ConnectionAttemptFailed" in reason
    assert "PIN" not in reason


def test_describe_pair_failure_empty_output_returns_fallback():
    """Empty bluetoothctl output shouldn't crash — return an empty-ish
    string the caller can still log as 'no explicit reason'."""
    assert describe_pair_failure("", pin_attempted=False) == ""
    assert describe_pair_failure("", pin_attempted=True, pin_used="0000") == ""


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
