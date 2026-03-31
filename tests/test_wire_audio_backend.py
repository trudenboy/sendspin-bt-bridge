"""Tests for wiring AudioBackend into device initialization.

Verifies that bridge_orchestrator.initialize_devices() creates a
BluetoothA2dpBackend for each device with a bt_manager and registers
the player+backend pair in the BackendOrchestrator.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import config
import state
from bridge_orchestrator import BridgeOrchestrator
from services.backend_orchestrator import BackendOrchestrator as BOrc
from services.backends.bluetooth_a2dp import BluetoothA2dpBackend
from services.player_model import Player, PlayerState

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"BLUETOOTH_DEVICES": []}))
    state.reset_startup_progress()
    state.set_runtime_mode_info(None)
    state.set_clients([])
    state.set_disabled_devices([])
    # Reset global BackendOrchestrator state so tests are independent
    orch = state.get_backend_orchestrator()
    with orch._lock:
        orch._players.clear()
        orch._backends.clear()
        orch._states.clear()


# ---------------------------------------------------------------------------
# Stubs (mirrors tests/test_player_registry_bridge.py)
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal client stub matching the SendspinClient contract."""

    def __init__(
        self,
        player_name,
        server_host,
        server_port,
        _bt_manager,
        *,
        listen_port,
        static_delay_ms,
        listen_host,
        effective_bridge,
        preferred_format,
        keepalive_enabled,
        keepalive_interval,
        idle_disconnect_minutes=0,
    ):
        self.player_name = player_name
        self.server_host = server_host
        self.server_port = server_port
        self.listen_port = listen_port
        self.listen_host = listen_host
        self.static_delay_ms = static_delay_ms
        self._effective_bridge = effective_bridge
        self.preferred_format = preferred_format
        self.keepalive_enabled = keepalive_enabled
        self.keepalive_interval = keepalive_interval
        self.idle_disconnect_minutes = idle_disconnect_minutes
        self.bt_manager = None
        self.bt_management_enabled = True
        self.bluetooth_sink_name = None
        self.status = {"volume": 100, "bluetooth_available": None}
        self._player = None
        self.audio_backend = None  # must accept AudioBackend assignment

    def _update_status(self, updates):
        self.status.update(updates)

    def set_bt_management_enabled(self, enabled):
        self.bt_management_enabled = enabled


class _FakeBtManager:
    """Minimal bt_manager stub with attributes needed by BluetoothA2dpBackend."""

    def __init__(self, mac_address, **kwargs):
        self.mac_address = mac_address
        self.kwargs = kwargs
        self.on_sink_found = kwargs.get("on_sink_found")
        self.connected = False

    def check_bluetooth_available(self):
        return True


def _make_bootstrap(*devices):
    """Build a SimpleNamespace bootstrap with the given device configs."""
    return SimpleNamespace(
        device_configs=list(devices),
        server_host="localhost",
        server_port=9000,
        effective_bridge="",
        prefer_sbc=False,
        bt_check_interval=15,
        bt_max_reconnect_fails=5,
        bt_churn_threshold=3,
        bt_churn_window=120.0,
        log_level="INFO",
        pulse_latency_msec=250,
    )


# ===================================================================
# Unit tests — BackendOrchestrator.register_player_with_backend()
# ===================================================================


