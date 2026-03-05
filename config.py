"""
Configuration management for sendspin-bt-bridge.

Provides the config file path, a process-wide lock for atomic writes,
and helpers for loading/persisting configuration.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import os
import secrets as _secrets
import threading
import uuid as _uuid
from pathlib import Path

VERSION = "2.7.16"
BUILD_DATE = "2026-03-05"

__all__ = [
    "BUILD_DATE",
    "CONFIG_DIR",
    "CONFIG_FILE",
    "DEFAULT_CONFIG",
    "VERSION",
    "check_password",
    "config_lock",
    "ensure_secret_key",
    "hash_password",
    "load_config",
    "save_device_volume",
]

DEFAULT_CONFIG = {
    "SENDSPIN_SERVER": "auto",
    "SENDSPIN_PORT": 9000,
    "BRIDGE_NAME": "",
    "BLUETOOTH_MAC": "",
    "BLUETOOTH_DEVICES": [],
    "TZ": "Australia/Melbourne",
    "PULSE_LATENCY_MSEC": 200,
    "PREFER_SBC_CODEC": False,
    "BT_CHECK_INTERVAL": 10,
    "BT_MAX_RECONNECT_FAILS": 0,
    "AUTH_ENABLED": False,
    "AUTH_PASSWORD_HASH": "",
    "SECRET_KEY": "",
    "LOG_LEVEL": "INFO",
}

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "config.json"
config_lock = threading.Lock()  # serializes all config.json read-modify-write ops
_config_lock = config_lock  # backward-compat alias


def _player_id_from_mac(mac: str) -> str:
    """Stable, globally-unique player ID derived from BT MAC address."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, mac.lower()))


def save_device_volume(mac: str | None, volume: int) -> None:
    """Persist per-device volume to config.json under LAST_VOLUMES[mac]."""
    if not mac or not CONFIG_FILE.exists():
        return
    try:
        with config_lock:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            cfg.setdefault("LAST_VOLUMES", {})[mac] = volume
            tmp = str(CONFIG_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, str(CONFIG_FILE))
    except Exception as e:
        logger.debug("Could not save volume for %s: %s", mac, e)


# Keep private alias for backward compatibility with internal callers
_save_device_volume = save_device_volume


def load_config() -> dict:
    """Load configuration from file, falling back to defaults."""
    result = DEFAULT_CONFIG.copy()

    allowed_keys = {
        "SENDSPIN_SERVER",
        "SENDSPIN_PORT",
        "BRIDGE_NAME",
        "BLUETOOTH_MAC",
        "BLUETOOTH_DEVICES",
        "TZ",
        "LAST_VOLUME",
        "LAST_VOLUMES",
        "BLUETOOTH_ADAPTERS",
        "BRIDGE_NAME_SUFFIX",
        "PULSE_LATENCY_MSEC",
        "PREFER_SBC_CODEC",
        "BT_CHECK_INTERVAL",
        "BT_MAX_RECONNECT_FAILS",
        "AUTH_ENABLED",
        "AUTH_PASSWORD_HASH",
        "SECRET_KEY",
        "LOG_LEVEL",
    }

    if CONFIG_FILE.exists():
        try:
            with config_lock, open(CONFIG_FILE) as f:
                saved_config = json.load(f)
            for key, value in saved_config.items():
                if key in allowed_keys:
                    result[key] = value
            logger.info("Loaded config from %s", CONFIG_FILE)
        except Exception as e:
            logger.warning("Error loading config: %s, using defaults", e)
    else:
        logger.info("Config file not found at %s, using defaults", CONFIG_FILE)

    return result


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
    except Exception:
        return False


def ensure_secret_key(config: dict) -> str:
    """Return SECRET_KEY from config, generating and persisting one if absent."""
    key = config.get("SECRET_KEY", "")
    if key:
        return key
    key = _secrets.token_hex(32)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with config_lock:
            existing: dict = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
            existing["SECRET_KEY"] = key
            tmp = str(CONFIG_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(existing, f, indent=2)
            os.replace(tmp, str(CONFIG_FILE))
    except Exception as e:
        logger.warning("Could not persist SECRET_KEY: %s", e)
    return key
