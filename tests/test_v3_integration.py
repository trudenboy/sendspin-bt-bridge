"""V3-1 end-to-end integration tests.

Verify that the V3-1 components (AudioBackend, Player, BackendOrchestrator,
EventStore, config migration, config validation, DeviceRegistry,
SendspinClient) work together as a system.
"""

from __future__ import annotations

import json

import pytest

from config import (
    load_config,
    migrate_config_payload,
    write_config_file,
)
from services.audio_backend import BackendType
from services.backend_orchestrator import BackendOrchestrator
from services.backends import create_backend
from services.backends.mock_backend import MockAudioBackend
from services.config_validation import validate_uploaded_config
from services.device_registry import DeviceRegistrySnapshot
from services.event_store import EventStore
from services.internal_events import InternalEventPublisher
from services.player_model import Player, PlayerState, _player_id_from_mac

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

V1_DEVICES = [
    {
        "mac": "AA:BB:CC:DD:EE:FF",
        "player_name": "Kitchen Speaker",
        "adapter": "hci0",
        "listen_port": 8928,
        "delay_ms": -600,
        "enabled": True,
        "handoff_mode": "default",
        "volume_controller": "pa",
        "keepalive_enabled": False,
        "keepalive_interval": 30,
        "idle_disconnect_minutes": 0,
        "room_id": "kitchen",
        "room_name": "Kitchen",
    },
    {
        "mac": "11:22:33:44:55:66",
        "player_name": "Bedroom Speaker",
        "adapter": "hci1",
        "listen_port": 8929,
        "delay_ms": -300,
        "enabled": True,
    },
]


def _v1_config(**overrides: object) -> dict:
    """Return a minimal v1 config dict with BLUETOOTH_DEVICES."""
    cfg: dict = {
        "SENDSPIN_SERVER": "192.168.1.100",
        "SENDSPIN_PORT": 9000,
        "BLUETOOTH_DEVICES": list(V1_DEVICES),
        "LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 75, "11:22:33:44:55:66": 50},
    }
    cfg.update(overrides)
    return cfg


def _v2_player(
    name: str = "Test Speaker",
    mac: str = "AA:BB:CC:DD:EE:FF",
    adapter: str = "hci0",
    **kwargs: object,
) -> dict:
    """Return a v2 players[] entry."""
    entry: dict = {
        "player_name": name,
        "backend": {"type": "bluetooth_a2dp", "mac": mac, "adapter": adapter},
        "enabled": True,
        "listen_port": 8928,
    }
    entry.update(kwargs)
    return entry