class TestRegisterPlayerWithBackend:
    """Tests for BackendOrchestrator.register_player_with_backend()."""

    def _make_player(self, *, enabled=True, mac="AA:BB:CC:DD:EE:01"):
        return Player.from_config({"player_name": "Kitchen", "mac": mac, "enabled": enabled})

    def _make_backend(self, mac="AA:BB:CC:DD:EE:01"):
        bt = _FakeBtManager(mac)
        return BluetoothA2dpBackend(bt)

    # 1. register_player_with_backend stores player + backend
    def test_register_player_with_backend(self):
        orch = BOrc()
        player = self._make_player()
        backend = self._make_backend()

        orch.register_player_with_backend(player, backend)

        assert orch.get_player(player.id) is player
        assert orch.get_backend(player.id) is backend

    # 2. state is INITIALIZING for enabled, DISABLED for disabled
    def test_register_player_with_backend_sets_state(self):
        orch = BOrc()

        enabled_player = self._make_player(enabled=True)
        enabled_backend = self._make_backend()
        orch.register_player_with_backend(enabled_player, enabled_backend)
        assert orch.get_player_state(enabled_player.id) == PlayerState.INITIALIZING

        disabled_player = self._make_player(enabled=False, mac="AA:BB:CC:DD:EE:02")
        disabled_backend = self._make_backend(mac="AA:BB:CC:DD:EE:02")
        orch.register_player_with_backend(disabled_player, disabled_backend)
        assert orch.get_player_state(disabled_player.id) == PlayerState.DISABLED

    # 3. duplicate player_id raises ValueError
    def test_register_player_with_backend_duplicate_raises(self):
        orch = BOrc()
        player = self._make_player()
        backend = self._make_backend()
        orch.register_player_with_backend(player, backend)

        with pytest.raises(ValueError, match="already registered"):
            orch.register_player_with_backend(player, self._make_backend())

    # 4. emits "player.registered" event
    def test_register_player_with_backend_emits_event(self):
        from services.event_store import EventStore

        store = EventStore()
        orch = BOrc(event_store=store)
        player = self._make_player()
        backend = self._make_backend()

        orch.register_player_with_backend(player, backend)

        events = store.query(event_types=["player.registered"])
        assert len(events) == 1
        assert events[0].subject_id == player.id
        assert events[0].payload["player_name"] == "Kitchen"


# ===================================================================
# Integration tests — initialize_devices() wires audio_backend
# ===================================================================


class TestInitializeDevicesBackend:
    """Integration tests for audio backend wiring in initialize_devices()."""

    def _run_init(self, *devices):
        orch = BridgeOrchestrator()
        bootstrap = _make_bootstrap(*devices)
        result = orch.initialize_devices(
            bootstrap,
            client_factory=_FakeClient,
            bt_manager_factory=_FakeBtManager,
        )
        return result

    # 5. clients have audio_backend set after init
    def test_initialize_devices_assigns_audio_backend(self):
        result = self._run_init(
            {"player_name": "Kitchen", "mac": "AA:BB:CC:DD:EE:01", "adapter": "hci0"},
        )
        client = result.clients[0]
        assert client.audio_backend is not None
        assert isinstance(client.audio_backend, BluetoothA2dpBackend)

    # 6. backend.bt_manager is the same object as client.bt_manager
    def test_audio_backend_wraps_same_bt_manager(self):
        result = self._run_init(
            {"player_name": "Kitchen", "mac": "AA:BB:CC:DD:EE:01", "adapter": "hci0"},
        )
        client = result.clients[0]
        assert client.audio_backend is not None
        # The backend wraps the same bt_manager instance
        assert client.audio_backend._bt_manager is client.bt_manager

    # 7. client.audio_destination delegates to backend
    def test_audio_destination_returns_backend_sink(self):
        """For stub clients, verify backend has get_audio_destination()."""
        result = self._run_init(
            {"player_name": "Kitchen", "mac": "AA:BB:CC:DD:EE:01", "adapter": "hci0"},
        )
        client = result.clients[0]
        backend = client.audio_backend
        assert backend is not None
        # No sink discovered yet → None
        assert backend.get_audio_destination() is None

    # 8. orchestrator tracks player after init
    def test_orchestrator_tracks_player_after_init(self):
        result = self._run_init(
            {"player_name": "Kitchen", "mac": "AA:BB:CC:DD:EE:01", "adapter": "hci0"},
        )
        client = result.clients[0]
        orch = state.get_backend_orchestrator()
        player = client._player
        assert player is not None
        assert orch.get_player(player.id) is player
        assert orch.get_backend(player.id) is client.audio_backend

    # 9. client without BT has no audio_backend
    def test_no_backend_without_bt_manager(self):
        result = self._run_init(
            {"player_name": "Aux-In", "mac": "", "adapter": ""},
        )
        client = result.clients[0]
        assert client.audio_backend is None
