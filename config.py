"""
Configuration management for sendspin-bt-bridge.

Provides the config file path, a process-wide lock for atomic writes,
and helpers for loading/persisting configuration.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
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

# Backward-compatible re-exports — external code imports these from config.
from config_auth import check_password, hash_password
from config_migration import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_UPDATE_CHANNEL,
    HANDOFF_MODES,
    UPDATE_CHANNELS,
    normalize_handoff_mode,
    normalize_update_channel,
    resolve_device_room_context,
)
from config_network import (  # noqa: F401
    DEFAULT_LISTEN_PORT_BASE,
    DEFAULT_WEB_PORT,
    HA_ADDON_CHANNEL_DEFAULTS,
    detect_ha_addon_channel,
    get_local_ip,
    is_ha_addon_runtime,
    resolve_additional_web_port,
    resolve_base_listen_port,
    resolve_web_port,
)

VERSION = "2.48.0-rc.9"
BUILD_DATE = "2026-03-25"
_RUNTIME_VERSION_REF_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:-(?:rc|beta)\.\d+)?$")

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
    "HANDOFF_MODES",
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
    "get_installed_version_ref",
    "get_local_ip",
    "get_runtime_version",
    "hash_password",
    "is_ha_addon_runtime",
    "load_config",
    "migrate_config_payload",
    "normalize_handoff_mode",
    "normalize_update_channel",
    "resolve_additional_web_port",
    "resolve_base_listen_port",
    "resolve_device_room_context",
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
    "HA_AREA_NAME_ASSIST_ENABLED": False,
    "HA_ADAPTER_AREA_MAP": {},
    "TZ": "Australia/Melbourne",
    "LAST_VOLUMES": {},
    "LAST_SINKS": {},
    "PULSE_LATENCY_MSEC": 600,
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
    "STARTUP_BANNER_GRACE_SECONDS": 5,
    "RECOVERY_BANNER_GRACE_SECONDS": 15,
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
    "DUPLICATE_DEVICE_CHECK": True,
    "TRUSTED_PROXIES": [],
}

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
CONFIG_FILE = CONFIG_DIR / "config.json"
config_lock = threading.RLock()  # serializes all config.json read-modify-write ops
_config_load_log_lock = threading.Lock()
_config_load_logged_once = False
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
    from config_migration import _filter_allowed_config_keys as _mig_filter

    return _mig_filter(config, allowed_keys=CONFIG_ALLOWED_KEYS)


def _log_config_load(message: str, *args: Any) -> None:
    global _config_load_logged_once

    with _config_load_log_lock:
        level = logging.INFO if not _config_load_logged_once else logging.DEBUG
        _config_load_logged_once = True

    logger.log(level, message, *args)


def _changed_config_keys(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    return sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))


def _runtime_version_ref_path() -> Path:
    return Path(os.environ.get("SENDSPIN_VERSION_REF_FILE") or "/opt/sendspin-client/.release-ref")


def get_installed_version_ref() -> str | None:
    """Return the persisted install/update ref when it is an exact semver tag."""
    try:
        raw_ref = _runtime_version_ref_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw_ref or not _RUNTIME_VERSION_REF_RE.match(raw_ref):
        return None
    return raw_ref


def get_runtime_version() -> str:
    """Return the runtime version exposed to the UI and update checker."""
    installed_ref = get_installed_version_ref()
    if installed_ref:
        return installed_ref[1:] if installed_ref.startswith("v") else installed_ref
    return VERSION


def migrate_config_payload(config: dict[str, Any]) -> ConfigMigrationResult:
    """Run schema migration on a raw config dict.

    Delegates to config_migration, passing the allowed-keys set that lives
    in this module so the migration module stays decoupled from config.py.
    """
    from config_migration import migrate_config_payload as _mig_migrate

    return _mig_migrate(config, allowed_keys=CONFIG_ALLOWED_KEYS)


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
        raw = CONFIG_FILE.read_bytes() if CONFIG_FILE.exists() else None
    existing: dict = {}
    if raw is not None:
        existing = json.loads(raw)
        if not isinstance(existing, dict):
            raise ValueError("Config file must contain a JSON object")
    before = copy.deepcopy(existing)
    mutator(existing)
    existing.setdefault("CONFIG_SCHEMA_VERSION", CONFIG_SCHEMA_VERSION)
    changed_keys = _changed_config_keys(before, existing)
    if not changed_keys:
        logger.debug("Config update made no changes for %s", CONFIG_FILE)
        return
    with config_lock:
        write_config_file(existing)
    changed_key_list = ", ".join(changed_keys)
    if set(changed_keys).issubset(RUNTIME_STATE_CONFIG_KEYS):
        logger.debug("Updated runtime config state in %s (%s)", CONFIG_FILE, changed_key_list)
    else:
        logger.info("Updated config at %s (%s)", CONFIG_FILE, changed_key_list)


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
        from config_migration import _normalize_mac_key

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
    has_explicit_ha_area_name_assist = False

    if CONFIG_FILE.exists():
        try:
            with config_lock:
                raw = CONFIG_FILE.read_bytes()
            saved_config = json.loads(raw)
            if not isinstance(saved_config, dict):
                raise ValueError("Config file must contain a JSON object")
            migrated = migrate_config_payload(saved_config)
            result.update(migrated.normalized_config)
            has_explicit_ha_area_name_assist = "HA_AREA_NAME_ASSIST_ENABLED" in migrated.normalized_config
            for issue in migrated.warnings:
                logger.info("%s", issue.message)
            _log_config_load("Loaded config from %s", CONFIG_FILE)
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
                        for key, value in migrated.normalized_config.items():
                            if key not in saved_config or saved_config.get(key) != value:
                                cfg[key] = copy.deepcopy(value)
                        # Remove legacy keys consumed by migration
                        for key in saved_config:
                            if key not in migrated.normalized_config:
                                cfg.pop(key, None)

                    update_config(_persist_migration)
                    logger.info("Migrated legacy config keys to current format")
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("Could not persist config migration: %s", exc)
    else:
        _log_config_load("Config file not found at %s, using defaults", CONFIG_FILE)

    if is_ha_addon_runtime() and not has_explicit_ha_area_name_assist:
        result["HA_AREA_NAME_ASSIST_ENABLED"] = True

    from config_migration import _normalize_loaded_config

    _normalize_loaded_config(result, defaults=DEFAULT_CONFIG)
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
