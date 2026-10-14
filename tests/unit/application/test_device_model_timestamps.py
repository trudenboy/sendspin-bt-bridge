"""Device model: the timestamps the dashboard reads (standby since, release kind, RSSI sample time)."""

from __future__ import annotations

from sendspin_bridge.application.models.devices import Device

_BT = {
    "player_id": "11111111-2222-3333-4444-555555555555",
    "player_name": "Kitchen",
    "bluetooth_mac": "AA:BB:CC:DD:EE:FF",
    "bluetooth_adapter_hci": "hci0",
}


def test_rssi_sample_time_is_exposed_as_iso_utc() -> None:
    device = Device.from_status({**_BT, "rssi_dbm": -7, "rssi_at_ts": 1_700_000_000.5})

    assert device.bluetooth is not None
    assert device.bluetooth.rssi_dbm == -7
    assert device.bluetooth.rssi_at == "2023-11-14T22:13:20.500000+00:00"


def test_rssi_sample_time_is_null_without_a_sample() -> None:
    device = Device.from_status(dict(_BT))

    assert device.bluetooth is not None
    assert device.bluetooth.rssi_at is None


def test_rssi_sample_time_ignores_a_garbage_timestamp() -> None:
    device = Device.from_status({**_BT, "rssi_at_ts": "not-a-time"})

    assert device.bluetooth is not None
    assert device.bluetooth.rssi_at is None


def test_standby_since_and_release_kind_come_from_the_runtime_status() -> None:
    device = Device.from_status(
        {
            **_BT,
            "bt_standby": True,
            "bt_standby_since": "2026-10-14T10:00:00+00:00",
            "bt_management_enabled": False,
            "bt_released_by": "auto",
        }
    )

    assert device.bluetooth is not None
    assert device.bluetooth.standby_since == "2026-10-14T10:00:00+00:00"
    assert device.bluetooth.released_by == "auto"
    assert device.id == _BT["player_id"]


def test_output_schema_lists_the_dashboard_timestamps_as_required() -> None:
    schema = Device.model_json_schema(mode="serialization")
    bt = schema["$defs"]["DeviceBluetooth"]

    for key in ("standby_since", "released_by", "rssi_at"):
        assert key in bt["properties"]
        assert key in bt["required"]
