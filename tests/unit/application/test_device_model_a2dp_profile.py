"""Device model exposes the A2DP/HFP profile evidence used by #586."""

from __future__ import annotations

from sendspin_bridge.application.models.devices import Device
from sendspin_bridge.bridge.client import DeviceStatus

_BT = {
    "player_id": "11111111-2222-3333-4444-555555555555",
    "player_name": "TimeBox",
    "bluetooth_mac": "11:75:58:03:E9:31",
    "bluetooth_adapter_hci": "hci0",
}


def test_hfp_only_profile_reaches_the_device_model() -> None:
    device = Device.from_status(
        {
            **_BT,
            "a2dp_available": False,
            "hfp_available": True,
            "profile_mode": "hfp_only",
        }
    )

    assert device.bluetooth is not None
    assert device.bluetooth.a2dp_available is False
    assert device.bluetooth.hfp_available is True
    assert device.bluetooth.profile_mode == "hfp_only"


def test_unknown_profile_fields_are_null_not_false() -> None:
    device = Device.from_status(dict(_BT))

    assert device.bluetooth is not None
    assert device.bluetooth.a2dp_available is None
    assert device.bluetooth.hfp_available is None
    assert device.bluetooth.profile_mode is None


def test_runtime_status_defaults_to_unknown_profile_evidence() -> None:
    status = DeviceStatus()

    assert status["a2dp_available"] is None
    assert status["hfp_available"] is None
    assert status["profile_mode"] is None
