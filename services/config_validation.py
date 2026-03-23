"""Helpers for explicit config payload validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_LISTEN_PORT_BASE,
    DEFAULT_UPDATE_CHANNEL,
    UPDATE_CHANNELS,
    migrate_config_payload,
    normalize_update_channel,
)
from services.bluetooth import _MAC_RE


@dataclass(frozen=True)
class ConfigValidationIssue:
    field: str
    message: str


@dataclass
class ConfigValidationResult:
    normalized_config: dict[str, Any]
    errors: list[ConfigValidationIssue] = field(default_factory=list)
    warnings: list[ConfigValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _normalize_optional_port(normalized: dict[str, Any], result: ConfigValidationResult, field: str) -> None:
    raw_value = normalized.get(field)
    if raw_value in (None, ""):
        normalized[field] = None
        return
    try:
        value = int(str(raw_value))
    except (ValueError, TypeError):
        result.errors.append(ConfigValidationIssue(field=field, message=f"Invalid {field}: {raw_value}"))
        return
    if not (1 <= value <= 65535):
        result.errors.append(ConfigValidationIssue(field=field, message=f"Invalid {field}: {raw_value}"))
        return
    normalized[field] = value


def _normalize_optional_field_int(
    payload: dict[str, Any],
    result: ConfigValidationResult,
    field: str,
    *,
    issue_field: str,
    min_value: int,
    max_value: int,
) -> int | None:
    raw_value = payload.get(field)
    if raw_value in (None, ""):
        payload.pop(field, None)
        return None
    try:
        value = int(str(raw_value))
    except (ValueError, TypeError):
        result.errors.append(ConfigValidationIssue(field=issue_field, message=f"Invalid {field}: {raw_value}"))
        return None
    if not (min_value <= value <= max_value):
        result.errors.append(ConfigValidationIssue(field=issue_field, message=f"Invalid {field}: {raw_value}"))
        return None
    payload[field] = value
    return value


def _validate_effective_listen_ports(
    normalized: dict[str, Any],
    result: ConfigValidationResult,
    *,
    default_base_listen_port: int,
) -> None:
    bt_devices = normalized.get("BLUETOOTH_DEVICES", [])
    if not isinstance(bt_devices, list) or len(bt_devices) <= 1:
        return

    base_listen_port_raw = normalized.get("BASE_LISTEN_PORT")
    try:
        if isinstance(base_listen_port_raw, bool) or (
            isinstance(base_listen_port_raw, (int, str)) and base_listen_port_raw != ""
        ):
            base_listen_port = int(base_listen_port_raw)
        else:
            base_listen_port = int(default_base_listen_port)
    except (ValueError, TypeError):
        base_listen_port = int(default_base_listen_port)

    seen_ports: dict[int, str] = {}
    for index, dev in enumerate(bt_devices):
        if not isinstance(dev, dict) or dev.get("enabled", True) is False:
            continue

        raw_listen_port = dev.get("listen_port")
        if raw_listen_port in (None, ""):
            effective_port = base_listen_port + index
        else:
            try:
                if isinstance(raw_listen_port, bool | int | str):
                    effective_port = int(raw_listen_port)
                else:
                    continue
            except (ValueError, TypeError):
                continue

        if not (1 <= effective_port <= 65535):
            continue

        owner = str(dev.get("player_name") or dev.get("mac") or f"device {index + 1}")
        if effective_port in seen_ports:
            result.errors.append(
                ConfigValidationIssue(
                    field=f"BLUETOOTH_DEVICES[{index}].listen_port",
                    message=f"Duplicate effective listen_port {effective_port}: also used by {seen_ports[effective_port]}",
                )
            )
            continue
        seen_ports[effective_port] = owner


def validate_uploaded_config(
    uploaded: dict[str, Any], *, default_base_listen_port: int = DEFAULT_LISTEN_PORT_BASE
) -> ConfigValidationResult:
    """Validate an uploaded config payload and normalize additive defaults."""
    migration = migrate_config_payload(uploaded)
    normalized = migration.normalized_config
    result = ConfigValidationResult(normalized_config=normalized)
    result.warnings.extend(
        ConfigValidationIssue(field=issue.field, message=issue.message) for issue in migration.warnings
    )

    schema_version: object = normalized.get("CONFIG_SCHEMA_VERSION")
    try:
        normalized["CONFIG_SCHEMA_VERSION"] = int(str(schema_version))
    except (TypeError, ValueError):
        result.errors.append(
            ConfigValidationIssue(
                field="CONFIG_SCHEMA_VERSION",
                message=f"Invalid CONFIG_SCHEMA_VERSION: {schema_version}",
            )
        )
    else:
        if normalized["CONFIG_SCHEMA_VERSION"] > CONFIG_SCHEMA_VERSION:
            result.errors.append(
                ConfigValidationIssue(
                    field="CONFIG_SCHEMA_VERSION",
                    message=(
                        f"Unsupported CONFIG_SCHEMA_VERSION: {normalized['CONFIG_SCHEMA_VERSION']} "
                        f"(max supported {CONFIG_SCHEMA_VERSION})"
                    ),
                )
            )
        elif normalized["CONFIG_SCHEMA_VERSION"] < CONFIG_SCHEMA_VERSION:
            result.warnings.append(
                ConfigValidationIssue(
                    field="CONFIG_SCHEMA_VERSION",
                    message=(
                        f"CONFIG_SCHEMA_VERSION {normalized['CONFIG_SCHEMA_VERSION']} will be migrated "
                        f"to {CONFIG_SCHEMA_VERSION}"
                    ),
                )
            )
            normalized["CONFIG_SCHEMA_VERSION"] = CONFIG_SCHEMA_VERSION

    update_channel = normalized.get("UPDATE_CHANNEL")
    if update_channel in (None, ""):
        normalized["UPDATE_CHANNEL"] = DEFAULT_UPDATE_CHANNEL
    elif not isinstance(update_channel, str):
        result.errors.append(
            ConfigValidationIssue(
                field="UPDATE_CHANNEL",
                message=f"Invalid UPDATE_CHANNEL: {update_channel}",
            )
        )
    else:
        normalized_channel = normalize_update_channel(update_channel)
        if normalized_channel not in UPDATE_CHANNELS or normalized_channel != update_channel.strip().lower():
            if update_channel.strip().lower() not in UPDATE_CHANNELS:
                result.errors.append(
                    ConfigValidationIssue(
                        field="UPDATE_CHANNEL",
                        message=f"Invalid UPDATE_CHANNEL: {update_channel}",
                    )
                )
            else:
                normalized["UPDATE_CHANNEL"] = normalized_channel
        else:
            normalized["UPDATE_CHANNEL"] = normalized_channel

    bt_devices = normalized.get("BLUETOOTH_DEVICES", [])
    if not isinstance(bt_devices, list):
        result.errors.append(
            ConfigValidationIssue(
                field="BLUETOOTH_DEVICES",
                message="BLUETOOTH_DEVICES must be an array",
            )
        )
    else:
        seen_macs: set[str] = set()
        for index, dev in enumerate(bt_devices):
            field_prefix = f"BLUETOOTH_DEVICES[{index}]"
            if not isinstance(dev, dict):
                result.errors.append(ConfigValidationIssue(field=field_prefix, message="Each device must be an object"))
                continue
            mac = str(dev.get("mac", "")).strip().upper()
            if mac:
                normalized["BLUETOOTH_DEVICES"][index]["mac"] = mac
                if not _MAC_RE.match(mac):
                    result.errors.append(
                        ConfigValidationIssue(field=f"{field_prefix}.mac", message=f"Invalid MAC address: {mac}")
                    )
                elif mac in seen_macs:
                    result.errors.append(
                        ConfigValidationIssue(field=f"{field_prefix}.mac", message=f"Duplicate MAC address: {mac}")
                    )
                else:
                    seen_macs.add(mac)
            keepalive_interval_raw = dev.get("keepalive_interval")
            keepalive_interval = _normalize_optional_field_int(
                normalized["BLUETOOTH_DEVICES"][index],
                result,
                "keepalive_interval",
                issue_field=f"{field_prefix}.keepalive_interval",
                min_value=0,
                max_value=3600,
            )
            _normalize_optional_field_int(
                normalized["BLUETOOTH_DEVICES"][index],
                result,
                "listen_port",
                issue_field=f"{field_prefix}.listen_port",
                min_value=1024,
                max_value=65535,
            )
            if keepalive_interval is not None and keepalive_interval != 0 and keepalive_interval < 30:
                result.errors.append(
                    ConfigValidationIssue(
                        field=f"{field_prefix}.keepalive_interval",
                        message=(f"Invalid keepalive_interval: {keepalive_interval_raw} (must be 0 or 30-3600)"),
                    )
                )

    bt_adapters = normalized.get("BLUETOOTH_ADAPTERS", [])
    if not isinstance(bt_adapters, list):
        result.errors.append(
            ConfigValidationIssue(
                field="BLUETOOTH_ADAPTERS",
                message="BLUETOOTH_ADAPTERS must be an array",
            )
        )
    else:
        for index, adapter in enumerate(bt_adapters):
            field_prefix = f"BLUETOOTH_ADAPTERS[{index}]"
            if not isinstance(adapter, dict):
                result.errors.append(
                    ConfigValidationIssue(field=field_prefix, message="Each adapter must be an object")
                )
                continue
            adapter_mac = str(adapter.get("mac", "")).strip().upper()
            if adapter_mac:
                normalized["BLUETOOTH_ADAPTERS"][index]["mac"] = adapter_mac
                if not _MAC_RE.match(adapter_mac):
                    result.errors.append(
                        ConfigValidationIssue(
                            field=f"{field_prefix}.mac",
                            message=f"Invalid adapter MAC address: {adapter_mac}",
                        )
                    )

    sp: object = normalized.get("SENDSPIN_PORT")
    if sp not in (None, ""):
        try:
            sp_int = int(str(sp))
        except (ValueError, TypeError):
            result.errors.append(
                ConfigValidationIssue(
                    field="SENDSPIN_PORT",
                    message=f"Invalid SENDSPIN_PORT: {sp}",
                )
            )
        else:
            if not (1 <= sp_int <= 65535):
                result.errors.append(
                    ConfigValidationIssue(field="SENDSPIN_PORT", message=f"Invalid SENDSPIN_PORT: {sp}")
                )
            else:
                normalized["SENDSPIN_PORT"] = sp_int

    for port_field in ("WEB_PORT", "BASE_LISTEN_PORT"):
        _normalize_optional_port(normalized, result, port_field)

    _validate_effective_listen_ports(
        normalized,
        result,
        default_base_listen_port=default_base_listen_port,
    )

    return result
