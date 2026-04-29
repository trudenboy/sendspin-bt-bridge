"""Normalized bridge/device state payloads shared across status and guidance surfaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from services._helpers import _device_extra
from services.device_health_state import compute_device_health_state


def _obj_get(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


@dataclass
class RuntimeSubstrateStatus:
    status: str
    dbus_available: bool
    memory_ok: bool | None
    memory_mb: int | None
    failed_collections: list[str] = field(default_factory=list)
    collections_status: dict[str, Any] = field(default_factory=dict)
    bluetooth: dict[str, Any] = field(default_factory=dict)
    audio: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConfigurationIntentStatus:
    configured_device_count: int
    enabled_device_count: int
    disabled_device_count: int
    update_channel: str
    ma_configured: bool
    has_auth_password: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedDeviceState:
    player_name: str
    enabled: bool
    management: dict[str, Any]
    bluetooth: dict[str, Any]
    audio: dict[str, Any]
    transport: dict[str, Any]
    async_ops: dict[str, Any]
    music_assistant: dict[str, Any]
    health: dict[str, Any]
    recent_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BridgeStateModel:
    runtime_substrate: RuntimeSubstrateStatus
    configuration: ConfigurationIntentStatus
    runtime_mode: str
    startup_progress: str | None
    ma_connected: bool
    update_available: bool
    disabled_devices: list[dict[str, Any]]
    devices: list[NormalizedDeviceState] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_substrate": self.runtime_substrate.to_dict(),
            "configuration": self.configuration.to_dict(),
            "runtime_mode": self.runtime_mode,
            "startup_progress": self.startup_progress,
            "ma_connected": self.ma_connected,
            "update_available": self.update_available,
            "disabled_devices": list(self.disabled_devices),
            "devices": [device.to_dict() for device in self.devices],
        }


def build_runtime_substrate_status(preflight: dict[str, Any] | None) -> RuntimeSubstrateStatus:
    preflight = dict(preflight or {})
    collections_status = dict(preflight.get("collections_status") or {})
    failed_collections = list(preflight.get("failed_collections") or [])
    bluetooth = dict(preflight.get("bluetooth") or {})
    audio = dict(preflight.get("audio") or {})
    memory = dict(preflight.get("memory") or {})
    dbus_raw = preflight.get("dbus")
    dbus = dict(dbus_raw or {}) if isinstance(dbus_raw, dict) else {}
    dbus_available = bool(dbus.get("available")) if isinstance(dbus_raw, dict) else bool(dbus_raw)
    status = "degraded" if failed_collections else "ok"
    if bluetooth.get("status") == "error" or audio.get("status") == "error":
        status = "error"
    return RuntimeSubstrateStatus(
        status=status,
        dbus_available=dbus_available,
        memory_ok=memory.get("ok"),
        memory_mb=memory.get("available_mb"),
        failed_collections=failed_collections,
        collections_status=collections_status,
        bluetooth=bluetooth,
        audio=audio,
    )


def build_normalized_device_state(device: Any) -> NormalizedDeviceState:
    extra = _device_extra(device)
    health = _obj_get(device, "health_summary") or compute_device_health_state(device).to_dict()
    ma_now_playing = _obj_get(device, "ma_now_playing") or {}
    recent_events = list(_obj_get(device, "recent_events", []) or [])
    bluetooth_mac = _obj_get(device, "bluetooth_mac")
    player_name = str(_obj_get(device, "player_name", "") or "")
    reconnect_attempt = extra.get("reconnect_attempt")
    max_reconnect_fails = extra.get("bt_max_reconnect_fails")
    reconnect_attempts_remaining = None
    if isinstance(reconnect_attempt, int) and isinstance(max_reconnect_fails, int):
        reconnect_attempts_remaining = max(max_reconnect_fails - reconnect_attempt, 0)
    return NormalizedDeviceState(
        player_name=player_name,
        enabled=bool(_obj_get(device, "enabled", True)),
        management={
            "bridge_managed": bool(_obj_get(device, "bt_management_enabled", True)),
            "released": _obj_get(device, "bt_management_enabled", True) is False,
            "release_reason": extra.get("bt_released_by"),
        },
        bluetooth={
            "mac": bluetooth_mac,
            "connected": bool(_obj_get(device, "bluetooth_connected", False)),
            "paired": extra.get("bluetooth_paired"),
            "adapter": extra.get("bluetooth_adapter"),
            "adapter_hci": extra.get("bluetooth_adapter_hci"),
            "adapter_name": extra.get("bluetooth_adapter_name"),
            "reconnect_attempt": reconnect_attempt,
            "max_reconnect_fails": max_reconnect_fails,
            "reconnect_attempts_remaining": reconnect_attempts_remaining,
            "standby": bool(extra.get("bt_standby")),
            "pair_failure_kind": extra.get("pair_failure_kind"),
            "pair_failure_adapter_mac": extra.get("pair_failure_adapter_mac"),
            "pair_failure_at": extra.get("pair_failure_at"),
        },
        audio={
            "has_sink": bool(_obj_get(device, "has_sink", False)),
            "sink_name": extra.get("resolved_sink_name"),
            "streaming": bool(extra.get("audio_streaming")),
        },
        transport={
            "daemon_connected": bool(_obj_get(device, "server_connected", False)),
            "client_connected": bool(_obj_get(device, "connected", False)),
            "url": _obj_get(device, "url"),
            "host": _obj_get(device, "server_ip"),
            "port": _obj_get(device, "server_port"),
            "playing": bool(_obj_get(device, "playing", False)),
        },
        async_ops={
            "reconnecting": bool(extra.get("reconnecting")),
            "ma_reconnecting": bool(extra.get("ma_reconnecting")),
            "stopping": bool(extra.get("stopping")),
            "reanchoring": bool(extra.get("reanchoring")),
        },
        music_assistant={
            "connected": bool(ma_now_playing.get("connected")),
            "group_name": ma_now_playing.get("group_name"),
            "group_id": ma_now_playing.get("group_id"),
        },
        health=dict(health),
        recent_events=recent_events,
    )


def build_bridge_state_model(
    *,
    config: dict[str, Any],
    preflight: dict[str, Any] | None,
    devices: list[Any],
    ma_connected: bool,
    runtime_mode: str,
    startup_progress: dict[str, Any] | None = None,
    update_available: bool = False,
    disabled_devices: list[dict[str, Any]] | None = None,
) -> BridgeStateModel:
    device_configs = list(config.get("devices") or config.get("BLUETOOTH_DEVICES") or [])
    enabled_device_count = sum(1 for device in device_configs if device.get("enabled", True))
    disabled_device_count = len(device_configs) - enabled_device_count
    runtime_substrate = build_runtime_substrate_status(preflight)
    configuration = ConfigurationIntentStatus(
        configured_device_count=len(device_configs),
        enabled_device_count=enabled_device_count,
        disabled_device_count=disabled_device_count,
        update_channel=str(config.get("update_channel") or "stable"),
        ma_configured=bool(config.get("ma_base_url")) or bool(config.get("ma_token")),
        has_auth_password=bool(config.get("AUTH_PASSWORD_HASH")),
    )
    normalized_devices = [build_normalized_device_state(device) for device in devices]
    return BridgeStateModel(
        runtime_substrate=runtime_substrate,
        configuration=configuration,
        runtime_mode=runtime_mode,
        startup_progress=(startup_progress or {}).get("phase"),
        ma_connected=ma_connected,
        update_available=bool(update_available),
        disabled_devices=list(disabled_devices or []),
        devices=normalized_devices,
    )
