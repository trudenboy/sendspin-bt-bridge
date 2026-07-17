from sendspin_bridge.services.bluetooth.transport_telemetry import (
    MEDIA_TRANSPORT_IFACE,
    BluetoothTransportSnapshot,
    select_transport_snapshot,
)

DEVICE = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"


def test_selects_active_transport_and_converts_delay_units():
    managed = {
        f"{DEVICE}/sep1/fd1": {MEDIA_TRANSPORT_IFACE: {"Device": DEVICE, "State": "pending", "Codec": 0}},
        f"{DEVICE}/sep1/fd2": {MEDIA_TRANSPORT_IFACE: {"Device": DEVICE, "State": "active", "Codec": 0, "Delay": 1250}},
    }

    result = select_transport_snapshot(managed, DEVICE)

    assert result.path.endswith("fd2")
    assert result.state == "active"
    assert result.codec_name == "sbc"
    assert result.delay_supported is True
    assert result.delay_ms == 125.0


def test_missing_delay_is_distinct_from_zero_delay():
    without_delay = {f"{DEVICE}/fd1": {MEDIA_TRANSPORT_IFACE: {"Device": DEVICE, "State": "active"}}}
    with_zero = {f"{DEVICE}/fd1": {MEDIA_TRANSPORT_IFACE: {"Device": DEVICE, "State": "active", "Delay": 0}}}

    assert select_transport_snapshot(without_delay, DEVICE).delay_supported is False
    zero = select_transport_snapshot(with_zero, DEVICE)
    assert zero.delay_supported is True
    assert zero.delay_ms == 0.0


def test_no_matching_transport_returns_empty_snapshot():
    assert select_transport_snapshot({}, DEVICE) == BluetoothTransportSnapshot()
