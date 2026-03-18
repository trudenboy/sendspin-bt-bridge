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
import shutil
import socket as _socket
import tempfile
import threading
import time
import uuid as _uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

VERSION = "2.40.5-rc.2"
BUILD_DATE = "2026-03-18"
CONFIG_SCHEMA_VERSION = 1
UPDATE_CHANNELS = ("stable", "rc", "beta")
DEFAULT_UPDATE_CHANNEL = "stable"
DEFAULT_WEB_PORT = 8080
DEFAULT_LISTEN_PORT_BASE = 8928
HA_ADDON_CHANNEL_DEFAULTS = {
    "stable": {"web_port": DEFAULT_WEB_PORT, "base_listen_port": DEFAULT_LISTEN_PORT_BASE},
    "rc": {"web_port": 8081, "base_listen_port": 9028},
    "beta": {"web_port": 8082, "base_listen_port": 9128},
}

__all__ = [
    "BUILD_DATE",
    "CONFIG_DIR",
    "CONFIG_FILE",
    "CONFIG_SCHEMA_VERSION",
    "DEFAULT_CONFIG",
    "DEFAULT_LISTEN_PORT_BASE",
    "DEFAULT_UPDATE_CHANNEL",
    "DEFAULT_WEB_PORT",
    "UPDATE_CHANNELS",
    "VERSION",
    "check_password",
    "config_lock",
    "detect_ha_addon_channel",
    "ensure_bridge_name",
    "ensure_secret_key",
    "get_local_ip",
    "hash_password",
    "is_ha_addon_runtime",
    "load_config",
    "normalize_update_channel",
    "resolve_additional_web_port",
    "resolve_base_listen_port",
    "resolve_web_port",
    "save_device_sink",
    "save_device_volume",
    "update_config",
]

DEFAULT_CONFIG = {
    "CONFIG_SCHEMA_VERSION": CONFIG_SCHEMA_VERSION,
    "SENDSPIN_SERVER": "auto",
    "SENDSPIN_PORT": 9000,
    "WEB_PORT": None,
    "BASE_LISTEN_PORT": None,
    "BRIDGE_NAME": "",
    "BLUETOOTH_DEVICES": [],
    "BLUETOOTH_ADAPTERS": [],
    "TZ": "Australia/Melbourne",
    "LAST_VOLUMES": {},
    "LAST_SINKS": {},
    "PULSE_LATENCY_MSEC": 200,
    "PREFER_SBC_CODEC": False,
    "BT_CHECK_INTERVAL": 10,
    "BT_MAX_RECONNECT_FAILS": 0,
    "BT_CHURN_THRESHOLD": 0,
    "BT_CHURN_WINDOW": 300.0,
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
    "UPDATE_CHANNEL": DEFAULT_UPDATE_CHANNEL,
    "AUTO_UPDATE": False,
    "CHECK_UPDATES": True,
    "TRUSTED_PROXIES": [],
}

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "config.json"
config_lock = threading.Lock()  # serializes all config.json read-modify-write ops


def _backup_corrupt_config() -> Path | None:
    """Create a best-effort backup of a corrupt config file for later recovery."""
    if not CONFIG_FILE.exists():
        return None

    backup_path = CONFIG_DIR / f"{CONFIG_FILE.name}.corrupt-{int(time.time())}"
    try:
        shutil.copy2(CONFIG_FILE, backup_path)
        return backup_path
    except OSError as exc:
        logger.error("Could not back up corrupt config %s: %s", CONFIG_FILE, exc)
        return None


def _normalize_int_setting(
    config: dict, key: str, *, min_value: int | None = None, max_value: int | None = None
) -> None:
    raw = config.get(key, DEFAULT_CONFIG[key])
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s value %r in config; using default %r", key, raw, DEFAULT_CONFIG[key])
        config[key] = DEFAULT_CONFIG[key]
        return
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        logger.warning("Out-of-range %s value %r in config; using default %r", key, raw, DEFAULT_CONFIG[key])
        config[key] = DEFAULT_CONFIG[key]
        return
    config[key] = value


