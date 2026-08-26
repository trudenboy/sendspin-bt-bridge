"""Looking a controller up in the map the kernel gives us.

``build_hci_map()`` keys its controllers by the bare address form, and the
routes and the orchestrator used to strip the colons and fold the case
themselves before every lookup — four copies of the same two calls.  One of
them forgetting either step means the controller maps nowhere, and that
surfaces as a pair or a scan running against the wrong physical adapter
(issue #340).

Kept outside ``bluetooth/bluez/`` on purpose: that package is stdlib-only so
it can be imported from anywhere without a cycle.
"""

from __future__ import annotations

from sendspin_bridge.bluetooth.address import DeviceAddress

__all__ = ["hci_for"]


def hci_for(mapping: dict[str, str], mac: object) -> str:
    """The ``hciN`` name *mac* is registered as, or ``""`` when it is in none."""
    address = DeviceAddress.parse(mac)
    if address is None:
        return ""
    return mapping.get(address.bare, "")
