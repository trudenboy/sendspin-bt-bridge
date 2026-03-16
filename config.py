"""
Configuration management for sendspin-bt-bridge.

Provides the config file path, a process-wide lock for atomic writes,
and helpers for loading/persisting configuration.
"""

from __future__ import annotations

import copy
import hashlib
import hmac as _hmac
import json
import logging
import os
import secrets as _secrets
import socket as _socket
import tempfile
import threading
import uuid as _uuid
from pathlib import Path

VERSION = "2.31.9"
BUILD_DATE = "2026-03-16"

__all__ = [
    "BUILD_DATE",
    "CONFIG_DIR",
    "CONFIG_FILE",
    "DEFAULT_CONFIG",
    "VERSION",
    "check_password",
    "config_lock",
    "ensure_bridge_name",
    "ensure_secret_key",
    "get_local_ip",
    "hash_password",
    "load_config",
    "save_device_sink",
    "save_device_volume",
    "update_config",
]

DEFAULT_CONFIG = {
    "SENDSPIN_SERVER": "auto",
    "SENDSPIN_PORT": 9000,
    "BRIDGE_NAME": "",
    "BLUETOOTH_DEVICES": [],
    "TZ": "Australia/Melbourne",
    "PULSE_LATENCY_MSEC": 200,
    "PREFER_SBC_CODEC": False,
    "BT_CHECK_INTERVAL": 10,
    "BT_MAX_RECONNECT_FAILS": 0,
    "AUTH_ENABLED": False,
    "SESSION_TIMEOUT_HOURS": 24,
    "BRUTE_FORCE_PROTECTION": True,
    "BRUTE_FORCE_MAX_ATTEMPTS": 5,
    "BRUTE_FORCE_WINDOW_MINUTES": 1,
    "BRUTE_FORCE_LOCKOUT_MINUTES": 5,
    "AUTH_PASSWORD_HASH": "",
    "SECRET_KEY": "",
    "LOG_LEVEL": "INFO",
    "MA_API_URL": "",
    "MA_API_TOKEN": "",
    "MA_USERNAME": "",
    "MA_WEBSOCKET_MONITOR": True,
    "VOLUME_VIA_MA": True,
    "MUTE_VIA_MA": False,
    "SMOOTH_RESTART": True,
    "AUTO_UPDATE": False,
    "CHECK_UPDATES": True,
    "TRUSTED_PROXIES": [],
}

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "config.json"
config_lock = threading.Lock()  # serializes all config.json read-modify-write ops


def update_config(mutator) -> None:
    """Atomically read-modify-write config.json under config_lock.

    ``mutator`` is called with the current config dict and should modify it
    in-place.  The result is written to a temp file and atomically renamed.
    """
    with config_lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                existing = json.load(f)
        mutator(existing)
        tmp_f = tempfile.NamedTemporaryFile(  # noqa: SIM115
            dir=str(CONFIG_DIR), delete=False, mode="w", suffix=".tmp"
        )
        try:
            json.dump(existing, tmp_f, indent=2)
            tmp_f.flush()
            os.fsync(tmp_f.fileno())
            tmp_f.close()
            os.replace(tmp_f.name, str(CONFIG_FILE))
        except BaseException:
            tmp_f.close()
            try:
                os.unlink(tmp_f.name)
            except OSError:
                pass
            raise


