"""Device model: the A2DP Delay a speaker reports, as telemetry for the dashboard."""

from __future__ import annotations

from sendspin_bridge.application.models.devices import Device
from sendspin_bridge.bridge.client import DeviceStatus

_BT = {
    "player_id": "11111111-2222-3333-4444-555555555555",
    "player_name": "Kitchen",
    "bluetooth_mac": "AA:BB:CC:DD:EE:FF",
    "bluetooth_adapter_hci": "hci0",
}


def test_a_reported_delay_reaches_the_device_model() -> None:
    device = Device.from_status({**_BT, "bt_reported_delay_ms": 123.4, "bt_delay_reporting_supported": True})

    assert device.bluetooth is not None
    assert device.bluetooth.reported_delay_ms == 123.4
    assert device.bluetooth.delay_reporting_supported is True


def test_a_speaker_without_delay_reporting_has_a_null_delay() -> None:
    device = Device.from_status(dict(_BT))

    assert device.bluetooth is not None
    assert device.bluetooth.reported_delay_ms is None
    assert device.bluetooth.delay_reporting_supported is False


def test_the_runtime_status_starts_without_a_delay() -> None:
    status = DeviceStatus()

    assert status["bt_reported_delay_ms"] is None
    assert status.get("bt_delay_reporting_supported") is False


def test_the_api_schema_documents_the_delay_as_nullable_telemetry() -> None:
    from sendspin_bridge.application.models.devices import DeviceBluetooth

    prop = DeviceBluetooth.model_json_schema(mode="serialization")["properties"]["reported_delay_ms"]

    assert {"type": "null"} in prop["anyOf"]
    assert "Delay Report" in prop.get("description", "")