def _make_player(
    player_id: str = "test-player-1",
    name: str = "Test Speaker",
    enabled: bool = True,
    mac: str = "AA:BB:CC:DD:EE:FF",
) -> Player:
    return Player(
        id=player_id,
        player_name=name,
        backend_type=BackendType.BLUETOOTH_A2DP,
        backend_config={"mac": mac, "adapter": "hci0"},
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def event_store():
    return EventStore()


@pytest.fixture()
def publisher():
    return InternalEventPublisher()


@pytest.fixture()
def orchestrator(event_store, publisher):
    return BackendOrchestrator(event_store=event_store, event_publisher=publisher)


# ===================================================================
# Scenario 1: Full config lifecycle  (v1 → v2 → persist → reload)
# ===================================================================


class TestConfigV1ToV2FullLifecycle:
    """Load v1 config, verify migration, save, reload, verify consistency."""

    def test_migration_creates_players(self, tmp_config):
        """v1 BLUETOOTH_DEVICES are migrated to players[]."""
        v1 = _v1_config()
        tmp_config.write_text(json.dumps(v1))

        config = load_config()

        players = config.get("players", [])
        assert len(players) == 2
        for p in players:
            assert "backend" in p
            assert p["backend"]["type"] == "bluetooth_a2dp"
            assert p["backend"]["mac"]  # non-empty

    def test_migration_preserves_device_fields(self, tmp_config):
        """Migrated players carry over all v1 device fields."""
        v1 = _v1_config()
        tmp_config.write_text(json.dumps(v1))

        config = load_config()
        kitchen = next(p for p in config["players"] if p["player_name"] == "Kitchen Speaker")

        assert kitchen["backend"]["mac"] == "AA:BB:CC:DD:EE:FF"
        assert kitchen["backend"]["adapter"] == "hci0"
        assert kitchen["listen_port"] == 8928
        assert kitchen["static_delay_ms"] == -600
        assert kitchen["enabled"] is True
        assert kitchen["handoff_mode"] == "default"
        assert kitchen["room_id"] == "kitchen"
        assert kitchen["room_name"] == "Kitchen"

    def test_persist_and_reload_consistency(self, tmp_config):
        """Save migrated config, reload it — result must be consistent."""
        v1 = _v1_config()
        tmp_config.write_text(json.dumps(v1))

        first_load = load_config()
        players_first = first_load["players"]

        # Persist the migrated config
        import config as _cfg

        write_config_file(first_load, config_file=_cfg.CONFIG_FILE, config_dir=_cfg.CONFIG_DIR)

        second_load = load_config()
        players_second = second_load["players"]

        assert len(players_first) == len(players_second)
        for p1, p2 in zip(players_first, players_second, strict=False):
            assert p1["player_name"] == p2["player_name"]
            assert p1["backend"]["mac"] == p2["backend"]["mac"]
            assert p1["id"] == p2["id"]

    def test_bluetooth_devices_and_players_coexist(self, tmp_config):
        """After migration both BLUETOOTH_DEVICES and players[] are present."""
        v1 = _v1_config()
        tmp_config.write_text(json.dumps(v1))

        config = load_config()

        assert len(config["BLUETOOTH_DEVICES"]) == 2
        assert len(config["players"]) == 2

    def test_volume_store_migrated_to_player_ids(self, tmp_config):
        """Migration adds player-id keys to LAST_VOLUMES in the persisted file."""
        v1 = _v1_config()
        tmp_config.write_text(json.dumps(v1))

        # migrate_config_payload adds player_id keys alongside MAC keys
        result = migrate_config_payload(v1)
        volumes = result.normalized_config.get("LAST_VOLUMES", {})

        pid = _player_id_from_mac("AA:BB:CC:DD:EE:FF")
        assert pid in volumes
        assert volumes[pid] == 75
        # Original MAC key also preserved
        assert "AA:BB:CC:DD:EE:FF" in volumes


# ===================================================================
# Scenario 2: Backend orchestrator full lifecycle with mock
# ===================================================================


class TestOrchestratorFullLifecycle:
    """Register players, connect, set states, disconnect, unregister."""

    def test_full_lifecycle(self, orchestrator, event_store):
        p1 = _make_player("p1", "Speaker 1", enabled=True, mac="AA:BB:CC:DD:EE:01")
        p2 = _make_player("p2", "Speaker 2", enabled=True, mac="AA:BB:CC:DD:EE:02")
        p3 = _make_player("p3", "Speaker 3", enabled=False, mac="AA:BB:CC:DD:EE:03")

        # Register all three (one disabled)
        orchestrator.register_player(p1, backend_type_override="mock")
        orchestrator.register_player(p2, backend_type_override="mock")
        orchestrator.register_player(p3, backend_type_override="mock")

        assert orchestrator.player_count == 3
        assert orchestrator.get_player_state("p1") == PlayerState.INITIALIZING
        assert orchestrator.get_player_state("p3") == PlayerState.DISABLED

        # Connect enabled players
        assert orchestrator.connect_player("p1") is True
        assert orchestrator.connect_player("p2") is True
        assert orchestrator.get_player_state("p1") == PlayerState.READY
        assert orchestrator.get_player_state("p2") == PlayerState.READY
        assert orchestrator.connected_count == 2

        # Set one to STREAMING
        orchestrator.set_player_state("p1", PlayerState.STREAMING)
        assert orchestrator.get_player_state("p1") == PlayerState.STREAMING
        assert orchestrator.connected_count == 2  # READY + STREAMING

        # Disconnect all
        orchestrator.disconnect_player("p1")
        orchestrator.disconnect_player("p2")
        assert orchestrator.get_player_state("p1") == PlayerState.OFFLINE
        assert orchestrator.get_player_state("p2") == PlayerState.OFFLINE
        assert orchestrator.connected_count == 0

        # Verify events were recorded
        events = event_store.query()
        assert len(events) > 0
        player_ids_in_events = {e.subject_id for e in events}
        assert "p1" in player_ids_in_events
        assert "p2" in player_ids_in_events

    def test_unregister_disconnects_first(self, orchestrator):
        player = _make_player("u1", "Unregister Me")
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.connect_player("u1")
        assert orchestrator.connected_count == 1

        orchestrator.unregister_player("u1")

        assert orchestrator.get_player_state("u1") is None
        assert orchestrator.get_backend("u1") is None
        assert orchestrator.player_count == 0


# ===================================================================
# Scenario 3: Player model round-trip (config → Player → config)
# ===================================================================


class TestPlayerModelRoundtrip:
    """Player.from_config() → to_dict() and config format roundtrips."""

    def test_v1_from_config_preserves_fields(self):
        v1_cfg = V1_DEVICES[0]
        player = Player.from_config(v1_cfg)

        assert player.player_name == "Kitchen Speaker"
        assert player.backend_type == BackendType.BLUETOOTH_A2DP
        assert player.mac == "AA:BB:CC:DD:EE:FF"

        d = player.to_dict()
        assert d["player_name"] == "Kitchen Speaker"
        assert d["backend_type"] in ("bluetooth_a2dp", BackendType.BLUETOOTH_A2DP)
        assert d["backend_config"]["mac"] == "AA:BB:CC:DD:EE:FF"
        assert d["id"] == player.id

    def test_v2_from_config_preserves_fields(self):
        v2_cfg = {
            "id": "custom-id",
            "player_name": "Bedroom",
            "backend": {
                "type": "bluetooth_a2dp",
                "mac": "11:22:33:44:55:66",
                "adapter": "hci1",
            },
            "listen_port": 8929,
            "static_delay_ms": -300,
            "enabled": True,
        }
        player = Player.from_config(v2_cfg)

        assert player.id == "custom-id"
        assert player.player_name == "Bedroom"
        assert player.mac == "11:22:33:44:55:66"
        assert player.static_delay_ms == -300

        d = player.to_dict()
        assert d["id"] == "custom-id"
        assert d["backend_config"]["mac"] == "11:22:33:44:55:66"
        assert d["static_delay_ms"] == -300

    def test_v2_config_roundtrip(self):
        """v2 config dict → from_config() → v2 config dict → from_config() is stable."""
        v2_cfg = _v2_player("Roundtrip Speaker", "AA:BB:CC:DD:EE:FF", "hci0")
        p1 = Player.from_config(v2_cfg)

        # Rebuild v2 config from the Player (manual, matching the v2 format)
        rebuilt_cfg = {
            "id": p1.id,
            "player_name": p1.player_name,
            "backend": {
                "type": p1.backend_type.value,
                **p1.backend_config,
            },
            "enabled": p1.enabled,
            "listen_port": p1.listen_port,
        }
        p2 = Player.from_config(rebuilt_cfg)

        assert p2.player_name == p1.player_name
        assert p2.mac == p1.mac
        assert p2.id == p1.id
        assert p2.backend_type == p1.backend_type

    def test_to_dict_contains_all_essential_fields(self):
        """to_dict() output has all fields needed for API responses."""
        player = _make_player("td-1", "Dict Speaker")
        d = player.to_dict()

        assert "id" in d
        assert "player_name" in d
        assert "backend_type" in d
        assert "backend_config" in d
        assert "enabled" in d
        assert "listen_port" in d


# ===================================================================
# Scenario 4: Factory + orchestrator integration
# ===================================================================


class TestFactoryOrchestratorIntegration:
    """create_backend() works correctly when called via BackendOrchestrator."""

    def test_mock_factory_via_orchestrator(self, orchestrator):
        player = _make_player("f1", "Factory Speaker")
        orchestrator.register_player(player, backend_type_override="mock")

        backend = orchestrator.get_backend("f1")
        assert backend is not None
        assert isinstance(backend, MockAudioBackend)

    def test_connect_logs_in_call_log(self, orchestrator):
        player = _make_player("f2", "Log Speaker")
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.connect_player("f2")

        backend = orchestrator.get_backend("f2")
        assert "connect" in backend.call_log

    def test_disconnect_logs_in_call_log(self, orchestrator):
        player = _make_player("f3", "Disconnect Speaker")
        orchestrator.register_player(player, backend_type_override="mock")
        orchestrator.connect_player("f3")
        orchestrator.disconnect_player("f3")

        backend = orchestrator.get_backend("f3")
        assert "connect" in backend.call_log
        assert "disconnect" in backend.call_log

    def test_factory_standalone_mock(self):
        """create_backend('mock') returns a working MockAudioBackend."""
        backend = create_backend("mock", backend_id="standalone")
        assert isinstance(backend, MockAudioBackend)
        assert backend.backend_id == "standalone"

        assert backend.connect() is True
        assert backend.is_ready() is True
        assert backend.get_audio_destination() == "mock_sink_standalone"

    def test_factory_mock_fail_connect(self):
        backend = create_backend("mock", backend_id="fail", fail_connect=True)
        assert backend.connect() is False
        assert backend.is_ready() is False


# ===================================================================
# Scenario 5: Event store captures orchestrator events
# ===================================================================


class TestEventStoreCapturesLifecycle:
    """Events from orchestrator state changes appear in EventStore."""

    def test_register_connect_disconnect_events(self, event_store, publisher):
        orch = BackendOrchestrator(event_store=event_store, event_publisher=publisher)
        player = _make_player("ev1", "Event Speaker")

        orch.register_player(player, backend_type_override="mock")
        orch.connect_player("ev1")
        orch.disconnect_player("ev1")

        events = event_store.query(player_id="ev1")
        assert len(events) >= 3  # registered + state changes (connecting→ready→offline)

        event_types = [e.event_type for e in events]
        assert "player.registered" in event_types

    def test_event_order_is_chronological(self, event_store, publisher):
        orch = BackendOrchestrator(event_store=event_store, event_publisher=publisher)
        player = _make_player("ev2", "Order Speaker")

        orch.register_player(player, backend_type_override="mock")
        orch.connect_player("ev2")
        orch.set_player_state("ev2", PlayerState.STREAMING)
        orch.disconnect_player("ev2")

        events = event_store.query(player_id="ev2")
        timestamps = [e.at for e in events]
        assert timestamps == sorted(timestamps)

    def test_event_types_filter(self, event_store, publisher):
        orch = BackendOrchestrator(event_store=event_store, event_publisher=publisher)
        player = _make_player("ev3", "Filter Speaker")

        orch.register_player(player, backend_type_override="mock")
        orch.connect_player("ev3")
        orch.disconnect_player("ev3")

        registered = event_store.query(
            player_id="ev3",
            event_types=["player.registered"],
        )
        assert len(registered) == 1
        assert registered[0].event_type == "player.registered"

    def test_stats_reflect_recorded_events(self, event_store, publisher):
        orch = BackendOrchestrator(event_store=event_store, event_publisher=publisher)
        p1 = _make_player("s1", "Stats 1")
        p2 = _make_player("s2", "Stats 2")

        orch.register_player(p1, backend_type_override="mock")
        orch.register_player(p2, backend_type_override="mock")
        orch.connect_player("s1")

        stats = event_store.stats()
        assert stats.total_events > 0
        assert "s1" in stats.player_counts
        assert "s2" in stats.player_counts


# ===================================================================
# Scenario 6: Config validation accepts v2 player entries
# ===================================================================


class TestConfigValidationV2Players:
    """Upload config with players[] passes validation."""

    def test_valid_v2_config(self):
        cfg = {
            "CONFIG_SCHEMA_VERSION": 2,
            "SENDSPIN_PORT": 9000,
            "players": [
                _v2_player("Kitchen", "AA:BB:CC:DD:EE:FF"),
                _v2_player("Bedroom", "11:22:33:44:55:66", listen_port=8929),
            ],
        }
        result = validate_uploaded_config(cfg)
        assert result.is_valid, f"Validation errors: {[e.message for e in result.errors]}"

    def test_valid_v2_mixed_backends(self):
        """Config with different known backend types passes."""
        cfg = {
            "CONFIG_SCHEMA_VERSION": 2,
            "SENDSPIN_PORT": 9000,
            "players": [
                _v2_player("BT Speaker", "AA:BB:CC:DD:EE:FF"),
                {
                    "player_name": "Local Speaker",
                    "backend": {"type": "local_sink"},
                    "listen_port": 8929,
                },
            ],
        }
        result = validate_uploaded_config(cfg)
        assert result.is_valid, f"Validation errors: {[e.message for e in result.errors]}"

    def test_invalid_missing_player_name(self):
        cfg = {
            "CONFIG_SCHEMA_VERSION": 2,
            "players": [
                {
                    "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:FF"},
                },
            ],
        }
        result = validate_uploaded_config(cfg)
        assert not result.is_valid
        error_fields = [e.field for e in result.errors]
        assert any("player_name" in f for f in error_fields)

    def test_invalid_bad_backend_type(self):
        cfg = {
            "CONFIG_SCHEMA_VERSION": 2,
            "players": [
                {
                    "player_name": "Bad Backend",
                    "backend": {"type": "nonexistent"},
                },
            ],
        }
        result = validate_uploaded_config(cfg)
        assert not result.is_valid
        error_fields = [e.field for e in result.errors]
        assert any("backend.type" in f for f in error_fields)

    def test_invalid_bluetooth_without_mac(self):
        """bluetooth_a2dp backend requires a MAC address."""
        cfg = {
            "CONFIG_SCHEMA_VERSION": 2,
            "players": [
                {
                    "player_name": "No MAC",
                    "backend": {"type": "bluetooth_a2dp"},
                },
            ],
        }
        result = validate_uploaded_config(cfg)
        assert not result.is_valid
        error_msgs = [e.message for e in result.errors]
        assert any("mac" in m.lower() for m in error_msgs)


