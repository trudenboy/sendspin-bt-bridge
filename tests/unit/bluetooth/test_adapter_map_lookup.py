"""A controller lookup that cannot be spelled wrong.

The map from `build_hci_map()` is keyed by the bare address, and four call
sites stripped the colons and folded the case by hand before each lookup.
Either step forgotten means the controller maps nowhere — which surfaces as a
pair or a scan running against the wrong physical adapter (issue #340).
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.adapter_map import hci_for

MAPPING = {"C0FBF962D7D6": "hci0", "0002720AE43B": "hci1"}


@pytest.mark.parametrize(
    "mac",
    ["C0:FB:F9:62:D7:D6", "c0:fb:f9:62:d7:d6", "C0_FB_F9_62_D7_D6", "c0fbf962d7d6"],
)
def test_however_the_address_is_spelled_the_controller_is_found(mac):
    assert hci_for(MAPPING, mac) == "hci0"


def test_a_controller_the_map_does_not_carry_answers_empty():
    assert hci_for(MAPPING, "AA:BB:CC:DD:EE:FF") == ""


@pytest.mark.parametrize("mac", ["", None, "hci0", 17])
def test_a_value_that_is_not_an_address_answers_empty_rather_than_raising(mac):
    """These lookups sit on request paths; a bad argument must not 500."""
    assert hci_for(MAPPING, mac) == ""
