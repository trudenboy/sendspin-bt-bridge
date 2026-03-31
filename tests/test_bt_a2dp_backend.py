"""Tests for BluetoothA2dpBackend — concrete AudioBackend for BT A2DP."""

from __future__ import annotations

from unittest.mock import MagicMock

from services.audio_backend import (
    AudioBackend,
    BackendCapability,
    BackendStatus,
    BackendType,
)
from services.backends.bluetooth_a2dp import BluetoothA2dpBackend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bt_manager(**overrides) -> MagicMock:
    """Return a mock BluetoothManager with sensible defaults."""
    mgr = MagicMock()
    mgr.mac_address = overrides.get("mac_address", "AA:BB:CC:DD:EE:FF")
    mgr.adapter = overrides.get("adapter", "hci0")
    mgr.device_name = overrides.get("device_name", "ENEBY20")
    mgr.connected = overrides.get("connected", False)
    mgr.battery_level = overrides.get("battery_level")
    mgr.on_sink_found = overrides.get("on_sink_found")
    mgr.connect_device.return_value = overrides.get("connect_ok", True)
    mgr.configure_bluetooth_audio.return_value = overrides.get("audio_ok", True)
    mgr.disconnect_device.return_value = overrides.get("disconnect_ok", True)
    return mgr


# ---------------------------------------------------------------------------
# ABC conformance
# ---------------------------------------------------------------------------


