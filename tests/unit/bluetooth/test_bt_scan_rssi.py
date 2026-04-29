"""Tests for RSSI extraction from bluetoothctl scan + info output (v2.63.0-rc.2).

The bridge surfaces signal strength on each device card so operators can
diagnose audio-quality complaints rooted in BT range / interference
without dropping into ``btmgmt`` or ``hcidump``.  Two ingest paths:

* ``_parse_scan_output`` — reads ``[CHG] Device <MAC> RSSI: <dB>`` lines
  emitted while a scan is running.  The unit covers the multiple
  bluetoothctl line formats (decimal, parenthesised hex, signed).
* ``_extract_rssi_from_info`` — reads ``RSSI: <dB>`` lines from
  ``bluetoothctl info <MAC>`` for already-connected devices that don't
  appear in the live scan stream.
"""

from __future__ import annotations

import pytest

# ── _parse_scan_output: dict with rssi_by_mac added ─────────────────────


def test_parse_scan_output_extracts_rssi_decimal():
    """Modern bluetoothctl emits ``RSSI: -43`` directly."""
    from sendspin_bridge.web.routes.api_bt import _parse_scan_output

    stdout = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -43\n[CHG] Device 11:22:33:44:55:66 RSSI: -78\n"

    parsed = _parse_scan_output(stdout)

    # The legacy 4-tuple shape stays; rssi_by_mac is exposed via the new
    # 5-element return (mac → int dB).  Tests pin the contract.
    assert len(parsed) == 5, parsed
    rssi_by_mac = parsed[4]
    assert rssi_by_mac["AA:BB:CC:DD:EE:FF"] == -43
    assert rssi_by_mac["11:22:33:44:55:66"] == -78


def test_parse_scan_output_extracts_rssi_parenthesised_hex():
    """Older bluetoothctl emits ``RSSI: 0xffffffd5 (-43)`` — the
    parenthesised decimal is what we want."""
    from sendspin_bridge.web.routes.api_bt import _parse_scan_output

    stdout = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: 0xffffffd5 (-43)\n"

    parsed = _parse_scan_output(stdout)

    assert parsed[4].get("AA:BB:CC:DD:EE:FF") == -43


def test_parse_scan_output_keeps_active_mac_set_even_without_rssi_value():
    """A ``[CHG] ... RSSI:`` line with no numeric tail must still mark the
    device as active (legacy contract) even though no RSSI is captured."""
    from sendspin_bridge.web.routes.api_bt import _parse_scan_output

    stdout = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI:\n"

    parsed = _parse_scan_output(stdout)

    assert "AA:BB:CC:DD:EE:FF" in parsed[3]  # active_macs
    # rssi_by_mac may or may not contain the MAC, but an entry must not be wrong.
    assert parsed[4].get("AA:BB:CC:DD:EE:FF") in (None,)


# ── _extract_rssi_from_info: bluetoothctl info <MAC> ────────────────────


@pytest.mark.parametrize(
    ("info_text", "expected"),
    [
        # Modern decimal form
        ("Name: ENEBY20\nRSSI: -54\nConnected: yes\n", -54),
        # Legacy parenthesised hex
        ("Name: x\nRSSI: 0xffffffd0 (-48)\n", -48),
        # No RSSI line at all
        ("Name: x\nConnected: no\n", None),
        # Garbage RSSI value
        ("RSSI: not-a-number\n", None),
    ],
)
def test_extract_rssi_from_info_handles_format_variants(info_text, expected):
    from sendspin_bridge.web.routes.api_bt import _extract_rssi_from_info

    assert _extract_rssi_from_info(info_text) == expected


# ── DeviceStatus rssi fields ────────────────────────────────────────────


def test_device_status_has_rssi_fields_with_safe_defaults():
    """``DeviceStatus`` must declare ``rssi_dbm`` and ``rssi_at_ts`` so the
    background refresh task can populate them and Flask routes can read
    them without ``getattr`` ceremony."""
    from sendspin_bridge.bridge.client import DeviceStatus

    s = DeviceStatus()
    assert hasattr(s, "rssi_dbm") and s.rssi_dbm is None
    assert hasattr(s, "rssi_at_ts") and s.rssi_at_ts is None