def _normalize_optional_int_setting(
    config: dict, key: str, *, min_value: int | None = None, max_value: int | None = None
) -> None:
    raw = config.get(key)
    if raw in (None, ""):
        config[key] = None
        return
    if not isinstance(raw, (int, str)):
        logger.warning("Invalid %s value %r in config; clearing override", key, raw)
        config[key] = None
        return
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s value %r in config; clearing override", key, raw)
        config[key] = None
        return
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        logger.warning("Out-of-range %s value %r in config; clearing override", key, raw)
        config[key] = None
        return
    config[key] = value


def _normalize_bool_setting(config: dict, key: str) -> None:
    raw = config.get(key, DEFAULT_CONFIG[key])
    if isinstance(raw, bool):
        return
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            config[key] = True
            return
        if lowered in {"0", "false", "no", "off"}:
            config[key] = False
            return
    logger.warning("Invalid %s value %r in config; using default %r", key, raw, DEFAULT_CONFIG[key])
    config[key] = DEFAULT_CONFIG[key]


def _normalize_float_setting(
    config: dict, key: str, *, min_value: float | None = None, max_value: float | None = None
) -> None:
    raw = config.get(key, DEFAULT_CONFIG[key])
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s value %r in config; using default %r", key, raw, DEFAULT_CONFIG[key])
        config[key] = DEFAULT_CONFIG[key]
        return
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        logger.warning("Out-of-range %s value %r in config; using default %r", key, raw, DEFAULT_CONFIG[key])
        config[key] = DEFAULT_CONFIG[key]
        return
    config[key] = value


def normalize_update_channel(raw_channel: object) -> str:
    """Return a supported update channel name."""
    if not isinstance(raw_channel, str):
        return DEFAULT_UPDATE_CHANNEL
    normalized = raw_channel.strip().lower()
    if normalized in UPDATE_CHANNELS:
        return normalized
    return DEFAULT_UPDATE_CHANNEL


def _coerce_port(raw_value: object, default: int) -> int:
    if isinstance(raw_value, bool):
        port = int(raw_value)
    elif isinstance(raw_value, (int, str)):
        try:
            port = int(raw_value)
        except ValueError:
            return default
    else:
        return default
    if 1 <= port <= 65535:
        return port
    return default


def _configured_port_override(config: dict | None, key: str, default: int) -> int | None:
    if not isinstance(config, dict):
        return None
    raw_value = config.get(key)
    if raw_value in (None, ""):
        return None
    return _coerce_port(raw_value, default)


