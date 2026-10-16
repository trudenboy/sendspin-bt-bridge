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


def test_volume_is_read_when_the_transport_has_one():
    managed = {f"{DEVICE}/fd1": {MEDIA_TRANSPORT_IFACE: {"Device": DEVICE, "State": "idle", "Volume": 96}}}
    assert select_transport_snapshot(managed, DEVICE).volume == 96
    managed[f"{DEVICE}/fd1"][MEDIA_TRANSPORT_IFACE].pop("Volume")
    assert select_transport_snapshot(managed, DEVICE).volume is None


def test_a_new_transport_gets_one_line_with_codec_delay_and_volume():
    from sendspin_bridge.services.bluetooth.transport_telemetry import transport_connect_line

    managed = {
        f"{DEVICE}/sep1/fd2": {
            MEDIA_TRANSPORT_IFACE: {"Device": DEVICE, "State": "active", "Codec": 0, "Delay": 1250, "Volume": 96}
        }
    }
    snapshot = select_transport_snapshot(managed, DEVICE)

    line = transport_connect_line(None, snapshot)

    assert line == (
        f"BT transport {DEVICE}/sep1/fd2: codec sbc (0x00), Delay 125.0 ms (reported by the speaker), "
        "Volume 96/127, state active"
    )
    assert transport_connect_line(snapshot.path, snapshot) is None


def test_the_line_says_when_delay_and_volume_are_absent():
    from sendspin_bridge.services.bluetooth.transport_telemetry import transport_connect_line

    managed = {f"{DEVICE}/fd1": {MEDIA_TRANSPORT_IFACE: {"Device": DEVICE, "State": "idle", "Codec": 2}}}
    line = transport_connect_line(None, select_transport_snapshot(managed, DEVICE))

    assert line is not None
    assert "codec aac (0x02)" in line
    assert "Delay absent" in line
    assert "Volume absent" in line


def test_no_transport_no_line():
    from sendspin_bridge.services.bluetooth.transport_telemetry import transport_connect_line

    assert transport_connect_line(None, BluetoothTransportSnapshot()) is None
