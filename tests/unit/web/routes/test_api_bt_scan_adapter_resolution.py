"""Tests for scan adapter resolution (issue #340).

The scan endpoint receives the adapter as a kernel ``hciN`` label (that
is what ``/api/bt/adapters`` hands the UI, resolved via sysfs — issue
#193).  Resolving that label back to a controller MAC must go through
the same sysfs map: ``bluetoothctl list`` enumerates controllers in
BlueZ's internal registration order, which can disagree with kernel
hci numbering.  Indexing the list positionally scanned — and labelled
the results with — the wrong physical adapter (issue #340).
"""

from __future__ import annotations

import pytest

from sendspin_bridge.web.routes.api_bt import _resolve_scan_adapter_macs

# bluetoothctl list order (BlueZ registration order): USB stick first.
BT_LIST_MACS = ["88:A2:9E:C0:07:0D", "A0:AD:9F:6E:B2:D5"]
# Kernel numbering (sysfs): the *built-in* adapter is hci0.
HCI_MAP = {
    "A0AD9F6EB2D5": "hci0",
    "88A29EC0070D": "hci1",
}


@pytest.fixture
def swapped_order(monkeypatch):
    """Two adapters whose kernel hci numbering disagrees with list order."""
    monkeypatch.setattr("sendspin_bridge.web.routes.api_bt.list_bt_adapters", lambda: list(BT_LIST_MACS))
    monkeypatch.setattr("sendspin_bridge.web.routes.api_bt.build_hci_map", lambda: dict(HCI_MAP))


def test_hci_label_resolves_via_sysfs_map_not_list_index(swapped_order):
    # "hci0" is the built-in adapter per sysfs — NOT bluetoothctl list[0].
    assert _resolve_scan_adapter_macs("hci0") == ["A0:AD:9F:6E:B2:D5"]
    assert _resolve_scan_adapter_macs("hci1") == ["88:A2:9E:C0:07:0D"]


def test_hci_label_unknown_to_sysfs_raises(swapped_order):
    # Sysfs knows the controllers but no such hciN exists → stale UI
    # selection; refuse rather than silently scanning something else.
    with pytest.raises(ValueError, match="not available"):
        _resolve_scan_adapter_macs("hci7")


def test_hci_label_falls_back_to_position_without_sysfs(monkeypatch):
    # Degraded environment (no /sys, hciconfig missing): the adapters
    # endpoint emits synthetic hci{i} labels in list order, so the scan
    # must resolve them the same way.
    monkeypatch.setattr("sendspin_bridge.web.routes.api_bt.list_bt_adapters", lambda: list(BT_LIST_MACS))
    monkeypatch.setattr("sendspin_bridge.web.routes.api_bt.build_hci_map", lambda: {})
    assert _resolve_scan_adapter_macs("hci0") == ["88:A2:9E:C0:07:0D"]
    assert _resolve_scan_adapter_macs("hci1") == ["A0:AD:9F:6E:B2:D5"]
    with pytest.raises(ValueError, match="not available"):
        _resolve_scan_adapter_macs("hci2")


def test_mac_and_all_selections_unchanged(swapped_order):
    # Direct MAC selection and the "all" scope predate the hciN labels
    # and must keep working as-is.
    assert _resolve_scan_adapter_macs("a0:ad:9f:6e:b2:d5") == ["A0:AD:9F:6E:B2:D5"]
    assert _resolve_scan_adapter_macs("all") == BT_LIST_MACS
    assert _resolve_scan_adapter_macs("") == BT_LIST_MACS


def test_invalid_hci_suffix_raises(swapped_order):
    with pytest.raises(ValueError, match="Invalid adapter identifier"):
        _resolve_scan_adapter_macs("hcixyz")
