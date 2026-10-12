"""Helpers for explicit config payload validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from sendspin_bridge.config import (
    CONFIG_SCHEMA_VERSION,
    DEFAULT_LISTEN_PORT_BASE,
    DEFAULT_UPDATE_CHANNEL,
    UPDATE_CHANNELS,
    migrate_config_payload,
    normalize_update_channel,
)
from sendspin_bridge.config.migration import normalize_device_settings
from sendspin_bridge.config.players import BLUETOOTH, PULSE_SINK, Player, normalize_mac
from sendspin_bridge.services.bluetooth import _MAC_RE


@dataclass(frozen=True)
class ConfigValidationIssue:
    field: str
    message: str


# ---------------------------------------------------------------------------
# SENDSPIN_SERVER format validation
# ---------------------------------------------------------------------------

# Root-cause guard for the issue #291 class of bug — users pasting the full MA
# WebSocket URL (`http://192.168.1.11:8095`) into `SENDSPIN_SERVER`, which the
# bridge then mashed into `ws://http://192.168.1.11:8095:8927/sendspin`.

_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+\-.]*://", re.IGNORECASE)
_PORT_SUFFIX_RE = re.compile(r":\d+$")
_SLASH_RE = re.compile(r"/")
_WHITESPACE_RE = re.compile(r"\s")
_AUTO_VALUES: frozenset[str] = frozenset({"auto", "discover"})


def validate_sendspin_server_format(value: object) -> ConfigValidationIssue | None:
    """Return a ConfigValidationIssue if value is not a bare hostname/IP, else None.

    Empty / ``auto`` / ``discover`` are accepted. The bridge auto-discovers via
    mDNS in those modes.
    """
    if value is None or value == "":
        return None
    raw = str(value).strip()
    if not raw or raw.lower() in _AUTO_VALUES:
        return None
    if _SCHEME_RE.search(raw):
        return ConfigValidationIssue(
            field="SENDSPIN_SERVER",
            message=(
                f"SENDSPIN_SERVER must be a bare hostname or IP — remove the scheme prefix "
                f"from {raw!r} (e.g. use '192.168.1.11', not 'http://192.168.1.11')."
            ),
        )
    if _PORT_SUFFIX_RE.search(raw):
        return ConfigValidationIssue(
            field="SENDSPIN_SERVER",
            message=(
                f"SENDSPIN_SERVER must not include a port — remove the ':<port>' suffix from "
                f"{raw!r} and set SENDSPIN_PORT separately."
            ),
        )
    if _SLASH_RE.search(raw):
        return ConfigValidationIssue(
            field="SENDSPIN_SERVER",
            message=(f"SENDSPIN_SERVER must not contain a path/slash — got {raw!r}; enter just the hostname or IP."),
        )
    if _WHITESPACE_RE.search(raw):
        return ConfigValidationIssue(
            field="SENDSPIN_SERVER",
            message=f"SENDSPIN_SERVER must not contain whitespace (got {raw!r}).",
        )
    return None


def is_valid_sendspin_host(value: object) -> bool:
    """Pure-bool wrapper around :func:`validate_sendspin_server_format` for runtime gates."""
    return validate_sendspin_server_format(value) is None


def resolve_sendspin_url(host: object, port: int) -> str | None:
    """Build the canonical ``ws://host:port/sendspin`` URL the daemon dials.

    Returns ``None`` when *host* is empty / ``auto`` / ``discover`` — those modes
    delegate target resolution to the sendspin library's own mDNS discovery.
    """
    if host is None:
        return None
    raw = str(host).strip()
    if not raw or raw.lower() in _AUTO_VALUES:
        return None
    return f"ws://{raw}:{int(port)}/sendspin"


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
    players = normalized.get("players", [])
    if not isinstance(players, list) or len(players) <= 1:
        return

    base_listen_port_raw = normalized.get("BASE_LISTEN_PORT")
    try:
        if isinstance(base_listen_port_raw, bool):
            base_listen_port = int(default_base_listen_port)
        elif isinstance(base_listen_port_raw, (int, str)) and base_listen_port_raw != "":
            base_listen_port = int(base_listen_port_raw)
        else:
            base_listen_port = int(default_base_listen_port)
    except (ValueError, TypeError):
        base_listen_port = int(default_base_listen_port)

    seen_ports: dict[int, str] = {}
    # The ``base + index`` fallback uses a Bluetooth player's position among
    # the Bluetooth players (what ``device_entries`` yields, and what the
    # orchestrator and ``activate_device`` enumerate), not its index in
    # ``players``: that is the index stage 1 used, so effective ports do not
    # move for existing users. Other backends have no runtime port fallback
    # yet, so only an explicit ``listen_port`` of theirs is checked.
    bluetooth_position = 0
    for index, player in enumerate(players):
        if not isinstance(player, dict):
            continue
        fallback_port: int | None = None
        backend = player.get("backend")
        if isinstance(backend, dict) and backend.get("type") == BLUETOOTH and normalize_mac(backend.get("mac")):
            fallback_port = base_listen_port + bluetooth_position
            bluetooth_position += 1
        if player.get("enabled", True) is False:
            continue

        raw_listen_port = player.get("listen_port")
        if raw_listen_port in (None, ""):
            if fallback_port is None:
                continue
            effective_port = fallback_port
        else:
            try:
                if isinstance(raw_listen_port, (int, str)) and not isinstance(raw_listen_port, bool):
                    effective_port = int(raw_listen_port)
                else:
                    continue
            except (ValueError, TypeError):
                continue

        if not (1 <= effective_port <= 65535):
            continue

        owner = str(player.get("name") or f"player {index + 1}")
        if effective_port in seen_ports:
            result.errors.append(
                ConfigValidationIssue(
                    field=f"players[{index}].listen_port",
                    message=f"Duplicate effective listen_port {effective_port}: also used by {seen_ports[effective_port]}",
                )
            )
            continue
        seen_ports[effective_port] = owner


def _validate_player_settings(player: dict[str, Any], result: ConfigValidationResult, field_prefix: str) -> None:
    """Normalise and check a Bluetooth player's own (non-backend) settings in place."""
    room_id = str(player.get("room_id") or "").strip()
    room_name = str(player.get("room_name") or "").strip()
    if room_id:
        player["room_id"] = room_id
    else:
        player.pop("room_id", None)
    if room_name:
        player["room_name"] = room_name
    else:
        player.pop("room_name", None)
    player.pop("handoff_mode", None)
    keepalive_interval_raw = player.get("keepalive_interval")
    keepalive_interval = _normalize_optional_field_int(
        player,
        result,
        "keepalive_interval",
        issue_field=f"{field_prefix}.keepalive_interval",
        min_value=0,
        max_value=3600,
    )
    _normalize_optional_field_int(
        player,
        result,
        "listen_port",
        issue_field=f"{field_prefix}.listen_port",
        min_value=1024,
        max_value=65535,
    )
    _normalize_optional_field_int(
        player,
        result,
        "static_delay_ms",
        issue_field=f"{field_prefix}.static_delay_ms",
        min_value=0,
        max_value=5000,
    )
    if keepalive_interval is not None and keepalive_interval != 0 and keepalive_interval < 30:
        result.errors.append(
            ConfigValidationIssue(
                field=f"{field_prefix}.keepalive_interval",
                message=(f"Invalid keepalive_interval: {keepalive_interval_raw} (must be 0 or 30-3600)"),
            )
        )