def _player_id_from_mac(mac: str) -> str:
    """Stable, globally-unique player ID derived from BT MAC address."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, mac.lower()))


def save_device_volume(mac: str | None, volume: int) -> None:
    """Persist per-device volume to config.json under LAST_VOLUMES[mac]."""
    if not mac or not CONFIG_FILE.exists():
        return

    def _set_vol(cfg: dict) -> None:
        cfg.setdefault("LAST_VOLUMES", {})[mac] = volume

    try:
        update_config(_set_vol)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Could not save volume for %s: %s", mac, e)


def save_device_sink(mac: str | None, sink_name: str) -> None:
    """Persist per-device PA sink name to config.json under LAST_SINKS[mac]."""
    if not mac or not CONFIG_FILE.exists():
        return

    def _set_sink(cfg: dict) -> None:
        cfg.setdefault("LAST_SINKS", {})[mac] = sink_name

    try:
        update_config(_set_sink)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Could not save sink for %s: %s", mac, e)


def load_config() -> dict:
    """Load configuration from file, falling back to defaults."""
    result = copy.deepcopy(DEFAULT_CONFIG)

    allowed_keys = {
        "SENDSPIN_SERVER",
        "SENDSPIN_PORT",
        "BRIDGE_NAME",
        "BLUETOOTH_DEVICES",
        "TZ",
        "LAST_VOLUMES",
        "LAST_SINKS",
        "BLUETOOTH_ADAPTERS",
        "PULSE_LATENCY_MSEC",
        "PREFER_SBC_CODEC",
        "BT_CHECK_INTERVAL",
        "BT_MAX_RECONNECT_FAILS",
        "AUTH_ENABLED",
        "SESSION_TIMEOUT_HOURS",
        "BRUTE_FORCE_PROTECTION",
        "BRUTE_FORCE_MAX_ATTEMPTS",
        "BRUTE_FORCE_WINDOW_MINUTES",
        "BRUTE_FORCE_LOCKOUT_MINUTES",
        "AUTH_PASSWORD_HASH",
        "SECRET_KEY",
        "LOG_LEVEL",
        "MA_API_URL",
        "MA_API_TOKEN",
        "MA_AUTH_PROVIDER",
        "MA_USERNAME",
        "MA_WEBSOCKET_MONITOR",
        "VOLUME_VIA_MA",
        "MUTE_VIA_MA",
        "SMOOTH_RESTART",
        "AUTO_UPDATE",
        "CHECK_UPDATES",
        "TRUSTED_PROXIES",
    }

    _needs_migration = False

    if CONFIG_FILE.exists():
        try:
            with config_lock, open(CONFIG_FILE) as f:
                saved_config = json.load(f)
            for key, value in saved_config.items():
                if key in allowed_keys:
                    result[key] = value

            # Auto-migrate legacy BLUETOOTH_MAC → BLUETOOTH_DEVICES
            legacy_mac = saved_config.get("BLUETOOTH_MAC", "")
            if legacy_mac and not result.get("BLUETOOTH_DEVICES"):
                result["BLUETOOTH_DEVICES"] = [
                    {"mac": legacy_mac, "adapter": "", "player_name": "Sendspin Player"},
                ]
                _needs_migration = True
            else:
                _needs_migration = False

            # Auto-migrate legacy LAST_VOLUME (single int) → LAST_VOLUMES (dict)
            legacy_vol = saved_config.get("LAST_VOLUME")
            if legacy_vol is not None and not result.get("LAST_VOLUMES"):
                result["LAST_VOLUMES"] = {}
                _needs_migration = True

            logger.info("Loaded config from %s", CONFIG_FILE)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("Error loading config: %s, using defaults", e)
            _needs_migration = False

        if _needs_migration:
            try:

                def _do_migrate(cfg: dict) -> None:
                    if legacy_mac and not cfg.get("BLUETOOTH_DEVICES"):
                        cfg["BLUETOOTH_DEVICES"] = result["BLUETOOTH_DEVICES"]
                    cfg.pop("BLUETOOTH_MAC", None)
                    cfg.pop("LAST_VOLUME", None)

                update_config(_do_migrate)
                logger.info("Migrated legacy config keys to current format")
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not persist config migration: %s", exc)
    else:
        logger.info("Config file not found at %s, using defaults", CONFIG_FILE)

    return result


def ensure_bridge_name(config: dict | None = None) -> str:
    """Return BRIDGE_NAME, auto-populating it with the hostname if empty.

    On first startup the config will have ``BRIDGE_NAME: ""``.  This function
    writes the machine hostname into ``config.json`` so that the user can see
    and edit it in the Web UI *before* adding any Bluetooth devices.
    """
    if config is None:
        config = load_config()
    raw = config.get("BRIDGE_NAME", "") or os.getenv("BRIDGE_NAME", "")
    if raw.lower() in ("auto", "hostname"):
        raw = _socket.gethostname()
    if raw:
        return raw

    hostname = _socket.gethostname()
    try:
        update_config(lambda cfg: cfg.__setitem__("BRIDGE_NAME", hostname))
        logger.info("Auto-set BRIDGE_NAME to '%s' (hostname)", hostname)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not persist BRIDGE_NAME: %s", e)
    return hostname


def get_local_ip() -> str:
    """Return the primary local IP address via a UDP socket probe."""
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

_PBKDF2_ITERS = 260_000


def hash_password(plain: str) -> str:
    """Return PBKDF2-SHA256 hash with embedded random salt (salt_hex:hash_hex)."""
    salt = _secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERS)
    return salt.hex() + ":" + h.hex()


def check_password(plain: str, stored: str) -> bool:
    """Verify plain password against a stored hash produced by hash_password()."""
    try:
        salt_hex, h_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt, _PBKDF2_ITERS)
        return _hmac.compare_digest(h.hex(), h_hex)
    except (ValueError, TypeError):
        return False


def ensure_secret_key(config: dict) -> str:
    """Return SECRET_KEY from config, generating and persisting one if absent."""
    key = config.get("SECRET_KEY", "")
    if key:
        return key
    key = _secrets.token_hex(32)
    try:
        update_config(lambda cfg: cfg.__setitem__("SECRET_KEY", key))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not persist SECRET_KEY: %s", e)
    return key
