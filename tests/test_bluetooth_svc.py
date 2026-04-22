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


def test_bt_remove_device_cleans_bluez_cache_when_adapter_given(tmp_path, monkeypatch):
    """After ``bluetoothctl remove``, the stale cache file at
    ``/var/lib/bluetooth/<adapter>/cache/<device>`` must be deleted too —
    BlueZ's `RemoveDevice` leaves service-record entries there, and the
    next pair attempt picks up the stale `ServiceRecords`/`Endpoints`,
    producing ``org.bluez.Error.Failed — Protocol not available`` on
    A2DP sinks (bluez/bluez#191, #348, #698). Guarding the cleanup
    behind an injectable base path keeps the test hermetic.
    """
    import services.bluetooth as _bt_mod

    adapter_mac = "C0:FB:F9:62:D6:9D"
    device_mac = "AA:BB:CC:DD:EE:FF"
    bluez_root = tmp_path / "var_lib_bluetooth"
    cache_dir = bluez_root / adapter_mac / "cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / device_mac
    cache_file.write_text("[ServiceRecords] stale junk")
    assert cache_file.exists()

    monkeypatch.setattr(_bt_mod, "_BLUEZ_LIB_DIR", bluez_root)

    with patch("services.bluetooth.threading.Thread") as mock_thread:
        bt_remove_device(device_mac, adapter_mac)
        target_fn = mock_thread.call_args[1]["target"]
        with patch("services.bluetooth.subprocess.run"):
            target_fn()

    assert not cache_file.exists(), (
        "bt_remove_device must delete /var/lib/bluetooth/<adapter>/cache/<device> "
        "after `bluetoothctl remove` to prevent stale-cache Protocol-not-available "
        "on the next pair attempt"
    )


def test_bt_remove_device_cache_cleanup_missing_file_does_not_raise(tmp_path, monkeypatch):
    """If the cache file does not exist (e.g. device never paired, or
    another process cleaned it first), the cleanup is a no-op — no
    exception propagates to kill the daemon thread and nothing is
    logged at warning level for this expected case."""
    import services.bluetooth as _bt_mod

    bluez_root = tmp_path / "var_lib_bluetooth"
    (bluez_root / "C0:FB:F9:62:D6:9D" / "cache").mkdir(parents=True)
    monkeypatch.setattr(_bt_mod, "_BLUEZ_LIB_DIR", bluez_root)

    with patch("services.bluetooth.threading.Thread") as mock_thread:
        bt_remove_device("AA:BB:CC:DD:EE:FF", "C0:FB:F9:62:D6:9D")
        target_fn = mock_thread.call_args[1]["target"]
        with patch("services.bluetooth.subprocess.run"):
            target_fn()  # must not raise


def test_bt_remove_device_skips_cache_cleanup_without_adapter(tmp_path, monkeypatch):
    """No adapter MAC → no known cache path → cleanup must not walk the
    BlueZ tree blindly (could match the wrong device if multiple
    adapters cached the same peer). Only the bluetoothctl remove runs."""
    import services.bluetooth as _bt_mod

    bluez_root = tmp_path / "var_lib_bluetooth"
    bluez_root.mkdir(parents=True)
    monkeypatch.setattr(_bt_mod, "_BLUEZ_LIB_DIR", bluez_root)

    with patch("services.bluetooth.threading.Thread") as mock_thread:
        bt_remove_device("AA:BB:CC:DD:EE:FF")  # no adapter_mac
        target_fn = mock_thread.call_args[1]["target"]
        with (
            patch("services.bluetooth.subprocess.run"),
            patch.object(_bt_mod, "_clean_bluez_cache") as clean_mock,
        ):
            target_fn()
        clean_mock.assert_not_called()


def test_bt_remove_device_logs_warning_when_bluetoothctl_reports_failure(caplog):
    """bluetoothctl returns exit 0 even when `remove <mac>` fails (device
    not in BlueZ tree, etc.). The "BT stack: removed" info log must not
    fire in that case — it misrepresents the state and misleads operators
    reading logs. Instead, a warning with the output detail is logged.
    """
    import logging

    with patch("services.bluetooth.threading.Thread") as mock_thread:
        bt_remove_device("AA:BB:CC:DD:EE:FF")
        target_fn = mock_thread.call_args[1]["target"]
        failure_output = "Device AA:BB:CC:DD:EE:FF not available\n"
        with patch("services.bluetooth.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=failure_output, stderr="", returncode=0)
            with caplog.at_level(logging.DEBUG, logger="services.bluetooth"):
                target_fn()

    messages = [r.getMessage() for r in caplog.records]
    assert not any("BT stack: removed" in m for m in messages), (
        "Must not claim success when bluetoothctl reports the device was not available"
    )
    assert any("not available" in m for m in messages), (
        "Expected a warning/log surfacing the bluetoothctl failure detail"
    )


def test_bt_remove_device_logs_removed_only_on_success(caplog):
    """When bluetoothctl actually removes the device (output contains
    "Device has been removed"), the success log fires as before."""
    import logging

    with patch("services.bluetooth.threading.Thread") as mock_thread:
        bt_remove_device("AA:BB:CC:DD:EE:FF")
        target_fn = mock_thread.call_args[1]["target"]
        success_output = "[DEL] Device AA:BB:CC:DD:EE:FF Speaker\nDevice has been removed\n"
        with patch("services.bluetooth.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=success_output, stderr="", returncode=0)
            with caplog.at_level(logging.INFO, logger="services.bluetooth"):
                target_fn()

    messages = [r.getMessage() for r in caplog.records]
    assert any("BT stack: removed" in m for m in messages)


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