def is_ha_addon_runtime(*, env: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if env is None else env
    return bool(environ.get("SUPERVISOR_TOKEN")) or Path("/data/options.json").exists()


def detect_ha_addon_channel(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> str:
    """Infer the installed HA addon delivery channel from the container hostname."""
    environ = os.environ if env is None else env
    if not is_ha_addon_runtime(env=environ):
        return DEFAULT_UPDATE_CHANNEL
    detected_hostname = (hostname or environ.get("HOSTNAME") or _socket.gethostname()).strip().lower()
    if detected_hostname.endswith("-rc"):
        return "rc"
    if detected_hostname.endswith("-beta"):
        return "beta"
    return DEFAULT_UPDATE_CHANNEL


def resolve_web_port(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> int:
    environ = os.environ if env is None else env
    if not is_ha_addon_runtime(env=environ):
        explicit_port = environ.get("WEB_PORT")
        if explicit_port not in (None, ""):
            return _coerce_port(explicit_port, DEFAULT_WEB_PORT)
        configured_port = _configured_port_override(load_config(), "WEB_PORT", DEFAULT_WEB_PORT)
        if configured_port is not None:
            return configured_port
    channel = detect_ha_addon_channel(env=environ, hostname=hostname)
    return HA_ADDON_CHANNEL_DEFAULTS[channel]["web_port"]


def resolve_additional_web_port(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> int | None:
    environ = os.environ if env is None else env
    if not is_ha_addon_runtime(env=environ):
        return None
    channel = detect_ha_addon_channel(env=environ, hostname=hostname)
    primary_port = HA_ADDON_CHANNEL_DEFAULTS[channel]["web_port"]
    explicit_port = environ.get("WEB_PORT")
    extra_port: int | None
    if explicit_port not in (None, ""):
        extra_port = _coerce_port(explicit_port, primary_port)
    else:
        extra_port = _configured_port_override(load_config(), "WEB_PORT", primary_port)
    if extra_port in (None, primary_port):
        return None
    return extra_port


def resolve_base_listen_port(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> int:
    environ = os.environ if env is None else env
    channel = detect_ha_addon_channel(env=environ, hostname=hostname)
    default_port = HA_ADDON_CHANNEL_DEFAULTS[channel]["base_listen_port"]
    explicit_port = environ.get("BASE_LISTEN_PORT")
    if explicit_port not in (None, ""):
        return _coerce_port(explicit_port, default_port)
    configured_port = _configured_port_override(load_config(), "BASE_LISTEN_PORT", default_port)
    if configured_port is not None:
        return configured_port
    return default_port


def _normalize_choice_setting(config: dict, key: str, *, allowed_values: tuple[str, ...], default: str) -> None:
    raw = config.get(key, default)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in allowed_values:
            config[key] = normalized
            return
    logger.warning("Invalid %s value %r in config; using default %r", key, raw, default)
    config[key] = default


def _normalize_bluetooth_devices(config: dict) -> list[dict]:
    devices = config.get("BLUETOOTH_DEVICES", DEFAULT_CONFIG["BLUETOOTH_DEVICES"])
    if not isinstance(devices, list):
        logger.warning("Invalid BLUETOOTH_DEVICES value %r in config; using default []", devices)
        return []

    normalized_devices: list[dict] = []
    for device in devices:
        if not isinstance(device, dict):
            logger.warning("Ignoring invalid Bluetooth device entry %r", device)
            continue
        normalized = dict(device)
        mac = normalized.get("mac")
        if isinstance(mac, str):
            normalized["mac"] = mac.strip().upper()
        normalized_devices.append(normalized)
    return normalized_devices


def _prune_last_volumes(config: dict) -> None:
    last_volumes = config.get("LAST_VOLUMES", DEFAULT_CONFIG["LAST_VOLUMES"])
    if not isinstance(last_volumes, dict):
        logger.warning("Invalid LAST_VOLUMES value %r in config; using default {}", last_volumes)
        config["LAST_VOLUMES"] = {}
        return

    configured_macs = {
        device.get("mac")
        for device in config.get("BLUETOOTH_DEVICES", [])
        if isinstance(device, dict) and isinstance(device.get("mac"), str) and device.get("mac")
    }
    sanitized: dict[str, int] = {}
    for mac, volume in last_volumes.items():
        if mac not in configured_macs:
            continue
        if not isinstance(volume, int) or not 0 <= volume <= 100:
            logger.warning("Ignoring invalid saved volume %r for %s", volume, mac)
            continue
        sanitized[mac] = volume
    config["LAST_VOLUMES"] = sanitized


def _normalize_loaded_config(config: dict) -> None:
    config["BLUETOOTH_DEVICES"] = _normalize_bluetooth_devices(config)
    _prune_last_volumes(config)

    for key, min_value, max_value in (
        ("SENDSPIN_PORT", 1, 65535),
        ("PULSE_LATENCY_MSEC", 1, 5000),
        ("BT_CHECK_INTERVAL", 1, 3600),
        ("BT_MAX_RECONNECT_FAILS", 0, 1000),
        ("BT_CHURN_THRESHOLD", 0, 1000),
        ("SESSION_TIMEOUT_HOURS", 1, 168),
        ("BRUTE_FORCE_MAX_ATTEMPTS", 1, 50),
        ("BRUTE_FORCE_WINDOW_MINUTES", 1, 1440),
        ("BRUTE_FORCE_LOCKOUT_MINUTES", 1, 1440),
    ):
        _normalize_int_setting(config, key, min_value=min_value, max_value=max_value)

    for key in ("WEB_PORT", "BASE_LISTEN_PORT"):
        _normalize_optional_int_setting(config, key, min_value=1, max_value=65535)

    _normalize_float_setting(config, "BT_CHURN_WINDOW", min_value=1.0, max_value=86400.0)
    _normalize_choice_setting(
        config,
        "UPDATE_CHANNEL",
        allowed_values=UPDATE_CHANNELS,
        default=DEFAULT_UPDATE_CHANNEL,
    )

    for key in (
        "PREFER_SBC_CODEC",
        "AUTH_ENABLED",
        "BRUTE_FORCE_PROTECTION",
        "MA_WEBSOCKET_MONITOR",
        "VOLUME_VIA_MA",
        "MUTE_VIA_MA",
        "SMOOTH_RESTART",
        "AUTO_UPDATE",
        "CHECK_UPDATES",
    ):
        _normalize_bool_setting(config, key)

    for key in ("BLUETOOTH_ADAPTERS", "TRUSTED_PROXIES"):
        value = config.get(key, DEFAULT_CONFIG.get(key, []))
        if not isinstance(value, list):
            logger.warning("Invalid %s value %r in config; using default []", key, value)
            config[key] = []


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
        devices = cfg.get("BLUETOOTH_DEVICES", [])
        known_macs = {
            device.get("mac", "").strip().upper()
            for device in devices
            if isinstance(device, dict) and isinstance(device.get("mac"), str)
        }
        normalized_mac = mac.strip().upper()
        if normalized_mac not in known_macs:
            cfg.setdefault("LAST_VOLUMES", {}).pop(normalized_mac, None)
            logger.warning("Skipping saved volume for unknown Bluetooth device %s", normalized_mac)
            return
        cfg.setdefault("LAST_VOLUMES", {})[normalized_mac] = volume

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
        "CONFIG_SCHEMA_VERSION",
        "SENDSPIN_SERVER",
        "SENDSPIN_PORT",
        "WEB_PORT",
        "BASE_LISTEN_PORT",
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
        "BT_CHURN_THRESHOLD",
        "BT_CHURN_WINDOW",
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
        "UPDATE_CHANNEL",
        "AUTO_UPDATE",
        "CHECK_UPDATES",
        "TRUSTED_PROXIES",
    }

    _needs_migration = False
    legacy_mac = ""

    if CONFIG_FILE.exists():
        try:
            with config_lock, open(CONFIG_FILE) as f:
                saved_config = json.load(f)
            for key, value in saved_config.items():
                if key in allowed_keys:
                    result[key] = value

            # Auto-migrate legacy BLUETOOTH_MAC → BLUETOOTH_DEVICES
            schema_version = saved_config.get("CONFIG_SCHEMA_VERSION")
            try:
                loaded_schema_version = int(schema_version) if schema_version is not None else None
            except (TypeError, ValueError):
                loaded_schema_version = None
            if loaded_schema_version != CONFIG_SCHEMA_VERSION:
                _needs_migration = True
                result["CONFIG_SCHEMA_VERSION"] = CONFIG_SCHEMA_VERSION

            legacy_mac = saved_config.get("BLUETOOTH_MAC", "")
            if legacy_mac and not result.get("BLUETOOTH_DEVICES"):
                result["BLUETOOTH_DEVICES"] = [
                    {"mac": legacy_mac, "adapter": "", "player_name": "Sendspin Player"},
                ]
                _needs_migration = True

            # Auto-migrate legacy LAST_VOLUME (single int) → LAST_VOLUMES (dict)
            legacy_vol = saved_config.get("LAST_VOLUME")
            if legacy_vol is not None and not result.get("LAST_VOLUMES"):
                result["LAST_VOLUMES"] = {}
                _needs_migration = True

            logger.info("Loaded config from %s", CONFIG_FILE)
        except json.JSONDecodeError as e:
            backup_path = _backup_corrupt_config()
            if backup_path is not None:
                logger.error(
                    "Config file %s is corrupted (%s); backup saved to %s; using defaults",
                    CONFIG_FILE,
                    e,
                    backup_path,
                )
            else:
                logger.error("Config file %s is corrupted (%s); using defaults", CONFIG_FILE, e)
            _needs_migration = False
        except (OSError, ValueError) as e:
            logger.warning("Error loading config: %s, using defaults", e)
            _needs_migration = False

        if _needs_migration:
            try:

                def _do_migrate(cfg: dict) -> None:
                    cfg["CONFIG_SCHEMA_VERSION"] = CONFIG_SCHEMA_VERSION
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

    _normalize_loaded_config(result)
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
