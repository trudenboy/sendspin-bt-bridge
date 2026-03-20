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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

VERSION = "2.42.0-rc.20"
BUILD_DATE = "2026-03-20"
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
    "CONFIG_ALLOWED_KEYS",
    "CONFIG_DIR",
    "CONFIG_FILE",
    "CONFIG_SCHEMA_VERSION",
    "DEFAULT_CONFIG",
    "DEFAULT_LISTEN_PORT_BASE",
    "DEFAULT_UPDATE_CHANNEL",
    "DEFAULT_WEB_PORT",
    "RUNTIME_STATE_CONFIG_KEYS",
    "SENSITIVE_CONFIG_KEYS",
    "UPDATE_CHANNELS",
    "VERSION",
    "ConfigMigrationIssue",
    "ConfigMigrationResult",
    "check_password",
    "config_lock",
    "detect_ha_addon_channel",
    "ensure_bridge_name",
    "ensure_secret_key",
    "get_local_ip",
    "hash_password",
    "is_ha_addon_runtime",
    "load_config",
    "migrate_config_payload",
    "normalize_update_channel",
    "resolve_additional_web_port",
    "resolve_base_listen_port",
    "resolve_web_port",
    "save_device_sink",
    "save_device_volume",
    "update_config",
    "write_config_file",
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
    "MA_AUTH_PROVIDER": "",
    "MA_USERNAME": "",
    "MA_TOKEN_INSTANCE_HOSTNAME": "",
    "MA_TOKEN_LABEL": "",
    "MA_ACCESS_TOKEN": "",
    "MA_REFRESH_TOKEN": "",
    "MA_AUTO_SILENT_AUTH": True,
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
CONFIG_ALLOWED_KEYS = frozenset(DEFAULT_CONFIG)
RUNTIME_STATE_CONFIG_KEYS = frozenset(("LAST_VOLUMES", "LAST_SINKS"))
SENSITIVE_CONFIG_KEYS = frozenset(
    (
        "AUTH_PASSWORD_HASH",
        "SECRET_KEY",
        "MA_API_TOKEN",
        "MA_ACCESS_TOKEN",
        "MA_REFRESH_TOKEN",
    )
)


@dataclass(frozen=True)
class ConfigMigrationIssue:
    field: str
    message: str


@dataclass
class ConfigMigrationResult:
    normalized_config: dict[str, Any]
    warnings: list[ConfigMigrationIssue] = field(default_factory=list)
    needs_persist: bool = False


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


def _read_raw_config_file() -> dict[str, Any]:
    with open(CONFIG_FILE) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Config file must contain a JSON object")
    return data


