"""One object path per speaker, whoever builds it.

Two modules built the MPRIS object path for a device, each with its own copy
of the colons-to-underscores conversion: the export helper and the activation
factory.  A player is registered under one and looked up under the other, so
the two agreeing is not a nicety — it is what makes the registration findable.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.bluetooth.device_activation import _mpris_dbus_path
from sendspin_bridge.services.bluetooth.mpris_export import mpris_dbus_path


@pytest.mark.parametrize("mac", ["FC:58:FA:EB:08:6C", "fc:58:fa:eb:08:6c", "FC_58_FA_EB_08_6C"])
def test_both_builders_name_the_same_path(mac):
    assert _mpris_dbus_path(mac) == mpris_dbus_path(mac)


def test_the_path_is_a_legal_dbus_path():
    """D-Bus paths admit only ``[A-Za-z0-9_/]`` — colons must not survive."""
    path = mpris_dbus_path("fc:58:fa:eb:08:6c")

    assert path == "/org/sendspin/players/FC_58_FA_EB_08_6C"
    assert ":" not in path


def test_however_the_address_was_written_the_player_is_found_at_one_path():
    """A config in lower case must not hide the player from its own lookup."""
    assert mpris_dbus_path("fc:58:fa:eb:08:6c") == mpris_dbus_path("FC:58:FA:EB:08:6C")


def test_a_value_that_is_not_an_address_is_refused_rather_than_pathed():
    """Silently exporting a player at /org/sendspin/players/GARBAGE helps nobody."""
    with pytest.raises(ValueError):
        mpris_dbus_path("not-a-mac")
