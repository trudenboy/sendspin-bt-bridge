"""Tests for player-registry extensions on DeviceRegistrySnapshot (V3-1)."""

from __future__ import annotations

from types import SimpleNamespace

from services.audio_backend import BackendType
from services.device_registry import DeviceRegistrySnapshot, build_device_registry_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bt_client(player_id: str, player_name: str, mac: str, backend_type: BackendType = BackendType.BLUETOOTH_A2DP):
    """Create a fake client with player_id, player attribute, and bt_manager."""
    player = SimpleNamespace(backend_type=backend_type)
    return SimpleNamespace(
        player_id=player_id,
        player_name=player_name,
        player=player,
        bt_manager=SimpleNamespace(mac_address=mac),
        bt_management_enabled=True,
    )


# ---------------------------------------------------------------------------
# find_client_by_player_id
# ---------------------------------------------------------------------------


class TestFindClientByPlayerId:
    def test_returns_correct_client(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        c2 = _bt_client("pid-2", "Bedroom", "11:22:33:44:55:66")
        snap = DeviceRegistrySnapshot(active_clients=[c1, c2])

        assert snap.find_client_by_player_id("pid-1") is c1
        assert snap.find_client_by_player_id("pid-2") is c2

    def test_returns_none_for_unknown_id(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = DeviceRegistrySnapshot(active_clients=[c1])

        assert snap.find_client_by_player_id("no-such-id") is None

    def test_returns_none_for_none_input(self):
        snap = DeviceRegistrySnapshot(active_clients=[_bt_client("pid-1", "K", "AA:BB:CC:DD:EE:FF")])
        assert snap.find_client_by_player_id(None) is None

    def test_returns_none_for_empty_string(self):
        snap = DeviceRegistrySnapshot(active_clients=[_bt_client("pid-1", "K", "AA:BB:CC:DD:EE:FF")])
        assert snap.find_client_by_player_id("") is None

    def test_skips_clients_without_player_id_attr(self):
        bare = SimpleNamespace(player_name="NoPid")
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = DeviceRegistrySnapshot(active_clients=[bare, c1])

        assert snap.find_client_by_player_id("pid-1") is c1


# ---------------------------------------------------------------------------
# client_map_by_player_id
# ---------------------------------------------------------------------------


class TestClientMapByPlayerId:
    def test_maps_player_id_to_client(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        c2 = _bt_client("pid-2", "Bedroom", "11:22:33:44:55:66")
        snap = DeviceRegistrySnapshot(active_clients=[c1, c2])

        result = snap.client_map_by_player_id()
        assert result == {"pid-1": c1, "pid-2": c2}

    def test_omits_clients_without_player_id(self):
        bare = SimpleNamespace(player_name="NoPid")
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = DeviceRegistrySnapshot(active_clients=[bare, c1])

        assert snap.client_map_by_player_id() == {"pid-1": c1}

    def test_empty_snapshot_returns_empty_dict(self):
        snap = DeviceRegistrySnapshot()
        assert snap.client_map_by_player_id() == {}


# ---------------------------------------------------------------------------
# find_clients_by_backend_type
# ---------------------------------------------------------------------------


class TestFindClientsByBackendType:
    def test_returns_matching_clients(self):
        c_bt = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF", BackendType.BLUETOOTH_A2DP)
        c_local = _bt_client("pid-2", "Local", "11:22:33:44:55:66", BackendType.LOCAL_SINK)
        snap = DeviceRegistrySnapshot(active_clients=[c_bt, c_local])

        result = snap.find_clients_by_backend_type("bluetooth_a2dp")
        assert result == [c_bt]

    def test_returns_all_matching_clients(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF", BackendType.BLUETOOTH_A2DP)
        c2 = _bt_client("pid-2", "Bedroom", "11:22:33:44:55:66", BackendType.BLUETOOTH_A2DP)
        snap = DeviceRegistrySnapshot(active_clients=[c1, c2])

        result = snap.find_clients_by_backend_type("bluetooth_a2dp")
        assert result == [c1, c2]

    def test_returns_empty_for_no_match(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF", BackendType.BLUETOOTH_A2DP)
        snap = DeviceRegistrySnapshot(active_clients=[c1])

        assert snap.find_clients_by_backend_type("snapcast") == []

    def test_skips_clients_without_player_attr(self):
        bare = SimpleNamespace(player_name="NoPid")
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF", BackendType.BLUETOOTH_A2DP)
        snap = DeviceRegistrySnapshot(active_clients=[bare, c1])

        result = snap.find_clients_by_backend_type("bluetooth_a2dp")
        assert result == [c1]

    def test_empty_snapshot_returns_empty_list(self):
        snap = DeviceRegistrySnapshot()
        assert snap.find_clients_by_backend_type("bluetooth_a2dp") == []


# ---------------------------------------------------------------------------
# Backward compatibility — old methods still work after new ones added
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_find_client_by_player_name_still_works(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = DeviceRegistrySnapshot(active_clients=[c1])

        assert snap.find_client_by_player_name("Kitchen") is c1
        assert snap.find_client_by_player_name("Missing") is None

    def test_find_client_by_mac_still_works(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = DeviceRegistrySnapshot(active_clients=[c1])

        assert snap.find_client_by_mac("AA:BB:CC:DD:EE:FF") is c1
        assert snap.find_client_by_mac("FF:FF:FF:FF:FF:FF") is None

    def test_client_map_by_player_name_still_works(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = DeviceRegistrySnapshot(active_clients=[c1])

        assert snap.client_map_by_player_name() == {"Kitchen": c1}

    def test_client_map_by_mac_still_works(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = DeviceRegistrySnapshot(active_clients=[c1])

        assert snap.client_map_by_mac() == {"AA:BB:CC:DD:EE:FF": c1}

    def test_released_clients_still_works(self):
        released = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        released.bt_management_enabled = False
        snap = DeviceRegistrySnapshot(active_clients=[released])

        assert snap.released_clients() == [released]

    def test_snapshot_includes_backend_type_when_player_present(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF", BackendType.LOCAL_SINK)
        _snap = DeviceRegistrySnapshot(active_clients=[c1])

        assert c1.player.backend_type == BackendType.LOCAL_SINK

    def test_build_helper_works_with_new_methods(self):
        c1 = _bt_client("pid-1", "Kitchen", "AA:BB:CC:DD:EE:FF")
        snap = build_device_registry_snapshot(active_clients=[c1])

        assert snap.find_client_by_player_id("pid-1") is c1
        assert snap.client_map_by_player_id() == {"pid-1": c1}
        assert snap.find_clients_by_backend_type("bluetooth_a2dp") == [c1]