def _filter_allowed_config_keys(config: dict[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in config.items() if key in CONFIG_ALLOWED_KEYS}


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
    return None


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


def _normalize_mac_key(raw_mac: object) -> str:
    if not isinstance(raw_mac, str):
        return ""
    return raw_mac.strip().upper()


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


def _prune_last_sinks(config: dict) -> None:
    last_sinks = config.get("LAST_SINKS", DEFAULT_CONFIG["LAST_SINKS"])
    if not isinstance(last_sinks, dict):
        logger.warning("Invalid LAST_SINKS value %r in config; using default %r", last_sinks, {})
        config["LAST_SINKS"] = {}
        return

    configured_macs = {
        _normalize_mac_key(device.get("mac"))
        for device in config.get("BLUETOOTH_DEVICES", [])
        if isinstance(device, dict) and _normalize_mac_key(device.get("mac"))
    }
    sanitized: dict[str, str] = {}
    for mac, sink_name in last_sinks.items():
        normalized_mac = _normalize_mac_key(mac)
        if not normalized_mac:
            logger.warning("Ignoring non-string MAC key %r in LAST_SINKS", mac)
            continue
        if normalized_mac not in configured_macs:
            continue
        if not isinstance(sink_name, str) or not sink_name.strip():
            logger.warning("Ignoring invalid saved sink %r for %s", sink_name, mac)
            continue
        sanitized[normalized_mac] = sink_name.strip()
    config["LAST_SINKS"] = sanitized


def _normalize_loaded_config(config: dict) -> None:
    config["BLUETOOTH_DEVICES"] = _normalize_bluetooth_devices(config)
    _prune_last_volumes(config)
    _prune_last_sinks(config)

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
        "MA_AUTO_SILENT_AUTH",
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


def migrate_config_payload(config: dict[str, Any]) -> ConfigMigrationResult:
    normalized = _filter_allowed_config_keys(config)
    warnings: list[ConfigMigrationIssue] = []
    needs_persist = False

    raw_schema_version = config.get("CONFIG_SCHEMA_VERSION")
    try:
        schema_version = int(str(raw_schema_version)) if raw_schema_version not in (None, "") else None
    except (TypeError, ValueError):
        schema_version = None
        warnings.append(
            ConfigMigrationIssue(
                field="CONFIG_SCHEMA_VERSION",
                message=f"Invalid CONFIG_SCHEMA_VERSION {raw_schema_version!r}; migrating to {CONFIG_SCHEMA_VERSION}",
            )
        )
        needs_persist = True

    if schema_version is None or schema_version < CONFIG_SCHEMA_VERSION:
        if raw_schema_version in (None, ""):
            warnings.append(
                ConfigMigrationIssue(
                    field="CONFIG_SCHEMA_VERSION",
                    message=f"CONFIG_SCHEMA_VERSION missing; migrating to {CONFIG_SCHEMA_VERSION}",
                )
            )
        elif schema_version is not None:
            warnings.append(
                ConfigMigrationIssue(
                    field="CONFIG_SCHEMA_VERSION",
                    message=f"Config schema v{schema_version} migrated to v{CONFIG_SCHEMA_VERSION}",
                )
            )
        normalized["CONFIG_SCHEMA_VERSION"] = CONFIG_SCHEMA_VERSION
        needs_persist = True
    elif schema_version > CONFIG_SCHEMA_VERSION:
        warnings.append(
            ConfigMigrationIssue(
                field="CONFIG_SCHEMA_VERSION",
                message=(
                    f"Config schema v{schema_version} is newer than supported v{CONFIG_SCHEMA_VERSION}; "
                    "using compatible keys only"
                ),
            )
        )
        normalized["CONFIG_SCHEMA_VERSION"] = schema_version
    else:
        normalized["CONFIG_SCHEMA_VERSION"] = CONFIG_SCHEMA_VERSION

    legacy_mac = config.get("BLUETOOTH_MAC")
    if isinstance(legacy_mac, str):
        legacy_mac = legacy_mac.strip().upper()
    else:
        legacy_mac = ""
    if legacy_mac and not normalized.get("BLUETOOTH_DEVICES"):
        normalized["BLUETOOTH_DEVICES"] = [
            {"mac": legacy_mac, "adapter": "", "player_name": "Sendspin Player"},
        ]
        warnings.append(
            ConfigMigrationIssue(
                field="BLUETOOTH_DEVICES",
                message="Migrated legacy BLUETOOTH_MAC into BLUETOOTH_DEVICES",
            )
        )
        needs_persist = True

    legacy_volume = config.get("LAST_VOLUME")
    if legacy_volume is not None and not normalized.get("LAST_VOLUMES"):
        migrated_volumes: dict[str, int] = {}
        target_mac = legacy_mac
        devices = normalized.get("BLUETOOTH_DEVICES", [])
        if not target_mac and isinstance(devices, list) and len(devices) == 1 and isinstance(devices[0], dict):
            maybe_mac = devices[0].get("mac")
            if isinstance(maybe_mac, str):
                target_mac = maybe_mac.strip().upper()
        if target_mac:
            try:
                migrated_value = int(legacy_volume)
            except (TypeError, ValueError):
                migrated_value = None
            if migrated_value is not None and 0 <= migrated_value <= 100:
                migrated_volumes[target_mac] = migrated_value
        normalized["LAST_VOLUMES"] = migrated_volumes
        warnings.append(
            ConfigMigrationIssue(
                field="LAST_VOLUMES",
                message="Migrated legacy LAST_VOLUME into LAST_VOLUMES",
            )
        )
        needs_persist = True

    return ConfigMigrationResult(normalized_config=normalized, warnings=warnings, needs_persist=needs_persist)


def write_config_file(
    config: dict[str, Any], *, config_file: Path | None = None, config_dir: Path | None = None
) -> None:
    target_file = CONFIG_FILE if config_file is None else config_file
    target_dir = CONFIG_DIR if config_dir is None else config_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    tmp_f = tempfile.NamedTemporaryFile(dir=str(target_dir), delete=False, mode="w", suffix=".tmp")  # noqa: SIM115
    try:
        json.dump(config, tmp_f, indent=2)
        tmp_f.flush()
        os.fsync(tmp_f.fileno())
        tmp_f.close()
        os.replace(tmp_f.name, str(target_file))
    except BaseException:
        tmp_f.close()
        try:
            os.unlink(tmp_f.name)
        except OSError:
            pass
        raise


def update_config(mutator) -> None:
    """Atomically read-modify-write config.json under config_lock.

    ``mutator`` is called with the current config dict and should modify it
    in-place.  The result is written to a temp file and atomically renamed.
    """
    with config_lock:
        existing: dict = {}
        if CONFIG_FILE.exists():
            existing = _read_raw_config_file()
        mutator(existing)
        existing.setdefault("CONFIG_SCHEMA_VERSION", CONFIG_SCHEMA_VERSION)
        write_config_file(existing)


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
        normalized_mac = _normalize_mac_key(mac)
        if not normalized_mac:
            return
        cfg.setdefault("LAST_SINKS", {})[normalized_mac] = sink_name.strip()

    try:
        update_config(_set_sink)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("Could not save sink for %s: %s", mac, e)


def load_config() -> dict:
    """Load configuration from file, falling back to defaults."""
    result = copy.deepcopy(DEFAULT_CONFIG)

    if CONFIG_FILE.exists():
        try:
            with config_lock:
                saved_config = _read_raw_config_file()
            migrated = migrate_config_payload(saved_config)
            result.update(migrated.normalized_config)
            for issue in migrated.warnings:
                logger.info("%s", issue.message)
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
        else:
            if migrated.needs_persist:
                try:

                    def _persist_migration(cfg: dict) -> None:
                        cfg.clear()
                        cfg.update(result)

                    update_config(_persist_migration)
                    logger.info("Migrated legacy config keys to current format")
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("Could not persist config migration: %s", exc)
    else:
        logger.info("Config file not found at %s, using defaults", CONFIG_FILE)

    _normalize_loaded_config(result)
    if result.get("CONFIG_SCHEMA_VERSION") != CONFIG_SCHEMA_VERSION:
        logger.warning(
            "Loaded config schema version %r differs from supported version %r",
            result.get("CONFIG_SCHEMA_VERSION"),
            CONFIG_SCHEMA_VERSION,
        )
    else:
        result["CONFIG_SCHEMA_VERSION"] = CONFIG_SCHEMA_VERSION
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
