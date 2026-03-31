"""Tests for SendspinClient AudioBackend integration (V3-1)."""

from __future__ import annotations

import pytest

from services.backends.mock_backend import MockAudioBackend


@pytest.fixture
def mock_backend():
    """Create a MockAudioBackend for testing."""
    return MockAudioBackend(backend_id="test-mock")


@pytest.fixture
def client_with_backend(mock_backend):
    """Create a minimal SendspinClient with AudioBackend attached."""
    from sendspin_client import SendspinClient

    client = SendspinClient(
        player_name="Test Speaker",
        server_host="localhost",
        server_port=9000,
        bt_manager=None,
        listen_port=8928,
    )
    client.audio_backend = mock_backend
    return client


@pytest.fixture
def client_without_backend():
    """Create a SendspinClient without AudioBackend (legacy mode)."""
    from sendspin_client import SendspinClient

    return SendspinClient(
        player_name="Test Speaker",
        server_host="localhost",
        server_port=9000,
        bt_manager=None,
        listen_port=8928,
    )


# --- audio_backend property ---


class TestAudioBackendProperty:
    def test_audio_backend_default_none(self, client_without_backend):
        """New client has audio_backend=None by default."""
        assert client_without_backend.audio_backend is None

    def test_audio_backend_setter(self, client_without_backend, mock_backend):
        """Can set and read audio_backend."""
        client_without_backend.audio_backend = mock_backend
        assert client_without_backend.audio_backend is mock_backend

    def test_audio_backend_setter_none(self, client_with_backend):
        """Can clear audio_backend back to None."""
        client_with_backend.audio_backend = None
        assert client_with_backend.audio_backend is None


# --- audio_destination property ---


class TestAudioDestination:
    def test_audio_destination_with_backend(self, client_with_backend, mock_backend):
        """Uses backend.get_audio_destination() when backend is set."""
        mock_backend.connect()
        assert client_with_backend.audio_destination == f"mock_sink_{mock_backend.backend_id}"

    def test_audio_destination_without_backend_uses_sink(self, client_without_backend):
        """Falls back to bluetooth_sink_name when no backend."""
        client_without_backend.bluetooth_sink_name = "bluez_sink.AA_BB.a2dp_sink"
        assert client_without_backend.audio_destination == "bluez_sink.AA_BB.a2dp_sink"

    def test_audio_destination_without_anything(self, client_without_backend):
        """Returns None when no backend and no bluetooth_sink_name."""
        assert client_without_backend.audio_destination is None

    def test_audio_destination_backend_not_connected(self, client_with_backend):
        """Backend returns None when not connected."""
        assert client_with_backend.audio_destination is None

    def test_audio_destination_backend_overrides_sink(self, client_with_backend, mock_backend):
        """Backend takes priority over bluetooth_sink_name."""
        client_with_backend.bluetooth_sink_name = "bluez_sink.OLD.a2dp_sink"
        mock_backend.connect()
        assert client_with_backend.audio_destination == f"mock_sink_{mock_backend.backend_id}"


# --- backend_status property ---


class TestBackendStatus:
    def test_backend_status_with_backend(self, client_with_backend, mock_backend):
        """Returns backend.to_dict() when backend is set."""
        result = client_with_backend.backend_status
        assert result is not None
        assert result["backend_id"] == "test-mock"
        assert "connected" in result
        assert "backend_type" in result

    def test_backend_status_without_backend(self, client_without_backend):
        """Returns None when no backend."""
        assert client_without_backend.backend_status is None


# --- backend_connect / backend_disconnect ---


class TestBackendConnectDisconnect:
    @pytest.mark.asyncio
    async def test_backend_connect_with_backend(self, client_with_backend, mock_backend):
        """Calls backend.connect() and returns True on success."""
        result = await client_with_backend.backend_connect()
        assert result is True
        assert "connect" in mock_backend.call_log

    @pytest.mark.asyncio
    async def test_backend_connect_without_backend(self, client_without_backend):
        """Returns False when no backend (no-op)."""
        result = await client_without_backend.backend_connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_backend_disconnect_with_backend(self, client_with_backend, mock_backend):
        """Calls backend.disconnect() and returns True."""
        mock_backend.connect()
        result = await client_with_backend.backend_disconnect()
        assert result is True
        assert "disconnect" in mock_backend.call_log

    @pytest.mark.asyncio
    async def test_backend_disconnect_without_backend(self, client_without_backend):
        """Returns False when no backend (no-op)."""
        result = await client_without_backend.backend_disconnect()
        assert result is False


# --- snapshot() integration ---


class TestSnapshotIntegration:
    def test_snapshot_includes_backend(self, client_with_backend, mock_backend):
        """snapshot() includes audio_backend and audio_destination keys."""
        mock_backend.connect()
        snap = client_with_backend.snapshot()
        assert "audio_backend" in snap
        assert "audio_destination" in snap
        assert snap["audio_backend"]["backend_id"] == "test-mock"
        assert snap["audio_destination"] == f"mock_sink_{mock_backend.backend_id}"

    def test_snapshot_without_backend(self, client_without_backend):
        """snapshot() has audio_backend=None when no backend."""
        snap = client_without_backend.snapshot()
        assert snap["audio_backend"] is None
        assert snap["audio_destination"] is None


# --- backward compatibility ---


class TestBackwardCompat:
    def test_bt_manager_still_works(self, client_without_backend):
        """Setting bt_manager works as before — audio_backend is independent."""
        assert client_without_backend.bt_manager is None
        assert client_without_backend.audio_backend is None
        assert client_without_backend.bluetooth_sink_name is None

    def test_bt_manager_and_backend_coexist(self, client_with_backend, mock_backend):
        """bt_manager=None and audio_backend set — both attributes accessible."""
        assert client_with_backend.bt_manager is None
        assert client_with_backend.audio_backend is mock_backend

    def test_existing_snapshot_fields_preserved(self, client_without_backend):
        """All existing snapshot fields still present."""
        snap = client_without_backend.snapshot()
        expected_keys = {
            "status",
            "bluetooth_sink_name",
            "bt_management_enabled",
            "connected_server_url",
            "is_running",
            "player_name",
            "player_id",
            "listen_port",
            "server_host",
            "server_port",
            "static_delay_ms",
            "bt_manager",
            "bluetooth_mac",
            "effective_adapter_mac",
            "adapter",
            "adapter_hci_name",
            "battery_level",
            "paired",
            "max_reconnect_fails",
        }
        for key in expected_keys:
            assert key in snap, f"Missing existing snapshot key: {key}"