def _validate_bluetooth_player(
    player: dict[str, Any],
    backend: dict[str, Any],
    result: ConfigValidationResult,
    field_prefix: str,
    seen_macs: set[str],
) -> None:
    mac = str(backend.get("mac") or "").strip().upper()
    if not mac:
        result.errors.append(
            ConfigValidationIssue(field=f"{field_prefix}.backend.mac", message="A Bluetooth player needs a MAC address")
        )
    else:
        backend["mac"] = mac
        if not _MAC_RE.match(mac):
            result.errors.append(
                ConfigValidationIssue(field=f"{field_prefix}.backend.mac", message=f"Invalid MAC address: {mac}")
            )
        elif mac in seen_macs:
            result.errors.append(
                ConfigValidationIssue(field=f"{field_prefix}.backend.mac", message=f"Duplicate MAC address: {mac}")
            )
        else:
            seen_macs.add(mac)
    backend["adapter"] = str(backend.get("adapter") or "").strip()
    _validate_player_settings(player, result, field_prefix)
    normalize_device_settings(player)


def _validate_other_player(player: dict[str, Any], result: ConfigValidationResult, field_prefix: str) -> None:
    """Check a non-Bluetooth player against the ``Player`` model and keep its coerced values."""
    # A new player has no id yet (the save assigns one), so validate it with a stand-in.
    missing_id = player.get("id") is None
    candidate = {**player, "id": "new-player"} if missing_id else player
    try:
        validated = Player.model_validate(candidate)
    except ValidationError as exc:
        for error in exc.errors(include_url=False):
            loc = [str(part) for part in error["loc"]]
            # A discriminated union puts the backend tag in the path: backend.pulse_sink.sink.
            if len(loc) >= 2 and loc[0] == "backend" and loc[1] in (BLUETOOTH, PULSE_SINK):
                del loc[1]
            field_path = ".".join([field_prefix, *loc])
            result.errors.append(ConfigValidationIssue(field=field_path, message=error["msg"]))
        return
    player.update(validated.model_dump(mode="json", exclude_unset=True))
    if missing_id:
        player.pop("id", None)


