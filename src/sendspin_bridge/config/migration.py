"""Config schema migration and normalization."""

from __future__ import annotations

import copy
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sendspin_bridge.config import ConfigMigrationIssue, ConfigMigrationResult

logger = logging.getLogger(__name__)

CONFIG_SCHEMA_VERSION = 2
UPDATE_CHANNELS = ("stable", "rc", "beta")
DEFAULT_UPDATE_CHANNEL = "stable"
_VALID_IDLE_MODES = frozenset(("default", "power_save", "auto_disconnect", "keep_alive"))


def normalize_update_channel(raw_channel: object) -> str:
    """Return a supported update channel name."""
    if not isinstance(raw_channel, str):
        return DEFAULT_UPDATE_CHANNEL
    normalized = raw_channel.strip().lower()
    if normalized in UPDATE_CHANNELS:
        return normalized
    return DEFAULT_UPDATE_CHANNEL


def _normalize_int_setting(
    config: dict, key: str, *, defaults: Mapping[str, Any], min_value: int | None = None, max_value: int | None = None
) -> None:
    raw = config.get(key, defaults[key])
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s value %r in config; using default %r", key, raw, defaults[key])
        config[key] = defaults[key]
        return
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        logger.warning("Out-of-range %s value %r in config; using default %r", key, raw, defaults[key])
        config[key] = defaults[key]
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


def _normalize_bool_setting(config: dict, key: str, *, defaults: Mapping[str, Any]) -> None:
    raw = config.get(key, defaults[key])
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
    logger.warning("Invalid %s value %r in config; using default %r", key, raw, defaults[key])
    config[key] = defaults[key]


def _normalize_float_setting(
    config: dict,
    key: str,
    *,
    defaults: Mapping[str, Any],
    min_value: float | None = None,
    max_value: float | None = None,
) -> None:
    raw = config.get(key, defaults[key])
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s value %r in config; using default %r", key, raw, defaults[key])
        config[key] = defaults[key]
        return
    if (min_value is not None and value < min_value) or (max_value is not None and value > max_value):
        logger.warning("Out-of-range %s value %r in config; using default %r", key, raw, defaults[key])
        config[key] = defaults[key]
        return
    config[key] = value


def _normalize_choice_setting(config: dict, key: str, *, allowed_values: tuple[str, ...], default: str) -> None:
    raw = config.get(key, default)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in allowed_values:
            config[key] = normalized
            return
    logger.warning("Invalid %s value %r in config; using default %r", key, raw, default)
    config[key] = default


def _normalize_bluetooth_devices(config: dict, *, defaults: Mapping[str, Any]) -> list[dict]:
    devices = config.get("BLUETOOTH_DEVICES", defaults["BLUETOOTH_DEVICES"])
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
        room_id = str(normalized.get("room_id") or "").strip()
        room_name = str(normalized.get("room_name") or "").strip()
        if room_id:
            normalized["room_id"] = room_id
        else:
            normalized.pop("room_id", None)
        if room_name:
            normalized["room_name"] = room_name
        else:
            normalized.pop("room_name", None)
        normalized.pop("handoff_mode", None)
        _migrate_negative_static_delay(normalized)
        _migrate_device_idle_mode(normalized)
        _migrate_power_save_delay(normalized)
        normalized_devices.append(normalized)
    return normalized_devices


_warned_static_delay_issues: set[tuple[str, str]] = set()


def _migrate_negative_static_delay(device: dict) -> None:
    """Normalize static_delay_ms to a numeric value within 0-5000 for sendspin 7.0+."""
    raw = device.get("static_delay_ms")
    if raw is None:
        return
    key = str(device.get("mac", device.get("player_name", "?")))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        if (key, "invalid") not in _warned_static_delay_issues:
            _warned_static_delay_issues.add((key, "invalid"))
            logger.warning("Device %s: static_delay_ms=%r is invalid; removing field", key, raw)
        device.pop("static_delay_ms", None)
        return
    if value < 0:
        if (key, "negative") not in _warned_static_delay_issues:
            _warned_static_delay_issues.add((key, "negative"))
            logger.warning(
                "Device %s: static_delay_ms=%s is negative; clamping to 0 (sendspin 7.0+ uses DAC-anchored sync)",
                key,
                raw,
            )
        device["static_delay_ms"] = 0
    elif value > 5000:
        if (key, "high") not in _warned_static_delay_issues:
            _warned_static_delay_issues.add((key, "high"))
            logger.warning("Device %s: static_delay_ms=%s exceeds 5000; clamping to 5000", key, raw)
        device["static_delay_ms"] = 5000


