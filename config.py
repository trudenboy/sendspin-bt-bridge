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

VERSION = "2.3.1"
BUILD_DATE = "2026-03-03"

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
}

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.getenv("CONFIG_DIR", "/config"), "config.json")
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "config.json"
_config_lock = threading.Lock()  # serializes all config.json read-modify-write ops


def _player_id_from_mac(mac: str) -> str:
    """Stable, globally-unique player ID derived from BT MAC address."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, mac.lower()))


def _save_device_volume(mac: str | None, volume: int) -> None:
    """Persist per-device volume to config.json under LAST_VOLUMES[mac]."""
    if not mac or not os.path.exists(_CONFIG_PATH):
        return
    try:
        with _config_lock:
            with open(_CONFIG_PATH) as f:
                cfg = json.load(f)
            cfg.setdefault("LAST_VOLUMES", {})[mac] = volume
            tmp = _CONFIG_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, _CONFIG_PATH)
    except Exception as e:
        logger.debug(f"Could not save volume for {mac}: {e}")


def load_config() -> dict:
    """Load configuration from file, falling back to defaults."""
    config_dir = Path(os.getenv("CONFIG_DIR", "/config"))
    config_file = config_dir / "config.json"

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
    }

    if config_file.exists():
        try:
            with open(config_file) as f:
                saved_config = json.load(f)
            for key, value in saved_config.items():
                if key in allowed_keys:
                    result[key] = value
            logger.info(f"Loaded config from {config_file}")
        except Exception as e:
            logger.warning(f"Error loading config: {e}, using defaults")
    else:
        logger.info(f"Config file not found at {config_file}, using defaults")

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
        with _config_lock:
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
        logger.warning(f"Could not persist SECRET_KEY: {e}")
    return key