def _check_legacy_device_list(uploaded: dict[str, Any]) -> list[ConfigValidationIssue]:
    """Errors in a v5 upload's ``BLUETOOTH_DEVICES``, in the uploaded shape.

    The v6 conversion skips a bad or duplicate MAC with only a warning, so
    without this an imported v5 file would silently lose speakers. When the
    upload also has ``players`` the device list is ignored, so it is not checked.
    """
    if "BLUETOOTH_DEVICES" not in uploaded or "players" in uploaded:
        return []
    devices = uploaded["BLUETOOTH_DEVICES"]
    if not isinstance(devices, list):
        return [ConfigValidationIssue(field="BLUETOOTH_DEVICES", message="BLUETOOTH_DEVICES must be an array")]
    issues: list[ConfigValidationIssue] = []
    seen_macs: set[str] = set()
    for index, device in enumerate(devices):
        field_prefix = f"BLUETOOTH_DEVICES[{index}]"
        if not isinstance(device, dict):
            issues.append(ConfigValidationIssue(field=field_prefix, message="Each device must be an object"))
            continue
        mac = str(device.get("mac", "")).strip().upper()
        if not mac:
            continue
        if not _MAC_RE.match(mac):
            issues.append(ConfigValidationIssue(field=f"{field_prefix}.mac", message=f"Invalid MAC address: {mac}"))
        elif mac in seen_macs:
            issues.append(ConfigValidationIssue(field=f"{field_prefix}.mac", message=f"Duplicate MAC address: {mac}"))
        else:
            seen_macs.add(mac)
    return issues


def validate_uploaded_config(
    uploaded: dict[str, Any], *, default_base_listen_port: int = DEFAULT_LISTEN_PORT_BASE
) -> ConfigValidationResult:
    """Validate an uploaded config payload and normalize additive defaults.

    The result is schema v6 (``players``); a v5 upload (``BLUETOOTH_DEVICES``)
    is converted first, which is how import keeps accepting v5 files. Player
    issues are reported as ``players[<index in players>].<key>``, backend keys
    as ``players[<i>].backend.<key>``.
    """
    legacy_issues = _check_legacy_device_list(uploaded)
    migration = migrate_config_payload(uploaded)
    normalized = migration.normalized_config
    result = ConfigValidationResult(normalized_config=normalized)
    result.errors.extend(legacy_issues)
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

    if "players" in normalized:
        players = normalized["players"]
        if not isinstance(players, list):
            result.errors.append(ConfigValidationIssue(field="players", message="players must be an array"))
        else:
            seen_macs: set[str] = set()
            for index, player in enumerate(players):
                field_prefix = f"players[{index}]"
                if not isinstance(player, dict):
                    result.errors.append(
                        ConfigValidationIssue(field=field_prefix, message="Each player must be an object")
                    )
                    continue
                backend = player.get("backend")
                if isinstance(backend, dict) and backend.get("type") == BLUETOOTH:
                    _validate_bluetooth_player(player, backend, result, field_prefix, seen_macs)
                else:
                    _validate_other_player(player, result, field_prefix)

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

    server_issue = validate_sendspin_server_format(normalized.get("SENDSPIN_SERVER"))
    if server_issue is not None:
        result.errors.append(server_issue)

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
