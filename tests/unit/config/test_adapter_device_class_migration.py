"""Tests for the per-adapter ``device_class`` config field added in
v2.65.1 for the Samsung Q-series CoD-filter workaround (bluez/bluez#1025).

The migration runs through ``_normalize_loaded_config`` which calls
``_normalize_bluetooth_adapters`` for every BLUETOOTH_ADAPTERS entry.
Tests cover the three shapes operators may produce when editing the
config by hand or via the UI dropdown:

- valid 6-hex-digit form (Samsung-compat preset, custom hex)
- invalid value (typo, wrong length, missing prefix) — must drop the
  field with a logged warning, never propagate to the kernel mgmt
  call which would silently apply garbage octets
- empty / missing — left alone so the kernel/bluetoothd default stays
  in place (the safe baseline for non-quirky peers)
"""

from __future__ import annotations

from sendspin_bridge.config import DEFAULT_CONFIG
from sendspin_bridge.config.migration import _normalize_loaded_config


def test_samsung_compat_value_passes_through():
    """The recommended ``0x00010c`` workaround value must round-trip
    unchanged.  Lowercase normalisation keeps the saved form
    consistent — the schema regex accepts both cases but downstream
    log lines and dropdown matching expect the lowercase form."""
    config = {"BLUETOOTH_ADAPTERS": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "USB BT", "device_class": "0x00010C"}]}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["BLUETOOTH_ADAPTERS"] == [{"mac": "AA:BB:CC:DD:EE:FF", "name": "USB BT", "device_class": "0x00010c"}]


def test_invalid_value_is_dropped():
    """Garbage values (missing prefix, wrong length, non-hex) must be
    stripped on load — the schema regex would reject them at validation
    time but operators may also paste a config from elsewhere; rather
    than fail the whole load, drop just the bad field and keep the
    rest of the adapter entry usable."""
    config = {
        "BLUETOOTH_ADAPTERS": [
            {"mac": "AA:BB:CC:DD:EE:FF", "name": "X", "device_class": "00010c"},
            {"mac": "BB:BB:CC:DD:EE:FF", "name": "Y", "device_class": "0xZZZZZZ"},
            {"mac": "CC:BB:CC:DD:EE:FF", "name": "Z", "device_class": "garbage"},
        ]
    }
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    for entry in config["BLUETOOTH_ADAPTERS"]:
        assert "device_class" not in entry
    assert {entry["mac"] for entry in config["BLUETOOTH_ADAPTERS"]} == {
        "AA:BB:CC:DD:EE:FF",
        "BB:BB:CC:DD:EE:FF",
        "CC:BB:CC:DD:EE:FF",
    }


def test_empty_string_strips_field():
    """Empty value means "leave kernel default" — the orchestrator
    treats missing/empty equivalently, so normalising to "absent" keeps
    the saved config minimal and avoids round-trip noise in diffs."""
    config = {"BLUETOOTH_ADAPTERS": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "USB BT", "device_class": ""}]}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    entry = config["BLUETOOTH_ADAPTERS"][0]
    assert "device_class" not in entry


def test_missing_field_left_alone():
    """Adapter entries from before v2.65.1 don't carry the field at
    all; migration must accept them without injecting a default."""
    config = {"BLUETOOTH_ADAPTERS": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "USB BT"}]}
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["BLUETOOTH_ADAPTERS"] == [{"mac": "AA:BB:CC:DD:EE:FF", "name": "USB BT"}]


def test_non_dict_entries_dropped():
    """A malformed entry (string, None) must not crash the loader —
    log it and skip, so one bad row in BLUETOOTH_ADAPTERS doesn't
    take down startup."""
    config = {
        "BLUETOOTH_ADAPTERS": [
            "not-a-dict",
            None,
            {"mac": "AA:BB:CC:DD:EE:FF", "name": "OK"},
        ]
    }
    _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
    assert config["BLUETOOTH_ADAPTERS"] == [{"mac": "AA:BB:CC:DD:EE:FF", "name": "OK"}]
