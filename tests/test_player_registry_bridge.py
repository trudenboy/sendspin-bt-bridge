"""Tests for Player model integration into device initialization.

Verifies that bridge_orchestrator.initialize_devices() creates a typed Player
instance from each device config and stores it on the SendspinClient.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import config
import state
from bridge_orchestrator import BridgeOrchestrator
from sendspin_client import SendspinClient
from services.player_model import Player, _player_id_from_mac

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


# ---------------------------------------------------------------------------
# 1. SendspinClient has player property, defaults to None
# ---------------------------------------------------------------------------


def test_client_has_player_property():
    client = SendspinClient("Test Player", "localhost", 9000)
    assert hasattr(client, "player")
    assert client.player is None


# ---------------------------------------------------------------------------
# 2. After setting _player, the player property returns it
# ---------------------------------------------------------------------------


def test_player_assigned_from_config():
    client = SendspinClient("Test Player", "localhost", 9000)
    player = Player.from_config({"player_name": "Test Player", "mac": "AA:BB:CC:DD:EE:FF"})
    client._player = player
    assert client.player is player
    assert client.player.player_name == "Test Player"


# ---------------------------------------------------------------------------
# 3. Player.from_config() creates correct Player from BLUETOOTH_DEVICES entry
# ---------------------------------------------------------------------------


def test_player_from_device_config():
    cfg = {
        "player_name": "Kitchen",
        "mac": "AA:BB:CC:DD:EE:01",
        "adapter": "hci0",
        "enabled": True,
        "listen_port": 8928,
    }
    player = Player.from_config(cfg)
    assert player.player_name == "Kitchen"
    assert player.mac == "AA:BB:CC:DD:EE:01"
    assert player.adapter == "hci0"
    assert player.enabled is True
    assert player.listen_port == 8928


# ---------------------------------------------------------------------------
# 4. Player.id matches client.player_id for same MAC
# ---------------------------------------------------------------------------


def test_player_id_matches_client_id():
    mac = "AA:BB:CC:DD:EE:01"
    cfg = {"player_name": "Kitchen", "mac": mac}
    player = Player.from_config(cfg)

    from unittest.mock import MagicMock

    bt_mgr = MagicMock()
    bt_mgr.mac_address = mac
    bt_mgr.check_bluetooth_available.return_value = False
    client = SendspinClient("Kitchen", "localhost", 9000, bt_manager=bt_mgr)

    assert player.id == client.player_id
    assert player.id == _player_id_from_mac(mac.lower())


# ---------------------------------------------------------------------------
# 5. Player.mac matches bt_manager.mac_address
# ---------------------------------------------------------------------------


def test_player_mac_matches_bt_manager_mac():
    mac = "FC:58:FA:EB:08:6C"
    cfg = {"player_name": "ENEBY20", "mac": mac, "adapter": "hci0"}
    player = Player.from_config(cfg)

    from unittest.mock import MagicMock

    bt_mgr = MagicMock()
    bt_mgr.mac_address = mac
    bt_mgr.check_bluetooth_available.return_value = True
    client = SendspinClient("ENEBY20", "localhost", 9000, bt_manager=bt_mgr)
    client._player = player

    assert client.player.mac == client.bt_manager.mac_address


# ---------------------------------------------------------------------------
# 6. Integration: initialize_devices() sets _player on each client
# ---------------------------------------------------------------------------


class _FakeClient:
    """Minimal client stub matching the contract of SendspinClient."""

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
        self._player = None  # must accept Player assignment

    def _update_status(self, updates):
        self.status.update(updates)

    def set_bt_management_enabled(self, enabled):
        self.bt_management_enabled = enabled


class _FakeBtManager:
    def __init__(self, mac_address, **kwargs):
        self.mac_address = mac_address
        self.kwargs = kwargs
        self.on_sink_found = kwargs.get("on_sink_found")

    def check_bluetooth_available(self):
        return True


def test_initialize_devices_creates_player():
    orchestrator = BridgeOrchestrator()

    bootstrap = SimpleNamespace(
        device_configs=[
            {
                "player_name": "Kitchen",
                "mac": "AA:BB:CC:DD:EE:01",
                "adapter": "hci0",
            },
            {
                "player_name": "Bedroom",
                "mac": "AA:BB:CC:DD:EE:02",
                "adapter": "hci1",
            },
        ],
        server_host="music-assistant.local",
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

    result = orchestrator.initialize_devices(
        bootstrap,
        client_factory=_FakeClient,
        bt_manager_factory=_FakeBtManager,
    )

    assert len(result.clients) == 2
    for client in result.clients:
        assert client._player is not None, f"_player not set on client '{client.player_name}'"
        assert isinstance(client._player, Player)
        assert client._player.player_name == client.player_name


def test_initialize_devices_player_has_correct_mac():
    orchestrator = BridgeOrchestrator()

    bootstrap = SimpleNamespace(
        device_configs=[
            {
                "player_name": "Kitchen",
                "mac": "AA:BB:CC:DD:EE:01",
                "adapter": "hci0",
            },
        ],
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

    result = orchestrator.initialize_devices(
        bootstrap,
        client_factory=_FakeClient,
        bt_manager_factory=_FakeBtManager,
    )

    client = result.clients[0]
    assert client._player.mac == "AA:BB:CC:DD:EE:01"
    assert client._player.adapter == "hci0"


def test_initialize_devices_skips_player_for_disabled_devices():
    orchestrator = BridgeOrchestrator()

    bootstrap = SimpleNamespace(
        device_configs=[
            {
                "player_name": "Kitchen",
                "mac": "AA:BB:CC:DD:EE:01",
                "adapter": "hci0",
                "enabled": False,
            },
        ],
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

    result = orchestrator.initialize_devices(
        bootstrap,
        client_factory=_FakeClient,
        bt_manager_factory=_FakeBtManager,
    )

    assert len(result.clients) == 0
    assert len(result.disabled_devices) == 1


def test_initialize_devices_player_creation_failure_does_not_break_init(monkeypatch):
    """If Player.from_config() raises, the client is still created and appended."""
    orchestrator = BridgeOrchestrator()

    bootstrap = SimpleNamespace(
        device_configs=[
            {
                "player_name": "Kitchen",
                "mac": "AA:BB:CC:DD:EE:01",
                "adapter": "hci0",
            },
        ],
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

    from services.player_model import Player as _PlayerModule

    monkeypatch.setattr(
        _PlayerModule,
        "from_config",
        classmethod(lambda cls, cfg: (_ for _ in ()).throw(ValueError("boom"))),
    )

    result = orchestrator.initialize_devices(
        bootstrap,
        client_factory=_FakeClient,
        bt_manager_factory=_FakeBtManager,
    )

    assert len(result.clients) == 1
    assert result.clients[0]._player is None
