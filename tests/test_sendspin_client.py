"""Tests for sendspin_client.py — DeviceStatus dataclass and SendspinClient orchestrator.

Covers the dict-compatible DeviceStatus interface, thread-safe status mutation,
subprocess lifecycle helpers, IPC parsing, volume persistence, and edge cases
(malformed JSON, concurrent updates, subprocess crashes).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _ReadlineStdout:
    """Async stdout mock that yields pre-encoded lines then EOF via readline()."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory for every test."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{}")


@pytest.fixture()
def _patch_state():
    """Stub state helpers so tests never touch real SSE or event buses."""
    with (
        patch("sendspin_client._state.notify_status_changed"),
        patch("sendspin_client._state.publish_device_event"),
    ):
        yield


@pytest.fixture()
def client(_patch_state):
    """Return a minimal SendspinClient with a mocked BluetoothManager."""
    from sendspin_client import SendspinClient

    bt = MagicMock()
    bt.mac_address = "AA:BB:CC:DD:EE:FF"
    bt.check_bluetooth_available.return_value = True
    bt.connected = False

    return SendspinClient(
        player_name="TestSpeaker",
        server_host="192.168.1.10",
        server_port=9000,
        bt_manager=bt,
        listen_port=8928,
    )


# ===================================================================
# DeviceStatus dataclass
# ===================================================================


