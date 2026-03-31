"""Tests for the backend factory – services.backends.create_backend()."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from services.audio_backend import BackendType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_bt_manager() -> MagicMock:
    """Minimal BluetoothManager stub accepted by BluetoothA2dpBackend."""
    mgr = MagicMock()
    mgr.mac_address = "AA:BB:CC:DD:EE:FF"
    mgr.adapter = "hci0"
    mgr.connected = False
    mgr.on_sink_found = None
    mgr.battery_level = None
    return mgr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateBackendBluetooth:
    """Bluetooth A2DP backend creation."""

    def test_returns_bluetooth_backend(self, mock_bt_manager: MagicMock) -> None:
        from services.backends import create_backend
        from services.backends.bluetooth_a2dp import BluetoothA2dpBackend

        backend = create_backend("bluetooth_a2dp", bt_manager=mock_bt_manager)
        assert isinstance(backend, BluetoothA2dpBackend)

    def test_accepts_enum_value(self, mock_bt_manager: MagicMock) -> None:
        from services.backends import create_backend
        from services.backends.bluetooth_a2dp import BluetoothA2dpBackend

        backend = create_backend(BackendType.BLUETOOTH_A2DP, bt_manager=mock_bt_manager)
        assert isinstance(backend, BluetoothA2dpBackend)

    def test_missing_bt_manager_raises(self) -> None:
        from services.backends import create_backend

        with pytest.raises(ValueError, match="bt_manager"):
            create_backend("bluetooth_a2dp")


class TestCreateBackendMock:
    """Mock backend creation (testing helper)."""

    def test_returns_mock_backend(self) -> None:
        from services.backends import create_backend
        from services.backends.mock_backend import MockAudioBackend

        backend = create_backend("mock", backend_id="test-1")
        assert isinstance(backend, MockAudioBackend)
        assert backend.backend_id == "test-1"

    def test_mock_default_id(self) -> None:
        from services.backends import create_backend

        backend = create_backend("mock")
        assert backend.backend_id == "mock-default"


class TestCreateBackendUnimplemented:
    """Planned but not-yet-implemented backends."""

    def test_local_sink_raises(self) -> None:
        from services.backends import create_backend

        with pytest.raises(ValueError, match="not yet implemented"):
            create_backend("local_sink")

    def test_snapcast_raises(self) -> None:
        from services.backends import create_backend

        with pytest.raises(ValueError, match="not yet implemented"):
            create_backend("snapcast")


class TestCreateBackendUnknown:
    """Completely unknown backend types."""

    def test_unknown_string_raises(self) -> None:
        from services.backends import create_backend

        with pytest.raises(ValueError, match="Unknown backend type"):
            create_backend("unknown_type")


class TestCreateBackendSignature:
    """Factory function has proper type hints."""

    def test_has_return_annotation(self) -> None:
        from services.backends import create_backend

        sig = inspect.signature(create_backend)
        assert sig.return_annotation is not inspect.Parameter.empty

    def test_has_backend_type_annotation(self) -> None:
        from services.backends import create_backend

        sig = inspect.signature(create_backend)
        param = sig.parameters["backend_type"]
        assert param.annotation is not inspect.Parameter.empty

    def test_accepts_kwargs(self) -> None:
        from services.backends import create_backend

        sig = inspect.signature(create_backend)
        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        assert has_var_keyword, "create_backend should accept **kwargs"
