"""Helpers for explicit config payload validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import (
    CONFIG_SCHEMA_VERSION,
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


def validate_uploaded_config(uploaded: dict[str, Any]) -> ConfigValidationResult:
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

    return result