def _migrate_device_idle_mode(device: dict) -> None:
    """Auto-migrate legacy keepalive/idle fields to idle_mode if absent.

    Precedence: keepalive > idle_disconnect > default.
    Invalid idle_mode values are removed.
    """
    existing = device.get("idle_mode")
    if existing is not None:
        if existing not in _VALID_IDLE_MODES:
            logger.warning("Invalid idle_mode %r for device %s; removing", existing, device.get("mac", "?"))
            device.pop("idle_mode")
        return

    keepalive_interval = int(device.get("keepalive_interval") or 0)
    keepalive_enabled = bool(device.get("keepalive_enabled"))
    idle_disconnect = int(device.get("idle_disconnect_minutes") or 0)

    if keepalive_interval > 0 or keepalive_enabled:
        device["idle_mode"] = "keep_alive"
    elif idle_disconnect > 0:
        device["idle_mode"] = "auto_disconnect"


def _migrate_power_save_delay(device: dict) -> None:
    """Convert legacy power_save_delay_seconds to power_save_delay_minutes."""
    old = device.pop("power_save_delay_seconds", None)
    if old is not None and "power_save_delay_minutes" not in device:
        seconds = int(old)
        device["power_save_delay_minutes"] = max(1, round(seconds / 60))


def _normalize_mac_key(raw_mac: object) -> str:
    if not isinstance(raw_mac, str):
        return ""
    return raw_mac.strip().upper()


def _prune_last_volumes(config: dict, *, defaults: Mapping[str, Any]) -> None:
    last_volumes = config.get("LAST_VOLUMES", defaults["LAST_VOLUMES"])
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


def _prune_last_sinks(config: dict, *, defaults: Mapping[str, Any]) -> None:
    last_sinks = config.get("LAST_SINKS", defaults["LAST_SINKS"])
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


def _normalize_adapter_area_map(config: dict, *, defaults: Mapping[str, Any]) -> None:
    raw_mapping = config.get("HA_ADAPTER_AREA_MAP", defaults["HA_ADAPTER_AREA_MAP"])
    if not isinstance(raw_mapping, dict):
        logger.warning("Invalid HA_ADAPTER_AREA_MAP value %r in config; using default {}", raw_mapping)
        config["HA_ADAPTER_AREA_MAP"] = {}
        return

    normalized: dict[str, dict[str, str]] = {}
    for raw_mac, raw_entry in raw_mapping.items():
        mac = _normalize_mac_key(raw_mac)
        if not mac:
            logger.warning("Ignoring invalid HA adapter area key %r", raw_mac)
            continue
        if not isinstance(raw_entry, dict):
            logger.warning("Ignoring invalid HA adapter area entry for %s: %r", mac, raw_entry)
            continue
        area_id = str(raw_entry.get("area_id") or "").strip()
        area_name = str(raw_entry.get("area_name") or "").strip()
        if not area_id:
            logger.warning("Ignoring HA adapter area entry without area_id for %s", mac)
            continue
        normalized_entry = {"area_id": area_id}
        if area_name:
            normalized_entry["area_name"] = area_name
        normalized[mac] = normalized_entry
    config["HA_ADAPTER_AREA_MAP"] = normalized


def _normalize_ha_area_name_assist_enabled(config: dict, *, defaults: Mapping[str, Any]) -> None:
    from sendspin_bridge.config.network import is_ha_addon_runtime

    default_enabled = is_ha_addon_runtime()
    raw_value = config.get("HA_AREA_NAME_ASSIST_ENABLED", defaults["HA_AREA_NAME_ASSIST_ENABLED"])
    if isinstance(raw_value, bool):
        config["HA_AREA_NAME_ASSIST_ENABLED"] = raw_value
        return
    if raw_value not in (None, ""):
        logger.warning(
            "Invalid HA_AREA_NAME_ASSIST_ENABLED value %r in config; using runtime default %r",
            raw_value,
            default_enabled,
        )
    config["HA_AREA_NAME_ASSIST_ENABLED"] = default_enabled


