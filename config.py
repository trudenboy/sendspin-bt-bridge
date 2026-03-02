"""
Configuration management for sendspin-bt-bridge.

Provides the config file path, a process-wide lock for atomic writes,
and helpers for loading/persisting configuration.
"""

VERSION = "1.5.0"
BUILD_DATE = "2026-03-02"

DEFAULT_CONFIG = {
    'SENDSPIN_SERVER': 'auto',
    'SENDSPIN_PORT': 9000,
    'BRIDGE_NAME': '',
    'BLUETOOTH_MAC': '',
    'BLUETOOTH_DEVICES': [],
    'TZ': 'Australia/Melbourne',
    'PULSE_LATENCY_MSEC': 200,
    'PREFER_SBC_CODEC': False,
    'BT_CHECK_INTERVAL': 10,
    'BT_MAX_RECONNECT_FAILS': 0,
}

import json
import logging
import os
import threading
import uuid as _uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.getenv('CONFIG_DIR', '/config'), 'config.json')
CONFIG_DIR = Path(os.getenv('CONFIG_DIR', '/config'))
CONFIG_FILE = CONFIG_DIR / 'config.json'
_config_lock = threading.Lock()  # serializes all config.json read-modify-write ops


def _player_id_from_mac(mac: str) -> str:
    """Stable, globally-unique player ID derived from BT MAC address."""
    return str(_uuid.uuid5(_uuid.NAMESPACE_DNS, mac.lower()))


def _save_device_volume(mac: Optional[str], volume: int) -> None:
    """Persist per-device volume to config.json under LAST_VOLUMES[mac]."""
    if not mac or not os.path.exists(_CONFIG_PATH):
        return
    try:
        with _config_lock:
            with open(_CONFIG_PATH, 'r') as f:
                cfg = json.load(f)
            cfg.setdefault('LAST_VOLUMES', {})[mac] = volume
            tmp = _CONFIG_PATH + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, _CONFIG_PATH)
    except Exception as e:
        logger.debug(f"Could not save volume for {mac}: {e}")


def load_config() -> dict:
    """Load configuration from file, falling back to defaults."""
    config_dir = Path(os.getenv('CONFIG_DIR', '/config'))
    config_file = config_dir / 'config.json'

    result = DEFAULT_CONFIG.copy()

    allowed_keys = {
        'SENDSPIN_SERVER', 'SENDSPIN_PORT', 'BRIDGE_NAME',
        'BLUETOOTH_MAC', 'BLUETOOTH_DEVICES', 'TZ', 'LAST_VOLUME',
        'LAST_VOLUMES', 'BLUETOOTH_ADAPTERS', 'BRIDGE_NAME_SUFFIX',
        'PULSE_LATENCY_MSEC', 'PREFER_SBC_CODEC',
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