class TestDeviceStatusDictInterface:
    """DeviceStatus must behave like a dict for backward-compat callers."""

    def test_getitem_returns_field_value(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus(volume=42)
        assert ds["volume"] == 42

    def test_getitem_raises_keyerror_for_unknown_key(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        with pytest.raises(KeyError):
            ds["no_such_field"]

    def test_setitem_sets_known_field(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        ds["volume"] = 77
        assert ds.volume == 77

    def test_setitem_ignores_unknown_field(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        ds["bogus_key"] = 123  # should not raise
        assert not hasattr(ds, "bogus_key") or ds.get("bogus_key") is None

    def test_contains_known_field(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        assert "volume" in ds
        assert "playing" in ds

    def test_contains_unknown_field(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        assert "nonexistent" not in ds

    def test_contains_non_string_returns_false(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        assert 42 not in ds

    def test_get_returns_value_or_default(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus(volume=55)
        assert ds.get("volume") == 55
        assert ds.get("no_such_field", "fallback") == "fallback"
        assert ds.get("missing") is None

    def test_update_applies_known_keys(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        ds.update({"volume": 80, "playing": True, "current_track": "Song"})
        assert ds.volume == 80
        assert ds.playing is True
        assert ds.current_track == "Song"

    def test_update_ignores_unknown_keys(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        ds.update({"volume": 50, "alien_key": "ignored"})
        assert ds.volume == 50

    def test_copy_returns_plain_dict(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus(volume=65, playing=True)
        d = ds.copy()
        assert isinstance(d, dict)
        assert d["volume"] == 65
        assert d["playing"] is True
        # mutating the copy must not affect the original
        d["volume"] = 0
        assert ds.volume == 65

    def test_copy_excludes_field_names_cache(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        d = ds.copy()
        assert "_field_names" not in d

    def test_equality_between_instances(self):
        from sendspin_client import DeviceStatus

        fixed_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        a = DeviceStatus(volume=50, playing=True, uptime_start=fixed_time)
        b = DeviceStatus(volume=50, playing=True, uptime_start=fixed_time)
        assert a == b

    def test_inequality_between_instances(self):
        from sendspin_client import DeviceStatus

        a = DeviceStatus(volume=50)
        b = DeviceStatus(volume=99)
        assert a != b

    def test_default_field_values(self):
        from sendspin_client import DeviceStatus

        ds = DeviceStatus()
        assert ds.volume == 100
        assert ds.muted is False
        assert ds.playing is False
        assert ds.connected is False
        assert ds.bluetooth_connected is False
        assert isinstance(ds.uptime_start, datetime)


# ===================================================================
# Helper functions
# ===================================================================


class TestNormalizeDeviceMac:
    def test_strips_and_uppercases(self):
        from sendspin_client import _normalize_device_mac

        assert _normalize_device_mac("  aa:bb:cc:dd:ee:ff  ") == "AA:BB:CC:DD:EE:FF"

    def test_non_string_returns_empty(self):
        from sendspin_client import _normalize_device_mac

        assert _normalize_device_mac(None) == ""
        assert _normalize_device_mac(123) == ""


class TestFilterDuplicateBluetoothDevices:
    def test_keeps_first_occurrence_of_duplicate_mac(self):
        from sendspin_client import _filter_duplicate_bluetooth_devices

        devices = [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "First"},
            {"mac": "aa:bb:cc:dd:ee:ff", "player_name": "Duplicate"},
            {"mac": "11:22:33:44:55:66", "player_name": "Other"},
        ]
        result = _filter_duplicate_bluetooth_devices(devices)
        assert len(result) == 2
        assert result[0]["player_name"] == "First"
        assert result[1]["player_name"] == "Other"

    def test_preserves_order_without_duplicates(self):
        from sendspin_client import _filter_duplicate_bluetooth_devices

        devices = [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "A"},
            {"mac": "11:22:33:44:55:66", "player_name": "B"},
        ]
        result = _filter_duplicate_bluetooth_devices(devices)
        assert len(result) == 2

    def test_empty_list(self):
        from sendspin_client import _filter_duplicate_bluetooth_devices

        assert _filter_duplicate_bluetooth_devices([]) == []


class TestLoadSavedDeviceVolume:
    def test_returns_saved_volume(self, tmp_path):
        import config

        config.CONFIG_FILE.write_text(json.dumps({"LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 42}}))
        from sendspin_client import _load_saved_device_volume

        with patch("sendspin_client.CONFIG_FILE", config.CONFIG_FILE):
            assert _load_saved_device_volume("AA:BB:CC:DD:EE:FF") == 42

    def test_returns_none_for_missing_mac(self, tmp_path):
        import config

        config.CONFIG_FILE.write_text(json.dumps({"LAST_VOLUMES": {}}))
        from sendspin_client import _load_saved_device_volume

        with patch("sendspin_client.CONFIG_FILE", config.CONFIG_FILE):
            assert _load_saved_device_volume("AA:BB:CC:DD:EE:FF") is None

    def test_returns_none_for_out_of_range(self, tmp_path):
        import config

        config.CONFIG_FILE.write_text(json.dumps({"LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 150}}))
        from sendspin_client import _load_saved_device_volume

        with patch("sendspin_client.CONFIG_FILE", config.CONFIG_FILE):
            assert _load_saved_device_volume("AA:BB:CC:DD:EE:FF") is None

    def test_returns_none_on_corrupted_file(self, tmp_path):
        import config

        config.CONFIG_FILE.write_text("{bad json")
        from sendspin_client import _load_saved_device_volume

        with patch("sendspin_client.CONFIG_FILE", config.CONFIG_FILE):
            assert _load_saved_device_volume("AA:BB:CC:DD:EE:FF") is None


# ===================================================================
# SendspinClient — status management
# ===================================================================


class TestUpdateStatus:
    def test_update_status_changes_fields(self, client):
        client._update_status({"volume": 42, "playing": True})
        assert client.status.volume == 42
        assert client.status.playing is True

    def test_update_status_is_thread_safe(self, client):
        """Concurrent writers must not corrupt status."""
        errors = []

        def writer(vol: int):
            try:
                for _ in range(100):
                    client._update_status({"volume": vol})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert isinstance(client.status.volume, int)

    def test_get_status_value_returns_default(self, client):
        assert client.get_status_value("nonexistent", "default") == "default"

    def test_get_status_value_reads_current(self, client):
        client._update_status({"volume": 77})
        assert client.get_status_value("volume") == 77


# ===================================================================
# SendspinClient — subprocess lifecycle
# ===================================================================


class TestIsRunning:
    def test_false_when_no_process(self, client):
        assert client.is_running() is False

    def test_true_when_process_alive(self, client):
        proc = MagicMock()
        proc.returncode = None
        client._daemon_proc = proc
        assert client.is_running() is True

    def test_false_when_process_exited(self, client):
        proc = MagicMock()
        proc.returncode = 1
        client._daemon_proc = proc
        assert client.is_running() is False


class TestGetSubprocessPid:
    def test_returns_none_when_no_process(self, client):
        assert client.get_subprocess_pid() is None

    def test_returns_pid_when_alive(self, client):
        proc = MagicMock()
        proc.returncode = None
        proc.pid = 12345
        client._daemon_proc = proc
        assert client.get_subprocess_pid() == 12345

    def test_returns_none_when_exited(self, client):
        proc = MagicMock()
        proc.returncode = 0
        proc.pid = 12345
        client._daemon_proc = proc
        assert client.get_subprocess_pid() is None


class TestStopSendspin:
    @pytest.mark.asyncio
    async def test_stop_clears_status_fields(self, client):
        client._update_status({"server_connected": True, "playing": True, "group_name": "TestGroup"})
        await client.stop_sendspin()
        assert client.status.server_connected is False
        assert client.status.playing is False
        assert client.status.group_name is None

    @pytest.mark.asyncio
    async def test_stop_nullifies_daemon_proc(self, client):
        proc = AsyncMock()
        proc.returncode = None
        proc.wait = AsyncMock(return_value=0)
        proc.kill = MagicMock()
        client._daemon_proc = proc
        await client.stop_sendspin()
        assert client._daemon_proc is None


class TestSnapshot:
    def test_snapshot_returns_expected_keys(self, client):
        snap = client.snapshot()
        assert "status" in snap
        assert "player_name" in snap
        assert "player_id" in snap
        assert "bluetooth_sink_name" in snap
        assert "is_running" in snap
        assert isinstance(snap["status"], dict)

    def test_snapshot_is_atomic_copy(self, client):
        client._update_status({"volume": 33})
        snap = client.snapshot()
        client._update_status({"volume": 99})
        assert snap["status"]["volume"] == 33


# ===================================================================
# SendspinClient — IPC / subprocess output
# ===================================================================


class TestReadSubprocessOutput:
    @pytest.mark.asyncio
    async def test_parses_status_message_and_updates(self, client):
        """A valid JSON status line should update client status."""
        # IPC protocol uses flat status envelopes: {"type": "status", "volume": 55, ...}
        status_line = json.dumps({"type": "status", "volume": 55, "playing": True}).encode() + b"\n"

        proc = MagicMock()
        proc.stdout = _ReadlineStdout([status_line])
        proc.returncode = None
        client._daemon_proc = proc

        await client._read_subprocess_output()
        assert client.status.volume == 55
        assert client.status.playing is True

    @pytest.mark.asyncio
    async def test_malformed_json_is_skipped(self, client):
        """Non-JSON lines must be silently ignored, not crash."""

        lines = [b"not-json\n", json.dumps({"type": "status", "volume": 88}).encode() + b"\n"]

        proc = MagicMock()
        proc.stdout = _ReadlineStdout(lines)
        proc.returncode = None
        client._daemon_proc = proc

        await client._read_subprocess_output()
        assert client.status.volume == 88

    @pytest.mark.asyncio
    async def test_volume_change_triggers_save(self, client):
        """Volume updates from subprocess should call save_device_volume."""
        status_line = json.dumps({"type": "status", "volume": 60}).encode() + b"\n"

        proc = MagicMock()
        proc.stdout = _ReadlineStdout([status_line])
        proc.returncode = None
        client._daemon_proc = proc

        with patch("sendspin_client.save_device_volume") as mock_save:
            await client._read_subprocess_output()
            mock_save.assert_called_once_with("AA:BB:CC:DD:EE:FF", 60)

    @pytest.mark.asyncio
    async def test_non_volume_status_does_not_save(self, client):
        """Status updates without volume should not trigger save."""
        status_line = json.dumps({"type": "status", "playing": True}).encode() + b"\n"

        proc = MagicMock()
        proc.stdout = _ReadlineStdout([status_line])
        proc.returncode = None
        client._daemon_proc = proc

        with patch("sendspin_client.save_device_volume") as mock_save:
            await client._read_subprocess_output()
            mock_save.assert_not_called()

    @pytest.mark.asyncio
    async def test_server_connected_clears_ma_reconnecting(self, client):
        """server_connected=True in status should clear the MA reconnecting flag."""
        client._update_status({"ma_reconnecting": True})
        status_line = json.dumps({"type": "status", "server_connected": True}).encode() + b"\n"

        proc = MagicMock()
        proc.stdout = _ReadlineStdout([status_line])
        proc.returncode = None
        client._daemon_proc = proc

        await client._read_subprocess_output()
        assert client.status.ma_reconnecting is False


class TestReadSubprocessStderr:
    @pytest.mark.asyncio
    async def test_returns_immediately_if_no_proc(self, client):
        """No crash when daemon_proc is None."""
        client._daemon_proc = None
        await client._read_subprocess_stderr()

    @pytest.mark.asyncio
    async def test_returns_immediately_if_no_stderr(self, client):
        proc = MagicMock()
        proc.stderr = None
        client._daemon_proc = proc
        await client._read_subprocess_stderr()


# ===================================================================
# SendspinClient — commands
# ===================================================================


class TestSendSubprocessCommand:
    @pytest.mark.asyncio
    async def test_delegates_to_command_service(self, client):
        client._command_service.send = AsyncMock()
        client._daemon_proc = MagicMock()
        await client._send_subprocess_command({"cmd": "set_volume", "value": 50})
        client._command_service.send.assert_called_once_with(client._daemon_proc, {"cmd": "set_volume", "value": 50})


class TestSendTransportCommand:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_process(self, client):
        result = await client.send_transport_command("play")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_and_sends_command(self, client):
        proc = MagicMock()
        proc.returncode = None
        client._daemon_proc = proc
        client._command_service.send = AsyncMock()

        result = await client.send_transport_command("pause")
        assert result is True
        client._command_service.send.assert_called_once()
        cmd = client._command_service.send.call_args[0][1]
        assert cmd["cmd"] == "transport"
        assert cmd["action"] == "pause"

    @pytest.mark.asyncio
    async def test_includes_value_when_provided(self, client):
        proc = MagicMock()
        proc.returncode = None
        client._daemon_proc = proc
        client._command_service.send = AsyncMock()

        await client.send_transport_command("set_volume", 75)
        cmd = client._command_service.send.call_args[0][1]
        assert cmd["value"] == 75


class TestSendReconnect:
    @pytest.mark.asyncio
    async def test_noop_when_not_server_connected(self, client):
        """Reconnect should do nothing if server isn't connected."""
        client._command_service.send = AsyncMock()
        await client.send_reconnect()
        client._command_service.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_reconnect_when_connected(self, client):
        proc = MagicMock()
        proc.returncode = None
        client._daemon_proc = proc
        client._update_status({"server_connected": True})
        client._command_service.send = AsyncMock()

        await client.send_reconnect()
        client._command_service.send.assert_called_once()
        cmd = client._command_service.send.call_args[0][1]
        assert cmd["cmd"] == "reconnect"
        assert client.status.ma_reconnecting is True


# ===================================================================
# SendspinClient — BT management
# ===================================================================


class TestSetBtManagementEnabled:
    def test_disable_updates_status(self, client):
        # ensure daemon is not running so we skip the stop_sendspin path
        client._daemon_proc = None
        client.set_bt_management_enabled(False)
        assert client.bt_management_enabled is False
        assert client.status.bt_management_enabled is False
        assert client.status.bt_released_by == "user"

    def test_enable_updates_status(self, client):
        client._daemon_proc = None
        client.set_bt_management_enabled(False)
        client.set_bt_management_enabled(True)
        assert client.bt_management_enabled is True
        assert client.status.bt_management_enabled is True
        assert client.status.bt_released_by is None


# ===================================================================
# SendspinClient — constructor / initialization
# ===================================================================


class TestClientInit:
    def test_player_id_derived_from_mac(self, client):
        """player_id should be a UUID5 derived from the BT MAC."""
        from config import _player_id_from_mac

        expected = _player_id_from_mac("AA:BB:CC:DD:EE:FF")
        assert client.player_id == expected

    def test_no_bt_manager_uses_name_fallback(self, _patch_state):
        import uuid

        from sendspin_client import SendspinClient

        c = SendspinClient(
            player_name="NoBT Player",
            server_host="auto",
            server_port=9000,
        )
        expected = str(uuid.uuid5(uuid.NAMESPACE_DNS, "nobt player"))
        assert c.player_id == expected

    def test_long_name_player_id_fits_mdns(self, _patch_state):
        """Long speaker names must produce UUID5 player_id (always 36 chars, ≤63 bytes)."""
        from sendspin_client import SendspinClient

        c = SendspinClient(
            player_name="[AV] Samsung Soundbar M360 M-Series @ asus-laptop-ubuntu",
            server_host="auto",
            server_port=9000,
        )
        assert len(c.player_id) == 36
        assert len(c.player_id.encode()) <= 63

    def test_long_name_player_id_is_deterministic(self, _patch_state):
        """Same long name must always produce the same player_id."""
        from sendspin_client import SendspinClient

        name = "[AV] Samsung Soundbar M360 M-Series @ asus-laptop-ubuntu"
        c1 = SendspinClient(player_name=name, server_host="auto", server_port=9000)
        c2 = SendspinClient(player_name=name, server_host="auto", server_port=9000)
        assert c1.player_id == c2.player_id
        from sendspin_client import SendspinClient

        c = SendspinClient(
            player_name="Test",
            server_host="auto",
            server_port=9000,
            keepalive_interval=5,
        )
        assert c.keepalive_interval == 30

    def test_initial_status_reflects_bt_availability(self, client):
        assert client.status.bluetooth_available is True