def _normalize_ha_integration(config: dict, *, defaults: Mapping[str, Any]) -> None:
    """Normalise the ``HA_INTEGRATION`` block.

    The only fix-up needed today is rewriting ``mode == "both"`` from
    rc.1/rc.2 saved configs to ``"mqtt"``.  Both kicked off MQTT
    publishing AND mDNS advertisement at once, which led to duplicate HA
    entities (one set per transport).  v2.65.0-rc.3 collapsed the choice
    to one transport at a time; carry forward the MQTT half because that
    was the high-cost setup (broker creds) and operators rarely typed
    those by accident.
    """
    block = config.get("HA_INTEGRATION")
    if not isinstance(block, dict):
        return
    raw_mode = block.get("mode")
    if not isinstance(raw_mode, str):
        return
    if raw_mode.strip().lower() == "both":
        block["mode"] = "mqtt"
        logger.info(
            "HA_INTEGRATION.mode 'both' is no longer supported (v2.65.0-rc.3 dropped it). "
            "Coerced to 'mqtt' on load.  Switch to 'rest' explicitly if you only want the "
            "REST/custom_component transport."
        )


def _normalize_loaded_config(config: dict, *, defaults: Mapping[str, Any]) -> None:
    config["BLUETOOTH_DEVICES"] = _normalize_bluetooth_devices(config, defaults=defaults)
    _prune_last_volumes(config, defaults=defaults)
    _prune_last_sinks(config, defaults=defaults)
    _normalize_ha_area_name_assist_enabled(config, defaults=defaults)
    _normalize_adapter_area_map(config, defaults=defaults)
    _normalize_ha_integration(config, defaults=defaults)

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
        ("STARTUP_BANNER_GRACE_SECONDS", 0, 300),
        ("RECOVERY_BANNER_GRACE_SECONDS", 0, 300),
    ):
        _normalize_int_setting(config, key, defaults=defaults, min_value=min_value, max_value=max_value)

    for key in ("WEB_PORT", "BASE_LISTEN_PORT"):
        _normalize_optional_int_setting(config, key, min_value=1, max_value=65535)

    _normalize_float_setting(config, "BT_CHURN_WINDOW", defaults=defaults, min_value=1.0, max_value=86400.0)
    _normalize_choice_setting(
        config,
        "UPDATE_CHANNEL",
        allowed_values=UPDATE_CHANNELS,
        default=DEFAULT_UPDATE_CHANNEL,
    )

    # ``EXPERIMENTAL_RSSI_BADGE`` was promoted to ``RSSI_BADGE`` (default
    # True) in v2.64.0 once it stabilised.  Migrate any pre-existing
    # config silently — preserve the user's setting (whether True or
    # False) under the new key, then drop the old key.  If both are
    # present the new key wins (operator may have edited it directly).
    if "EXPERIMENTAL_RSSI_BADGE" in config:
        if "RSSI_BADGE" not in config:
            config["RSSI_BADGE"] = config["EXPERIMENTAL_RSSI_BADGE"]
        config.pop("EXPERIMENTAL_RSSI_BADGE", None)

    for key in (
        "PREFER_SBC_CODEC",
        "AUTH_ENABLED",
        "BRUTE_FORCE_PROTECTION",
        "MA_AUTO_SILENT_AUTH",
        "MA_WEBSOCKET_MONITOR",
        "SMOOTH_RESTART",
        "AUTO_UPDATE",
        "CHECK_UPDATES",
        "EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE",
        "EXPERIMENTAL_PA_MODULE_RELOAD",
        "EXPERIMENTAL_PAIR_JUST_WORKS",
        "EXPERIMENTAL_ADAPTER_AUTO_RECOVERY",
        "EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE",
        "RSSI_BADGE",
    ):
        _normalize_bool_setting(config, key, defaults=defaults)

    for key in ("BLUETOOTH_ADAPTERS", "TRUSTED_PROXIES"):
        value = config.get(key, defaults.get(key, []))
        if not isinstance(value, list):
            logger.warning("Invalid %s value %r in config; using default []", key, value)
            config[key] = []

    config["BLUETOOTH_ADAPTERS"] = _normalize_bluetooth_adapters(config.get("BLUETOOTH_ADAPTERS", []))


