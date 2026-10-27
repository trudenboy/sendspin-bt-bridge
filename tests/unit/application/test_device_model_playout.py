"""Device model: the playout engine snapshot reaches ``timing.playout`` (and the OpenAPI schema)."""

from __future__ import annotations

from sendspin_bridge.application.models.devices import Device, PlayoutTiming

_BT = {
    "player_id": "11111111-2222-3333-4444-555555555555",
    "player_name": "Kitchen",
    "bluetooth_mac": "AA:BB:CC:DD:EE:FF",
    "bluetooth_adapter_hci": "hci0",
}


def test_playout_snapshot_is_exposed_in_timing() -> None:
    device = Device.from_status(
        {
            **_BT,
            "playout": {"mode": "loop", "state": "playing", "error_ms": 1.25, "jumps": 2, "warm": True},
            "effective_required_lead_time_ms": 480,
        }
    )

    assert device.timing.playout is not None
    assert device.timing.playout.error_ms == 1.25
    assert device.timing.playout.mode == "loop"
    assert device.timing.playout.jumps == 2
    assert device.timing.playout.warm is True
    assert device.timing.effective_required_lead_time_ms == 480


def test_unknown_playout_keys_are_ignored() -> None:
    device = Device.from_status({**_BT, "playout": {"mode": "measure", "k_ns": 12345, "future": "x"}})

    assert device.timing.playout is not None
    assert device.timing.playout.mode == "measure"
    assert not hasattr(device.timing.playout, "k_ns")


def test_missing_playout_is_null() -> None:
    assert Device.from_status({**_BT, "playout": None}).timing.playout is None
    assert Device.from_status(dict(_BT)).timing.playout is None
    assert Device.from_status(dict(_BT)).timing.effective_required_lead_time_ms is None


def test_playout_defaults() -> None:
    p = PlayoutTiming()
    assert (p.mode, p.warm, p.inserted, p.dropped, p.jumps, p.underruns) == ("off", False, 0, 0, 0, 0)
    assert p.error_ms is None


def test_garbage_playout_does_not_break_the_device() -> None:
    assert Device.from_status({**_BT, "playout": "nope"}).timing.playout is None


def test_openapi_schema_lists_playout_timing() -> None:
    from sendspin_bridge.api.app import create_app

    schemas = create_app().openapi()["components"]["schemas"]
    assert "PlayoutTiming" in schemas
