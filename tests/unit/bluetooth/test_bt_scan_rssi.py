"""Tests for RSSI extraction from bluetoothctl scan + info output (v2.63.0-rc.2).

The bridge surfaces signal strength on each device card so operators can
diagnose audio-quality complaints rooted in BT range / interference
without dropping into ``btmgmt`` or ``hcidump``.  Two ingest paths:

* the scan-stream parser — reads ``[CHG] Device <MAC> RSSI: <dB>`` lines
  emitted while a scan is running.  The unit covers the multiple
  bluetoothctl line formats (decimal, parenthesised hex, signed); the
  active-MAC and mac-shaped-name contracts live in
  ``test_bluez_parsers.py``.
* ``DeviceInfo.rssi`` — reads ``RSSI: <dB>`` lines from
  ``bluetoothctl info <MAC>`` for already-connected devices that don't
  appear in the live scan stream.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import classify_lines, parse_scan

# ── scan-stream RSSI grammar ────────────────────────────────────────────


def test_parse_scan_extracts_rssi_decimal():
    """Modern bluetoothctl emits ``RSSI: -43`` directly."""
    stdout = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: -43\n[CHG] Device 11:22:33:44:55:66 RSSI: -78\n"

    rssi_by_mac = parse_scan(classify_lines(stdout)).rssi_by_mac

    assert rssi_by_mac["AA:BB:CC:DD:EE:FF"] == -43
    assert rssi_by_mac["11:22:33:44:55:66"] == -78


def test_parse_scan_extracts_rssi_parenthesised_hex():
    """Older bluetoothctl emits ``RSSI: 0xffffffd5 (-43)`` — the
    parenthesised decimal is what we want."""
    stdout = "[CHG] Device AA:BB:CC:DD:EE:FF RSSI: 0xffffffd5 (-43)\n"

    assert parse_scan(classify_lines(stdout)).rssi_by_mac == {"AA:BB:CC:DD:EE:FF": -43}


# ── DeviceInfo.rssi: bluetoothctl info <MAC> ────────────────────────────
# (batch 2: api_bt's ``_extract_rssi_from_info`` moved into the bluez
# module as the ``DeviceInfo.rssi`` property — same dual-format grammar)


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
    from sendspin_bridge.bluetooth.bluez import parse_device_info

    assert parse_device_info(info_text, "AA:BB:CC:DD:EE:FF").rssi == expected


# ── DeviceStatus rssi fields ────────────────────────────────────────────


def test_device_status_has_rssi_fields_with_safe_defaults():
    """``DeviceStatus`` must declare ``rssi_dbm`` and ``rssi_at_ts`` so the
    background refresh task can populate them and Flask routes can read
    them without ``getattr`` ceremony."""
    from sendspin_bridge.bridge.client import DeviceStatus

    s = DeviceStatus()
    assert hasattr(s, "rssi_dbm") and s.rssi_dbm is None
    assert hasattr(s, "rssi_at_ts") and s.rssi_at_ts is None
