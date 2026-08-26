"""The adapter map is keyed by an address, not by a spelling of one.

`build_hci_map()` keys its controllers by the bare form, and four call sites
across the routes and the orchestrator strip the colons themselves before
looking one up.  Each of those has to fold case as well — and each is a place
where forgetting means a controller quietly maps nowhere, which surfaces as a
pair or scan running against the wrong physical adapter.
"""

from __future__ import annotations

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.bluetooth.bluez._adapters import parse_hciconfig

HCICONFIG = """hci1:\tType: Primary  Bus: USB
\tBD Address: 00:02:72:0A:E4:3B  ACL MTU: 1017:8  SCO MTU: 64:8
\tUP RUNNING PSCAN
hci0:\tType: Primary  Bus: USB
\tBD Address: c0:fb:f9:62:d7:d6  ACL MTU: 310:10  SCO MTU: 64:8
\tUP RUNNING PSCAN
"""


def test_a_controller_is_found_however_its_address_was_written():
    """hciconfig prints lower case on some hosts; the caller may hold upper."""
    mapping = parse_hciconfig(HCICONFIG)

    assert mapping[DeviceAddress.require("C0:FB:F9:62:D7:D6").bare] == "hci0"
    assert mapping[DeviceAddress.require("00:02:72:0a:e4:3b").bare] == "hci1"


def test_the_map_is_keyed_the_way_the_address_type_spells_it():
    """So a lookup can be built from an address rather than by hand."""
    mapping = parse_hciconfig(HCICONFIG)

    assert set(mapping) == {
        DeviceAddress.require("00:02:72:0A:E4:3B").bare,
        DeviceAddress.require("C0:FB:F9:62:D7:D6").bare,
    }