# ===================================================================
# Scenario 7: Device registry + orchestrator consistency
# ===================================================================


class TestRegistryOrchestratorConsistency:
    """DeviceRegistry snapshot reflects orchestrator state."""

    def test_find_client_by_player_id(self, orchestrator):
        """Clients with player_id can be found in DeviceRegistrySnapshot."""
        _make_player("reg-1", "Registry Speaker 1")
        _make_player("reg-2", "Registry Speaker 2")

        # Simulate clients as simple objects with player_id attribute
        class FakeClient:
            def __init__(self, player_id: str, player_name: str):
                self.player_id = player_id
                self.player_name = player_name
                self.mac_address = ""
                self.bluetooth_mac = ""

        client1 = FakeClient("reg-1", "Registry Speaker 1")
        client2 = FakeClient("reg-2", "Registry Speaker 2")

        snap = DeviceRegistrySnapshot(active_clients=[client1, client2])
        assert snap.find_client_by_player_id("reg-1") is client1
        assert snap.find_client_by_player_id("reg-2") is client2
        assert snap.find_client_by_player_id("nonexistent") is None

    def test_client_map_by_player_id(self):
        class FakeClient:
            def __init__(self, player_id: str):
                self.player_id = player_id

        c1 = FakeClient("p1")
        c2 = FakeClient("p2")
        snap = DeviceRegistrySnapshot(active_clients=[c1, c2])

        id_map = snap.client_map_by_player_id()
        assert id_map["p1"] is c1
        assert id_map["p2"] is c2

    def test_orchestrator_state_matches_registry_clients(self, orchestrator):
        """Orchestrator player states are consistent with registered clients."""
        p1 = _make_player("oc-1", "Orch Client 1")
        p2 = _make_player("oc-2", "Orch Client 2")

        orchestrator.register_player(p1, backend_type_override="mock")
        orchestrator.register_player(p2, backend_type_override="mock")
        orchestrator.connect_player("oc-1")

        class FakeClient:
            def __init__(self, player_id: str, player_name: str):
                self.player_id = player_id
                self.player_name = player_name

        snap = DeviceRegistrySnapshot(
            active_clients=[
                FakeClient("oc-1", "Orch Client 1"),
                FakeClient("oc-2", "Orch Client 2"),
            ],
        )

        for client in snap.active_clients:
            state = orchestrator.get_player_state(client.player_id)
            assert state is not None

        assert orchestrator.get_player_state("oc-1") == PlayerState.READY
        assert orchestrator.get_player_state("oc-2") == PlayerState.INITIALIZING


