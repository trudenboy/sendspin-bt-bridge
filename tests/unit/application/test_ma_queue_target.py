"""Which Music Assistant queue a command from the UI should reach."""

from types import SimpleNamespace

import sendspin_bridge.application.music_assistant.playback as ma_playback
import sendspin_bridge.bridge.state as state
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot


def test_resolve_target_queue_uses_player_id_queue_for_solo_sendspin_player():

    state.set_ma_groups({}, [])
    try:
        state_key, queue_id = ma_playback._resolve_target_queue(
            "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798",
            "sendspin-yandex-mini-2---lxc",
            "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798",
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "sendspin-yandex-mini-2---lxc"
    assert queue_id == "sendspin-yandex-mini-2---lxc"


def test_resolve_target_queue_uses_ma_group_mapping_for_grouped_player():

    state.set_ma_groups(
        {"sendspin-yandex-mini-2---lxc": {"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT"}},
        [{"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT", "members": []}],
    )
    try:
        state_key, queue_id = ma_playback._resolve_target_queue(
            None,
            "sendspin-yandex-mini-2---lxc",
            None,
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "syncgroup_5zr8ss8g"
    assert queue_id == "syncgroup_5zr8ss8g"


def test_resolve_target_queue_ignores_stale_syncgroup_id_for_solo_player():

    state.set_ma_groups(
        {},
        [
            {
                "id": "syncgroup_5zr8ss8g",
                "name": "Semdspin BT",
                "members": [{"id": "upsendspinlencols500haos", "name": "Lenco LS-500 @ HAOS"}],
            }
        ],
    )
    try:
        state_key, queue_id = ma_playback._resolve_target_queue(
            "syncgroup_5zr8ss8g",
            "sendspin-yandex-mini-2---lxc",
            None,
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "sendspin-yandex-mini-2---lxc"
    assert queue_id == "sendspin-yandex-mini-2---lxc"


def test_resolve_target_queue_infers_single_active_player_for_stale_page(monkeypatch):

    fake_client = SimpleNamespace(
        player_id="sendspin-yandex-mini-2---lxc",
        status={"server_connected": True},
        is_running=lambda: True,
    )

    state.set_ma_groups(
        {},
        [
            {
                "id": "syncgroup_5zr8ss8g",
                "name": "Semdspin BT",
                "members": [{"id": "upsendspinlencols500haos", "name": "Lenco LS-500 @ HAOS"}],
            }
        ],
    )
    monkeypatch.setattr(
        ma_playback, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[fake_client])
    )
    try:
        state_key, queue_id = ma_playback._resolve_target_queue("syncgroup_5zr8ss8g", None, None)
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "sendspin-yandex-mini-2---lxc"
    assert queue_id == "sendspin-yandex-mini-2---lxc"


def test_resolve_target_queue_keeps_legacy_universal_queue_for_uuid_player_id():

    state.set_ma_groups({}, [])
    try:
        state_key, queue_id = ma_playback._resolve_target_queue(
            None,
            "d3002d0d-db47-51e2-b3a2-00f79b7fc683",
            None,
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "d3002d0d-db47-51e2-b3a2-00f79b7fc683"
    assert queue_id == "upd3002d0ddb4751e2b3a200f79b7fc683"