class TestABCConformance:
    def test_is_audio_backend_subclass(self):
        assert issubclass(BluetoothA2dpBackend, AudioBackend)

    def test_instantiation_satisfies_abc(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        assert isinstance(backend, AudioBackend)


# ---------------------------------------------------------------------------
# backend_type / backend_id
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_backend_type(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        assert backend.backend_type is BackendType.BLUETOOTH_A2DP

    def test_backend_id_contains_mac(self):
        backend = BluetoothA2dpBackend(_make_bt_manager(mac_address="11:22:33:44:55:66"))
        bid = backend.backend_id
        assert "11:22:33:44:55:66" in bid

    def test_backend_id_has_prefix(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        assert backend.backend_id.startswith("bt-a2dp-")


# ---------------------------------------------------------------------------
# mac / adapter convenience properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_mac_property(self):
        backend = BluetoothA2dpBackend(_make_bt_manager(mac_address="CC:DD:EE:FF:00:11"))
        assert backend.mac == "CC:DD:EE:FF:00:11"

    def test_adapter_property(self):
        backend = BluetoothA2dpBackend(_make_bt_manager(adapter="hci1"))
        assert backend.adapter == "hci1"


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------


class TestConnect:
    def test_connect_delegates_to_bt_manager(self):
        mgr = _make_bt_manager(connect_ok=True, audio_ok=True)
        backend = BluetoothA2dpBackend(mgr)
        result = backend.connect()
        assert result is True
        mgr.connect_device.assert_called_once()
        mgr.configure_bluetooth_audio.assert_called_once()

    def test_connect_returns_false_when_connect_device_fails(self):
        mgr = _make_bt_manager(connect_ok=False)
        backend = BluetoothA2dpBackend(mgr)
        assert backend.connect() is False
        mgr.configure_bluetooth_audio.assert_not_called()

    def test_connect_returns_true_even_if_audio_config_fails(self):
        """BT is connected; audio sink may appear later — still return True."""
        mgr = _make_bt_manager(connect_ok=True, audio_ok=False)
        backend = BluetoothA2dpBackend(mgr)
        assert backend.connect() is True


# ---------------------------------------------------------------------------
# disconnect()
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_disconnect_delegates_to_bt_manager(self):
        mgr = _make_bt_manager(disconnect_ok=True)
        backend = BluetoothA2dpBackend(mgr)
        result = backend.disconnect()
        assert result is True
        mgr.disconnect_device.assert_called_once()

    def test_disconnect_returns_false_on_failure(self):
        mgr = _make_bt_manager(disconnect_ok=False)
        backend = BluetoothA2dpBackend(mgr)
        assert backend.disconnect() is False

    def test_disconnect_clears_sink_name(self):
        mgr = _make_bt_manager()
        backend = BluetoothA2dpBackend(mgr)
        backend._sink_name = "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
        backend.disconnect()
        assert backend.get_audio_destination() is None

    def test_disconnect_returns_true_on_exception(self):
        """Graceful degradation — log and return False on unexpected errors."""
        mgr = _make_bt_manager()
        mgr.disconnect_device.side_effect = RuntimeError("D-Bus exploded")
        backend = BluetoothA2dpBackend(mgr)
        assert backend.disconnect() is False


# ---------------------------------------------------------------------------
# is_ready()
# ---------------------------------------------------------------------------


class TestIsReady:
    def test_ready_when_connected_and_sink_set(self):
        mgr = _make_bt_manager(connected=True)
        backend = BluetoothA2dpBackend(mgr)
        backend._sink_name = "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
        assert backend.is_ready() is True

    def test_not_ready_when_disconnected(self):
        mgr = _make_bt_manager(connected=False)
        backend = BluetoothA2dpBackend(mgr)
        backend._sink_name = "some_sink"
        assert backend.is_ready() is False

    def test_not_ready_when_no_sink(self):
        mgr = _make_bt_manager(connected=True)
        backend = BluetoothA2dpBackend(mgr)
        assert backend.is_ready() is False


# ---------------------------------------------------------------------------
# get_audio_destination() / sink capture via on_sink_found
# ---------------------------------------------------------------------------


class TestAudioDestination:
    def test_returns_none_before_connect(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        assert backend.get_audio_destination() is None

    def test_returns_sink_after_capture(self):
        mgr = _make_bt_manager()
        backend = BluetoothA2dpBackend(mgr)
        # Simulate bt_manager calling on_sink_found
        mgr.on_sink_found("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink", 42)
        assert backend.get_audio_destination() == "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"

    def test_on_sink_found_chains_original_callback(self):
        original = MagicMock()
        mgr = _make_bt_manager(on_sink_found=original)
        backend = BluetoothA2dpBackend(mgr)
        mgr.on_sink_found("my_sink", 7)
        original.assert_called_once_with("my_sink", 7)
        assert backend.get_audio_destination() == "my_sink"

    def test_on_sink_found_works_without_original_callback(self):
        mgr = _make_bt_manager(on_sink_found=None)
        backend = BluetoothA2dpBackend(mgr)
        mgr.on_sink_found("my_sink", None)
        assert backend.get_audio_destination() == "my_sink"


# ---------------------------------------------------------------------------
# set_volume() / get_volume()
# ---------------------------------------------------------------------------


class TestVolume:
    def test_volume_none_initially(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        assert backend.get_volume() is None

    def test_set_and_get_volume(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        backend.set_volume(75)
        assert backend.get_volume() == 75

    def test_volume_clamped_to_0(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        backend.set_volume(-10)
        assert backend.get_volume() == 0

    def test_volume_clamped_to_100(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        backend.set_volume(200)
        assert backend.get_volume() == 100


# ---------------------------------------------------------------------------
# get_status()
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_status_when_connected_with_sink(self):
        mgr = _make_bt_manager(connected=True, battery_level=85)
        backend = BluetoothA2dpBackend(mgr)
        backend._sink_name = "some_sink"
        status = backend.get_status()
        assert isinstance(status, BackendStatus)
        assert status.connected is True
        assert status.available is True
        assert status.battery_level == 85
        assert status.error is None

    def test_status_when_disconnected(self):
        mgr = _make_bt_manager(connected=False)
        backend = BluetoothA2dpBackend(mgr)
        status = backend.get_status()
        assert status.connected is False
        assert status.available is False

    def test_status_connected_but_no_sink(self):
        mgr = _make_bt_manager(connected=True)
        backend = BluetoothA2dpBackend(mgr)
        status = backend.get_status()
        assert status.connected is True
        assert status.available is False


# ---------------------------------------------------------------------------
# get_capabilities()
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_capabilities_include_expected(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        caps = backend.get_capabilities()
        assert BackendCapability.VOLUME_CONTROL in caps
        assert BackendCapability.DEVICE_DISCOVERY in caps
        assert BackendCapability.BATTERY_REPORTING in caps
        assert BackendCapability.CODEC_SELECTION in caps

    def test_capabilities_exclude_handoff(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        caps = backend.get_capabilities()
        assert BackendCapability.HANDOFF_SUPPORT not in caps

    def test_capabilities_returns_set(self):
        backend = BluetoothA2dpBackend(_make_bt_manager())
        assert isinstance(backend.get_capabilities(), set)


# ---------------------------------------------------------------------------
# to_dict()  (inherited from AudioBackend)
# ---------------------------------------------------------------------------


class TestToDict:
    def test_to_dict_keys(self):
        mgr = _make_bt_manager(connected=True, battery_level=42)
        backend = BluetoothA2dpBackend(mgr)
        backend._sink_name = "test_sink"
        d = backend.to_dict()
        assert d["backend_type"] == "bluetooth_a2dp"
        assert d["backend_id"] == "bt-a2dp-AA:BB:CC:DD:EE:FF"
        assert d["connected"] is True
        assert d["available"] is True
        assert d["audio_destination"] == "test_sink"
        assert d["battery_level"] == 42
        assert "volume_control" in d["capabilities"]

    def test_to_dict_when_disconnected(self):
        backend = BluetoothA2dpBackend(_make_bt_manager(connected=False))
        d = backend.to_dict()
        assert d["connected"] is False
        assert d["available"] is False
        assert d["audio_destination"] is None