# ===================================================================
# Scenario 8: SendspinClient with AudioBackend
# ===================================================================


class TestSendspinClientBackendIntegration:
    """SendspinClient uses AudioBackend for audio_destination."""

    def _make_client(self):
        from sendspin_client import SendspinClient

        return SendspinClient(
            "IntegrationTest",
            "localhost",
            9000,
            listen_port=8928,
        )

    def test_audio_destination_from_backend(self):
        backend = MockAudioBackend(backend_id="int-test")
        backend.connect()

        client = self._make_client()
        client.audio_backend = backend

        assert client.audio_destination == "mock_sink_int-test"

    def test_snapshot_includes_backend_info(self):
        backend = MockAudioBackend(backend_id="snap-test")
        backend.connect()

        client = self._make_client()
        client.audio_backend = backend

        snap = client.snapshot()
        assert snap["audio_backend"] is not None
        assert snap["audio_backend"]["backend_id"] == "snap-test"
        assert snap["audio_backend"]["connected"] is True
        assert snap["audio_destination"] == "mock_sink_snap-test"

    def test_backend_status_returns_dict(self):
        backend = MockAudioBackend(backend_id="stat-test")
        backend.connect()

        client = self._make_client()
        client.audio_backend = backend

        status = client.backend_status
        assert isinstance(status, dict)
        assert status["connected"] is True
        assert status["available"] is True

    def test_no_backend_destination_falls_back(self):
        """Without AudioBackend, audio_destination falls back to bluetooth_sink_name."""
        client = self._make_client()
        client.bluetooth_sink_name = "bluez_sink.AA_BB.a2dp_sink"

        assert client.audio_backend is None
        assert client.audio_destination == "bluez_sink.AA_BB.a2dp_sink"