_DEVICE_CLASS_HEX_RE = re.compile(r"^0x[0-9a-fA-F]{6}$")


def _normalize_bluetooth_adapters(adapters: Any) -> list[dict]:
    """Normalise BLUETOOTH_ADAPTERS entries.

    Entries are dicts; we currently only sanitise the ``device_class``
    field (added in v2.65.1 for the Samsung Q-series CoD-filter workaround,
    bluez/bluez#1025).  Invalid hex strings are dropped with a warning so a
    typo in main.conf doesn't propagate to the kernel mgmt call at startup.
    """
    if not isinstance(adapters, list):
        return []

    normalized: list[dict] = []
    for entry in adapters:
        if not isinstance(entry, dict):
            logger.warning("Ignoring invalid Bluetooth adapter entry %r", entry)
            continue
        clean = dict(entry)
        raw_class = clean.get("device_class")
        if raw_class is None or raw_class == "":
            clean.pop("device_class", None)
        elif isinstance(raw_class, str) and _DEVICE_CLASS_HEX_RE.match(raw_class.strip()):
            clean["device_class"] = raw_class.strip().lower()
        else:
            logger.warning(
                "Adapter %s: invalid device_class %r — must be six hex chars (e.g. '0x00010c'); dropping",
                clean.get("mac", "?"),
                raw_class,
            )
            clean.pop("device_class", None)
        normalized.append(clean)
    return normalized


def _configured_device_matches(
    device_config: dict[str, Any],
    *,
    player_name: str = "",
    device_mac: str = "",
) -> bool:
    configured_mac = _normalize_mac_key(device_config.get("mac"))
    if device_mac and configured_mac and configured_mac == device_mac:
        return True
    configured_name = str(device_config.get("player_name") or "").strip()
    if player_name and configured_name:
        from sendspin_bridge.services.bluetooth import _match_player_name

        return bool(_match_player_name(configured_name, player_name))
    return False


def resolve_device_room_context(
    config: dict[str, Any] | None,
    *,
    player_name: str = "",
    device_mac: str = "",
    adapter_mac: str = "",
) -> dict[str, str]:
    """Resolve room metadata for a configured Bluetooth device."""
    resolved = {
        "room_id": "",
        "room_name": "",
        "room_source": "unknown",
        "room_confidence": "",
    }
    if not isinstance(config, dict):
        return resolved

    normalized_device_mac = _normalize_mac_key(device_mac)
    normalized_adapter_mac = _normalize_mac_key(adapter_mac)
    configured_device = None
    for device in config.get("BLUETOOTH_DEVICES", []) or []:
        if isinstance(device, dict) and _configured_device_matches(
            device,
            player_name=str(player_name or "").strip(),
            device_mac=normalized_device_mac,
        ):
            configured_device = device
            break

    if isinstance(configured_device, dict):
        room_id = str(configured_device.get("room_id") or "").strip()
        room_name = str(configured_device.get("room_name") or "").strip()
        if room_id or room_name:
            resolved["room_id"] = room_id
            resolved["room_name"] = room_name
            resolved["room_source"] = "manual"
            resolved["room_confidence"] = "operator"
            return resolved

    if bool(config.get("HA_AREA_NAME_ASSIST_ENABLED")) and normalized_adapter_mac:
        adapter_map = config.get("HA_ADAPTER_AREA_MAP") or {}
        if isinstance(adapter_map, dict):
            area_entry = adapter_map.get(normalized_adapter_mac) or {}
            if isinstance(area_entry, dict):
                area_id = str(area_entry.get("area_id") or "").strip()
                area_name = str(area_entry.get("area_name") or "").strip()
                if area_id or area_name:
                    resolved["room_id"] = area_id
                    resolved["room_name"] = area_name
                    resolved["room_source"] = "ha_area"
                    resolved["room_confidence"] = "adapter_mac"
    return resolved


def _filter_allowed_config_keys(config: dict[str, Any], *, allowed_keys: frozenset[str]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in config.items() if key in allowed_keys}


def migrate_config_payload(config: dict[str, Any], *, allowed_keys: frozenset[str]) -> ConfigMigrationResult:
    from sendspin_bridge.config import ConfigMigrationIssue, ConfigMigrationResult

    normalized = _filter_allowed_config_keys(config, allowed_keys=allowed_keys)
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
