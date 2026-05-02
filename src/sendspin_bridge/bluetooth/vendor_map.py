"""Map BT Modalias vendor IDs to human-readable manufacturer names.

The bridge surfaces the manufacturer string in `client/hello.device_info.manufacturer`,
which Music Assistant displays directly in the player card. BlueZ exposes
`org.bluez.Device1.Modalias` as `bluetooth:v<vendor_hex>p<product_hex>d<device_hex>`,
where the vendor field is a Bluetooth SIG company identifier (16 bits).

We keep a small hand-curated map of the vendors we expect to see most often on
consumer BT speakers/headphones. Unknown IDs return an empty string so the
caller can decide the fallback (the bridge falls back to the host hostname,
matching the pre-2.68.x behaviour for backward compatibility).

Reference: https://www.bluetooth.com/specifications/assigned-numbers/company-identifiers/
"""

from __future__ import annotations

import re

_MODALIAS_RE = re.compile(r"bluetooth:v([0-9a-fA-F]+)p", re.IGNORECASE)

_VENDOR_MAP: dict[int, str] = {
    0x0006: "Microsoft",
    0x000A: "Cambridge Silicon Radio",
    0x000F: "Broadcom",
    0x0010: "Motorola",
    0x0044: "Harman International",  # JBL, Harman/Kardon, AKG
    0x004C: "Apple",
    0x0059: "Nordic Semiconductor",
    0x0067: "Logitech",
    0x0075: "Samsung",
    0x0078: "Nike",
    0x0087: "Garmin",
    0x008A: "Bose",
    0x0094: "JVCKENWOOD",
    0x009E: "Sony",
    0x00B7: "Beats",
    0x00BB: "Plantronics",
    0x010C: "IKEA",  # Sonos and IKEA Symfonisk co-branded line
    0x012D: "Sony Mobile",
    0x012E: "Yandex",
    0x0131: "Cypress",
    0x0136: "Skullcandy",
    0x0157: "Anhui Huami",
    0x0171: "Amazon",  # Echo speakers
    0x01DA: "Google",
    0x01F4: "Sonos",
    # Extend as new common vendors appear in field reports.
}


def vendor_from_modalias(modalias: str | None) -> str:
    """Return the manufacturer name encoded in a BlueZ Modalias string.

    Returns the empty string when the input is missing, malformed, or the
    parsed vendor ID is not in the curated map. Callers decide the fallback
    surface text (e.g. host hostname or "Unknown").
    """
    if not modalias:
        return ""
    match = _MODALIAS_RE.search(modalias)
    if not match:
        return ""
    try:
        vendor_id = int(match.group(1), 16)
    except ValueError:
        return ""
    return _VENDOR_MAP.get(vendor_id, "")