# ===================================================================
# Scenario 9: Backward compatibility — no AudioBackend
# ===================================================================


class TestBackwardCompatNoBackend:
    """Everything works without any V3-1 features (v1 config, no backend)."""

    def test_v1_config_loads_without_error(self, tmp_config):
        v1 = _v1_config()
        tmp_config.write_text(json.dumps(v1))

        config = load_config()
        assert config["SENDSPIN_SERVER"] == "192.168.1.100"
        assert len(config["BLUETOOTH_DEVICES"]) == 2

    def test_client_without_audio_backend(self):
        from sendspin_client import SendspinClient

        client = SendspinClient(
            "BackCompatTest",
            "localhost",
            9000,
            listen_port=8928,
        )
        assert client.audio_backend is None

    def test_bluetooth_sink_name_still_works(self):
        from sendspin_client import SendspinClient

        client = SendspinClient(
            "SinkTest",
            "localhost",
            9000,
            listen_port=8928,
        )
        client.bluetooth_sink_name = "bluez_sink.FC_58_FA.a2dp_sink"
        assert client.bluetooth_sink_name == "bluez_sink.FC_58_FA.a2dp_sink"
        assert client.audio_destination == "bluez_sink.FC_58_FA.a2dp_sink"

    def test_snapshot_without_backend(self):
        from sendspin_client import SendspinClient

        client = SendspinClient(
            "SnapTest",
            "localhost",
            9000,
            listen_port=8928,
        )
        snap = client.snapshot()
        assert snap["audio_backend"] is None
        assert snap["player_id"]  # player_id is always set
        assert snap["bluetooth_sink_name"] is None

    def test_migrate_result_for_v1_without_players(self):
        """migrate_config_payload on v1 config generates players[]."""
        v1 = _v1_config()
        result = migrate_config_payload(v1)

        assert result.needs_persist is True
        assert len(result.normalized_config.get("players", [])) == 2

    def test_migrate_idempotent_for_v2(self):
        """migrate_config_payload on already-v2 config does not re-migrate."""
        v2 = {
            "CONFIG_SCHEMA_VERSION": 2,
            "SENDSPIN_PORT": 9000,
            "BLUETOOTH_DEVICES": [],
            "players": [_v2_player("Speaker", "AA:BB:CC:DD:EE:FF")],
        }
        result = migrate_config_payload(v2)

        assert len(result.normalized_config.get("players", [])) == 1
        assert result.normalized_config["players"][0]["player_name"] == "Speaker"
