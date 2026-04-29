"""Tests for static_delay_ms migration (normalized to 0-5000 for sendspin 7.0+)."""

from __future__ import annotations

import sendspin_bridge.config.migration as config_migration
from sendspin_bridge.config.migration import _normalize_bluetooth_devices

_DEFAULTS: dict[str, list[dict[str, str]]] = {"BLUETOOTH_DEVICES": []}


def _clear_dedup_state():
    config_migration._warned_static_delay_issues.clear()


class TestStaticDelayMigration:
    def test_negative_delay_clamped_to_zero(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": -300},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["static_delay_ms"] == 0

    def test_large_negative_delay_clamped_to_zero(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": -600},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["static_delay_ms"] == 0

    def test_zero_delay_unchanged(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": 0},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["static_delay_ms"] == 0

    def test_positive_delay_unchanged(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": 150},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["static_delay_ms"] == 150

    def test_absent_delay_not_added(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF"},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert "static_delay_ms" not in devices[0]

    def test_string_negative_delay_clamped(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": "-500"},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["static_delay_ms"] == 0

    def test_migration_logs_warning(self, caplog):
        _clear_dedup_state()
        caplog.clear()
        with caplog.at_level("WARNING", logger="sendspin_bridge.config.migration"):
            config = {
                "BLUETOOTH_DEVICES": [
                    {"mac": "FC:58:FA:EB:08:6C", "static_delay_ms": -600},
                ]
            }
            _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert any("FC:58:FA:EB:08:6C" in msg and "clamping to 0" in msg for msg in caplog.messages)

    def test_exceeds_5000_clamped(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": 6000},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["static_delay_ms"] == 5000

    def test_non_numeric_removed(self):
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": "abc"},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert "static_delay_ms" not in devices[0]

    def test_non_numeric_logs_warning(self, caplog):
        _clear_dedup_state()
        caplog.clear()
        with caplog.at_level("WARNING", logger="sendspin_bridge.config.migration"):
            config = {
                "BLUETOOTH_DEVICES": [
                    {"mac": "11:22:33:44:55:66", "static_delay_ms": "not_a_number"},
                ]
            }
            _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert any("11:22:33:44:55:66" in msg and "invalid" in msg for msg in caplog.messages)

    def test_exceeds_5000_logs_warning(self, caplog):
        _clear_dedup_state()
        caplog.clear()
        with caplog.at_level("WARNING", logger="sendspin_bridge.config.migration"):
            config = {
                "BLUETOOTH_DEVICES": [
                    {"mac": "AA:BB:CC:DD:EE:FF", "static_delay_ms": 9999},
                ]
            }
            _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert any("AA:BB:CC:DD:EE:FF" in msg and "5000" in msg for msg in caplog.messages)
