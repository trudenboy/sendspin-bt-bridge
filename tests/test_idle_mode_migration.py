"""Tests for idle_mode config migration from legacy keepalive/idle fields."""

from __future__ import annotations

from config_migration import _normalize_bluetooth_devices

_DEFAULTS: dict[str, list[dict[str, str]]] = {"BLUETOOTH_DEVICES": []}


class TestIdleModeMigration:
    """Legacy keepalive/idle fields auto-migrate to idle_mode."""

    def test_keepalive_interval_migrates_to_keep_alive(self):
        """keepalive_interval > 0 without idle_mode → idle_mode: keep_alive."""
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "keepalive_interval": 45},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["idle_mode"] == "keep_alive"
        assert devices[0]["keepalive_interval"] == 45

    def test_idle_disconnect_migrates_to_auto_disconnect(self):
        """idle_disconnect_minutes > 0 without idle_mode → idle_mode: auto_disconnect."""
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "idle_disconnect_minutes": 15},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["idle_mode"] == "auto_disconnect"

    def test_both_zero_stays_default(self):
        """Both legacy fields zero/absent → no idle_mode set (stays default)."""
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF"},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert "idle_mode" not in devices[0]

    def test_explicit_idle_mode_not_overwritten(self):
        """Explicit idle_mode takes precedence over legacy fields."""
        config = {
            "BLUETOOTH_DEVICES": [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "keepalive_interval": 45,
                    "idle_mode": "power_save",
                },
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["idle_mode"] == "power_save"

    def test_keepalive_enabled_true_migrates(self):
        """Legacy keepalive_enabled: true (without interval) → keep_alive with default 30."""
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "keepalive_enabled": True},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["idle_mode"] == "keep_alive"

    def test_keepalive_wins_over_idle_disconnect(self):
        """When both legacy fields are set, keepalive takes precedence."""
        config = {
            "BLUETOOTH_DEVICES": [
                {
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "keepalive_interval": 60,
                    "idle_disconnect_minutes": 15,
                },
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert devices[0]["idle_mode"] == "keep_alive"

    def test_invalid_idle_mode_falls_back_to_default(self):
        """Invalid idle_mode value is removed."""
        config = {
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:FF", "idle_mode": "bogus"},
            ]
        }
        devices = _normalize_bluetooth_devices(config, defaults=_DEFAULTS)
        assert "idle_mode" not in devices[0]
