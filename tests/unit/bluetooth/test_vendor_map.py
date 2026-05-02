"""Unit tests for the BT Modalias vendor lookup."""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.vendor_map import _VENDOR_MAP, vendor_from_modalias


@pytest.mark.parametrize(
    ("modalias", "expected"),
    [
        ("bluetooth:v009Ep4020d0001", "Sony"),
        ("bluetooth:v004Cp1234d5678", "Apple"),
        ("bluetooth:v0044p0001d0001", "Harman International"),
        ("bluetooth:v010Cp0002d0003", "IKEA"),
        # Lower-case hex is valid per BlueZ; the regex must accept both.
        ("bluetooth:v009ep4020d0001", "Sony"),
        # Vendor IDs may be shorter than 4 hex digits when leading zeros
        # are dropped — the regex must still match.
        ("bluetooth:v9Ep4020d0001", "Sony"),
    ],
)
def test_vendor_from_modalias_known_id(modalias: str, expected: str) -> None:
    assert vendor_from_modalias(modalias) == expected


@pytest.mark.parametrize(
    "modalias",
    [
        # Valid format, vendor ID not in our curated map.
        "bluetooth:vFFFFp0001d0001",
        "bluetooth:v9999p0000d0000",
    ],
)
def test_vendor_from_modalias_unknown_id_returns_empty(modalias: str) -> None:
    assert vendor_from_modalias(modalias) == ""


@pytest.mark.parametrize(
    "modalias",
    [
        None,
        "",
        "not-a-modalias",
        # USB-style modalias should not match — we only handle Bluetooth.
        "usb:v009Ep4020d0001",
        # Missing 'p' separator → regex doesn't match.
        "bluetooth:v009E4020d0001",
        # Vendor field empty.
        "bluetooth:vp4020d0001",
    ],
)
def test_vendor_from_modalias_malformed_returns_empty(modalias: str | None) -> None:
    assert vendor_from_modalias(modalias) == ""


def test_vendor_map_has_no_empty_strings() -> None:
    """Empty values would be indistinguishable from 'unknown' at the call site."""
    for vendor_id, name in _VENDOR_MAP.items():
        assert name, f"vendor_id 0x{vendor_id:04X} has empty name"


def test_vendor_map_keys_are_within_16_bit_range() -> None:
    """Bluetooth SIG company identifiers are 16-bit unsigned integers."""
    for vendor_id in _VENDOR_MAP:
        assert 0 <= vendor_id <= 0xFFFF, f"vendor_id 0x{vendor_id:X} out of range"
