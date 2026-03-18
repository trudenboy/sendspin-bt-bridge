from __future__ import annotations

from types import SimpleNamespace

import state
from services.device_registry import (
    build_device_registry_snapshot,
    get_device_registry_snapshot,
)


def test_build_device_registry_snapshot_uses_state_surfaces():
    client = SimpleNamespace(player_name="Kitchen", bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"))
    state.set_clients([client])
    state.set_disabled_devices([{"player_name": "Bedroom", "enabled": False}])
    try:
        snapshot = get_device_registry_snapshot()

        assert snapshot.active_clients == [client]
        assert snapshot.disabled_devices == [{"player_name": "Bedroom", "enabled": False}]
    finally:
        state.set_clients([])
        state.set_disabled_devices([])


def test_device_registry_snapshot_indexes_active_clients():
    kitchen = SimpleNamespace(player_name="Kitchen", bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"))
    unnamed = SimpleNamespace(player_name="", bt_manager=SimpleNamespace(mac_address=None))
    bedroom = SimpleNamespace(player_name="Bedroom", bt_manager=SimpleNamespace(mac_address="11:22:33:44:55:66"))

    snapshot = build_device_registry_snapshot(active_clients=[kitchen, unnamed, bedroom], disabled_devices=[])

    assert snapshot.find_client_by_player_name("Kitchen") is kitchen
    assert snapshot.find_client_by_player_name("Missing") is None
    assert snapshot.client_map_by_player_name() == {"Kitchen": kitchen, "Bedroom": bedroom}
    assert snapshot.client_map_by_mac() == {
        "AA:BB:CC:DD:EE:FF": kitchen,
        "11:22:33:44:55:66": bedroom,
    }


def test_device_registry_snapshot_copies_inputs():
    active_clients = [SimpleNamespace(player_name="Kitchen")]
    disabled_devices = [{"player_name": "Bedroom", "enabled": False}]

    snapshot = build_device_registry_snapshot(active_clients=active_clients, disabled_devices=disabled_devices)
    active_clients.clear()
    disabled_devices.clear()

    assert len(snapshot.active_clients) == 1
    assert snapshot.disabled_devices == [{"player_name": "Bedroom", "enabled": False}]
