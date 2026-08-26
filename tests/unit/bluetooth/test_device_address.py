"""One address, however it is spelled.

The same speaker address is written three ways in this codebase: with colons
for BlueZ and the config, with underscores for audio sink names and D-Bus
object paths, and bare for the sysfs adapter map.  Fifteen call sites did that
conversion by hand, each remembering on its own to fold case first.  A type
that knows all three removes the chance of remembering wrong.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.address import DeviceAddress

# ── parsing whatever the caller has ──────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "FC:58:FA:EB:08:6C",
        "fc:58:fa:eb:08:6c",
        "FC_58_FA_EB_08_6C",
        "fc_58_fa_eb_08_6c",
        "FC58FAEB086C",
        "  FC:58:FA:EB:08:6C  ",
    ],
)
def test_the_spellings_all_name_the_same_speaker(text):
    assert DeviceAddress.require(text) == DeviceAddress.require("FC:58:FA:EB:08:6C")


@pytest.mark.parametrize(
    "text",
    ["", "not-a-mac", "FC:58:FA:EB:08", "FC:58:FA:EB:08:6C:9A", "GG:58:FA:EB:08:6C", None],
)
def test_what_is_not_an_address_is_not_accepted(text):
    assert DeviceAddress.parse(text) is None
    with pytest.raises(ValueError):
        DeviceAddress.require(text)


# ── writing it the way each consumer needs ───────────────────────────────


def test_it_writes_every_spelling_its_consumers_use():
    address = DeviceAddress.require("fc:58:fa:eb:08:6c")

    assert address.colons == "FC:58:FA:EB:08:6C"
    assert address.underscores == "FC_58_FA_EB_08_6C"
    assert address.bare == "FC58FAEB086C"
    assert str(address) == "FC:58:FA:EB:08:6C"


def test_it_writes_the_dbus_node_name_bluez_uses():
    assert DeviceAddress.require("fc:58:fa:eb:08:6c").dbus_node == "dev_FC_58_FA_EB_08_6C"


# ── the property the hand-rolled conversions kept re-deriving ────────────


def test_case_never_decides_whether_two_addresses_match():
    assert DeviceAddress.require("fc:58:fa:eb:08:6c") == DeviceAddress.require("FC:58:FA:EB:08:6C")


def test_it_can_be_a_dictionary_key_whatever_it_was_parsed_from():
    index = {DeviceAddress.require("FC_58_FA_EB_08_6C"): "Kitchen"}

    assert index[DeviceAddress.require("fc:58:fa:eb:08:6c")] == "Kitchen"
