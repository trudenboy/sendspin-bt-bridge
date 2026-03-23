"""Read-side status snapshot models and builders.

These helpers normalize bridge/device status reads for API routes without
changing the underlying runtime ownership yet. The builders are intentionally
compatibility-first: they preserve the current JSON shape closely while giving
the codebase typed read models to build on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import state
from config import BUILD_DATE, HANDOFF_MODES, get_runtime_version, load_config, resolve_device_room_context
from services.bluetooth import _match_player_name
from services.bridge_state_model import build_normalized_device_state
from services.device_health_state import build_device_capabilities, compute_device_health_state
from state import get_adapter_name, get_ma_group_for_player_id, get_ma_now_playing_for_group

UTC = timezone.utc


@dataclass
class DeviceSnapshot:
    connected: bool = False
    server_connected: bool = False
    bluetooth_connected: bool = False
    bluetooth_available: bool = False
    playing: bool = False
    error: str | None = None
    version: str = field(default_factory=get_runtime_version)
    build_date: str = BUILD_DATE
    bluetooth_mac: str | None = None
    player_name: str | None = None
    listen_port: int | None = None
    server_host: str | None = None
    server_port: int | None = None
    static_delay_ms: float | None = None
    connected_server_url: str = ""
    bluetooth_adapter: str | None = None
    bluetooth_adapter_name: str | None = None
    bluetooth_adapter_hci: str = ""
    has_sink: bool = False
    sink_name: str | None = None
    enabled: bool = True
    bt_management_enabled: bool = True
    battery_level: int | None = None
    room_id: str | None = None
    room_name: str | None = None
    room_source: str | None = None
    room_confidence: str | None = None
    handoff_mode: str = HANDOFF_MODES[0]
    transfer_readiness: dict[str, Any] | None = None
    runtime: str | None = None
    uptime: str | None = None
    ma_syncgroup_id: str | None = None
    ma_now_playing: dict[str, Any] | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    health_summary: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    state_model: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {})
        data.update(extra)
        return data


@dataclass
class GroupMemberSnapshot:
    player_name: str | None
    player_id: str
    volume: int
    playing: bool
    connected: bool
    server_connected: bool
    bluetooth_connected: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceHealthSummary:
    state: str
    severity: str
    summary: str
    reasons: list[str] = field(default_factory=list)
    last_event_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StartupProgressSnapshot:
    status: str
    phase: str
    current_step: int
    total_steps: int
    percent: int
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MockRuntimeLayerSnapshot:
    layer: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MockRuntimeSnapshot:
    mode: str
    is_mocked: bool
    simulator_active: bool
    fixture_devices: int
    fixture_groups: int
    disclaimer: str
    mocked_layers: list[MockRuntimeLayerSnapshot] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "is_mocked": self.is_mocked,
            "simulator_active": self.simulator_active,
            "fixture_devices": self.fixture_devices,
            "fixture_groups": self.fixture_groups,
            "disclaimer": self.disclaimer,
            "mocked_layers": [layer.to_dict() for layer in self.mocked_layers],
            "details": dict(self.details),
            "updated_at": self.updated_at,
        }


@dataclass
class GroupSnapshot:
    group_id: str | None
    group_name: str | None
    members: list[GroupMemberSnapshot]
    avg_volume: int
    playing: bool
    external_members: list[dict[str, Any]] = field(default_factory=list)
    external_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "group_name": self.group_name,
            "members": [member.to_dict() for member in self.members],
            "avg_volume": self.avg_volume,
            "playing": self.playing,
            "external_members": list(self.external_members),
            "external_count": self.external_count,
        }


@dataclass
class BridgeSnapshot:
    devices: list[DeviceSnapshot]
    groups: list[GroupSnapshot]
    ma_connected: bool
    disabled_devices: list[dict[str, Any]]
    system_info: dict[str, Any] = field(default_factory=dict)
    ma_web_url: str | None = None
    update_available: dict[str, Any] | None = None
    startup_progress: StartupProgressSnapshot | None = None
    runtime_mode: str = "production"
    mock_runtime: MockRuntimeSnapshot | None = None
    error: str | None = None

    def to_status_payload(self) -> dict[str, Any]:
        if not self.devices:
            payload = dict(self.system_info)
            if self.error:
                payload["error"] = self.error
            payload["devices"] = []
        elif len(self.devices) == 1:
            payload = self.devices[0].to_dict()
        else:
            payload = self.devices[0].to_dict()
            payload["devices"] = [device.to_dict() for device in self.devices]

        payload["groups"] = [group.to_dict() for group in self.groups]
        payload["ma_connected"] = self.ma_connected
        payload["disabled_devices"] = list(self.disabled_devices)
        if self.startup_progress:
            payload["startup_progress"] = self.startup_progress.to_dict()
        payload["runtime_mode"] = self.runtime_mode
        if self.mock_runtime:
            payload["mock_runtime"] = self.mock_runtime.to_dict()
        if self.ma_web_url:
            payload["ma_web_url"] = self.ma_web_url
        if self.update_available:
            payload["update_available"] = self.update_available
        return payload


def _enrich_device_snapshot_with_ma(device: DeviceSnapshot, client) -> None:
    player_id = device.extra.get("player_id") or getattr(client, "player_id", "")
    if not player_id:
        return
    ma_group = get_ma_group_for_player_id(player_id)
    if ma_group and ma_group.get("name"):
        device.extra["group_name"] = ma_group["name"]
    if ma_group and ma_group.get("id"):
        device.ma_syncgroup_id = ma_group["id"]
    elif device.extra.get("group_id"):
        ma_group_by_id = state.get_ma_group_by_id(device.extra["group_id"])
        if ma_group_by_id and ma_group_by_id.get("id"):
            device.ma_syncgroup_id = ma_group_by_id["id"]
    if ma_group:
        device.ma_now_playing = get_ma_now_playing_for_group(ma_group["id"])
    else:
        group_id = str(device.extra.get("group_id") or "")
        device.ma_now_playing = get_ma_now_playing_for_group(group_id) or get_ma_now_playing_for_group(player_id) or {}


def _get_device_event_id(client, device: DeviceSnapshot) -> str:
    player_id = str(device.extra.get("player_id") or getattr(client, "player_id", "") or "").strip()
    if player_id:
        return player_id
    if device.bluetooth_mac:
        return device.bluetooth_mac
    return str(device.player_name or "")


def _build_device_capabilities(device: DeviceSnapshot) -> dict[str, Any]:
    return build_device_capabilities(device)


def _build_health_summary(device: DeviceSnapshot) -> DeviceHealthSummary:
    health = compute_device_health_state(device)
    return DeviceHealthSummary(
        state=health.state,
        severity=health.severity,
        summary=health.summary,
        reasons=list(health.reasons),
        last_event_at=health.last_event_at,
    )


def build_startup_progress_snapshot() -> StartupProgressSnapshot:
    """Build a typed startup progress snapshot from bridge state."""
    progress = state.get_startup_progress()
    return StartupProgressSnapshot(
        status=str(progress.get("status") or "idle"),
        phase=str(progress.get("phase") or "idle"),
        current_step=int(progress.get("current_step") or 0),
        total_steps=int(progress.get("total_steps") or 0),
        percent=int(progress.get("percent") or 0),
        message=str(progress.get("message") or ""),
        details=dict(progress.get("details") or {}),
        started_at=progress.get("started_at"),
        updated_at=progress.get("updated_at"),
        completed_at=progress.get("completed_at"),
    )


def build_mock_runtime_snapshot() -> MockRuntimeSnapshot:
    """Build a typed mock runtime snapshot from bridge state."""
    runtime_info = state.get_runtime_mode_info()
    return MockRuntimeSnapshot(
        mode=str(runtime_info.get("mode") or "production"),
        is_mocked=bool(runtime_info.get("is_mocked", False)),
        simulator_active=bool(runtime_info.get("simulator_active", False)),
        fixture_devices=int(runtime_info.get("fixture_devices") or 0),
        fixture_groups=int(runtime_info.get("fixture_groups") or 0),
        disclaimer=str(runtime_info.get("disclaimer") or ""),
        mocked_layers=[
            MockRuntimeLayerSnapshot(
                layer=str(layer.get("layer") or ""),
                summary=str(layer.get("summary") or ""),
                details=dict(layer.get("details") or {}),
            )
            for layer in runtime_info.get("mocked_layers", [])
            if isinstance(layer, dict)
        ],
        details=dict(runtime_info.get("details") or {}),
        updated_at=runtime_info.get("updated_at"),
    )


def _configured_enabled_by_player_name() -> dict[str, bool]:
    configured: dict[str, bool] = {}
    for dev in load_config().get("BLUETOOTH_DEVICES", []) or []:
        player_name = str(dev.get("player_name") or "").strip()
        if not player_name:
            continue
        configured[player_name] = bool(dev.get("enabled", True))
    return configured


def _resolve_global_enabled(player_name: str | None, configured_enabled: dict[str, bool] | None) -> bool:
    if not player_name:
        return True
    for configured_name, enabled in (configured_enabled or {}).items():
        if _match_player_name(configured_name, player_name):
            return enabled
    return True


def _resolve_room_context(client, *, config: dict[str, Any]) -> dict[str, str]:
    bt_mgr = getattr(client, "bt_manager", None)
    return resolve_device_room_context(
        config,
        player_name=str(getattr(client, "player_name", None) or "").strip(),
        device_mac=str(getattr(bt_mgr, "mac_address", None) or "").strip(),
        adapter_mac=str(
            getattr(bt_mgr, "effective_adapter_mac", None) or getattr(bt_mgr, "adapter", None) or ""
        ).strip(),
    )


def _build_transfer_readiness(
    *,
    device: DeviceSnapshot,
    status: dict[str, Any],
    recent_events: list[dict[str, Any]],
) -> dict[str, Any]:
    reason = "ready"
    severity = "info"
    if not device.enabled:
        reason = "disabled"
        severity = "warning"
    elif not device.bt_management_enabled:
        reason = "bt_management_disabled"
        severity = "warning"
    elif bool(status.get("stopping")):
        reason = "stopping"
        severity = "warning"
    elif bool(status.get("reconnecting")):
        reason = "reconnecting"
        severity = "warning"
    elif bool(status.get("reanchoring")):
        reason = "reanchoring"
        severity = "warning"
    elif not device.connected:
        reason = "daemon_unavailable"
        severity = "error"
    elif not device.bluetooth_connected:
        reason = "bluetooth_disconnected"
        severity = "error"
    elif not device.server_connected:
        reason = "music_assistant_disconnected"
        severity = "error"
    elif not device.has_sink:
        reason = "sink_missing"
        severity = "error"

    recent_types = [str(event.get("event_type") or "") for event in recent_events if isinstance(event, dict)]
    return {
        "ready": reason == "ready",
        "reason": reason,
        "severity": severity,
        "bluetooth_ready": bool(device.bluetooth_connected),
        "daemon_ready": bool(device.connected),
        "sink_ready": bool(device.has_sink),
        "music_assistant_ready": bool(device.server_connected),
        "latency_profile": str(device.handoff_mode or HANDOFF_MODES[0]),
        "recent_recovery_activity": {
            "active": bool(status.get("reconnecting")) or bool(status.get("reanchoring")) or bool(recent_types),
            "event_types": recent_types[:5],
        },
    }


def build_device_snapshot_pairs(
    clients: list[Any],
    *,
    configured_enabled: dict[str, bool] | None = None,
) -> list[tuple[Any, DeviceSnapshot]]:
    """Build `(client, snapshot)` pairs for routes that need reads plus runtime objects."""
    resolved_enabled = configured_enabled if configured_enabled is not None else _configured_enabled_by_player_name()
    return [(client, build_device_snapshot(client, configured_enabled=resolved_enabled)) for client in clients]


def build_device_snapshot(client, *, configured_enabled: dict[str, bool] | None = None) -> DeviceSnapshot:
    """Build a typed device snapshot from a runtime client object."""
    if client is None:
        return DeviceSnapshot(error="Client not running")

    if not hasattr(client, "status"):
        return DeviceSnapshot(error="Client initializing")

    if hasattr(client, "_status_lock"):
        with client._status_lock:
            status = client.status.copy()
    else:
        status = client.status.copy()
    resolved_enabled = configured_enabled if configured_enabled is not None else _configured_enabled_by_player_name()
    current_config = load_config()

    uptime = None
    if "uptime_start" in status:
        uptime_value = datetime.now(tz=UTC) - status["uptime_start"]
        uptime = str(timedelta(seconds=int(uptime_value.total_seconds())))
        del status["uptime_start"]

    bt_mgr = getattr(client, "bt_manager", None)
    adapter_name = None
    if bt_mgr:
        lookup_mac = getattr(bt_mgr, "effective_adapter_mac", None) or getattr(bt_mgr, "adapter", None)
        if lookup_mac:
            adapter_name = get_adapter_name(lookup_mac.upper())

    connected_server_url = getattr(client, "connected_server_url", "") or (
        f"ws://{client.server_host}:{client.server_port}/sendspin"
        if getattr(client, "server_host", None) and client.server_host.lower() not in ("auto", "discover", "")
        else ""
    )
    if hasattr(client, "is_running"):
        connected = bool(client.is_running())
    else:
        connected = bool(
            status.get("connected", False)
            or status.get("server_connected", False)
            or status.get("bluetooth_connected", False)
        )

    room_context = _resolve_room_context(client, config=current_config)
    device = DeviceSnapshot(
        connected=connected,
        server_connected=bool(status.get("server_connected", False)),
        bluetooth_connected=bool(status.get("bluetooth_connected", False)),
        bluetooth_available=bool(status.get("bluetooth_available", False)),
        playing=bool(status.get("playing", False)),
        version=get_runtime_version(),
        build_date=BUILD_DATE,
        bluetooth_mac=bt_mgr.mac_address if bt_mgr else None,
        player_name=getattr(client, "player_name", None),
        listen_port=getattr(client, "listen_port", None),
        server_host=getattr(client, "server_host", None),
        server_port=getattr(client, "server_port", None),
        static_delay_ms=getattr(client, "static_delay_ms", None),
        connected_server_url=connected_server_url,
        bluetooth_adapter=(getattr(bt_mgr, "effective_adapter_mac", None) or getattr(bt_mgr, "adapter", None))
        if bt_mgr
        else None,
        bluetooth_adapter_name=adapter_name,
        bluetooth_adapter_hci=getattr(bt_mgr, "adapter_hci_name", "") if bt_mgr else "",
        has_sink=bool(getattr(client, "bluetooth_sink_name", None)),
        sink_name=getattr(client, "bluetooth_sink_name", None),
        enabled=_resolve_global_enabled(getattr(client, "player_name", None), resolved_enabled),
        bt_management_enabled=bool(getattr(client, "bt_management_enabled", True)),
        battery_level=getattr(bt_mgr, "battery_level", None) if bt_mgr else None,
        room_id=room_context["room_id"] or None,
        room_name=room_context["room_name"] or None,
        room_source=room_context["room_source"] or None,
        room_confidence=room_context["room_confidence"] or None,
        handoff_mode=room_context["handoff_mode"] or HANDOFF_MODES[0],
        runtime=state._detect_runtime_type(),
        uptime=uptime,
        extra=dict(status),
    )
    device.extra["version"] = get_runtime_version()
    device.extra["build_date"] = BUILD_DATE
    device.extra["runtime"] = device.runtime
    device.extra["connected"] = device.connected
    device.extra["player_name"] = device.player_name
    device.extra["listen_port"] = device.listen_port
    device.extra["server_host"] = device.server_host
    device.extra["server_port"] = device.server_port
    device.extra["static_delay_ms"] = device.static_delay_ms
    device.extra["connected_server_url"] = device.connected_server_url
    device.extra["bluetooth_mac"] = device.bluetooth_mac
    device.extra["bluetooth_adapter"] = device.bluetooth_adapter
    device.extra["bluetooth_adapter_name"] = device.bluetooth_adapter_name
    device.extra["bluetooth_adapter_hci"] = device.bluetooth_adapter_hci
    device.extra["has_sink"] = device.has_sink
    device.extra["sink_name"] = device.sink_name
    device.extra["enabled"] = device.enabled
    device.extra["bt_management_enabled"] = device.bt_management_enabled
    device.extra["battery_level"] = device.battery_level
    if device.room_id:
        device.extra["room_id"] = device.room_id
    if device.room_name:
        device.extra["room_name"] = device.room_name
    if device.room_source:
        device.extra["room_source"] = device.room_source
    if device.room_confidence:
        device.extra["room_confidence"] = device.room_confidence
    device.extra["handoff_mode"] = device.handoff_mode
    device.extra["bluetooth_paired"] = getattr(bt_mgr, "paired", None) if bt_mgr else None
    if bt_mgr:
        device.extra["max_reconnect_fails"] = int(getattr(bt_mgr, "max_reconnect_fails", 0) or 0)
        threshold = int(getattr(bt_mgr, "max_reconnect_fails", 0) or 0)
        reconnect_attempt = int(status.get("reconnect_attempt") or 0)
        if threshold > 0:
            device.extra["reconnect_attempts_remaining"] = max(threshold - reconnect_attempt, 0)
    if uptime is not None:
        device.extra["uptime"] = uptime
    _enrich_device_snapshot_with_ma(device, client)
    device.recent_events = state.get_device_events(_get_device_event_id(client, device), limit=5)
    device.transfer_readiness = _build_transfer_readiness(
        device=device, status=status, recent_events=device.recent_events
    )
    device.health_summary = _build_health_summary(device).to_dict()
    if device.ma_syncgroup_id:
        device.extra["ma_syncgroup_id"] = device.ma_syncgroup_id
    if device.ma_now_playing is not None:
        device.extra["ma_now_playing"] = device.ma_now_playing
    if device.recent_events:
        device.extra["recent_events"] = device.recent_events
    if device.health_summary is not None:
        device.extra["health_summary"] = device.health_summary
    if device.transfer_readiness is not None:
        device.extra["transfer_readiness"] = device.transfer_readiness
    device.capabilities = _build_device_capabilities(device)
    device.state_model = build_normalized_device_state(device).to_dict()
    device.extra["state_model"] = device.state_model
    return device


def build_group_snapshots(
    clients: list[Any],
    *,
    snapshot_pairs: list[tuple[Any, DeviceSnapshot]] | None = None,
) -> list[GroupSnapshot]:
    """Build normalized group snapshots from the current client list."""
    groups: dict[str | None, dict[str, Any]] = {}
    solo_counter = 0

    for client, device in snapshot_pairs or build_device_snapshot_pairs(clients):
        status = device.extra
        group_id = status.get("group_id")
        key = group_id if group_id is not None else f"__solo_{solo_counter}"
        if group_id is None:
            solo_counter += 1

        member = GroupMemberSnapshot(
            player_name=device.player_name,
            player_id=str(status.get("player_id") or getattr(client, "player_id", "")),
            volume=int(status.get("volume", 100)),
            playing=bool(device.playing),
            connected=bool(device.connected),
            server_connected=bool(device.server_connected),
            bluetooth_connected=bool(device.bluetooth_connected),
        )

        if key not in groups:
            groups[key] = {
                "group_id": group_id,
                "group_name": status.get("group_name"),
                "members": [],
            }
        groups[key]["members"].append(member)

    ma_groups = state.get_ma_groups()
    entry_syncgroup: dict[int, str] = {}
    if ma_groups:
        syncgroup_map: dict[str, dict[str, Any]] = {}
        merged: list[dict[str, Any]] = []
        for entry in groups.values():
            ma_syncgroup_id = None
            if entry["group_id"]:
                for member in entry["members"]:
                    if not member.player_id:
                        continue
                    ma_info = get_ma_group_for_player_id(member.player_id)
                    if ma_info:
                        ma_syncgroup_id = ma_info["id"]
                        if not entry["group_name"] and ma_info.get("name"):
                            entry["group_name"] = ma_info["name"]
                        break
                if not ma_syncgroup_id:
                    ma_group = state.get_ma_group_by_id(entry["group_id"])
                    if ma_group:
                        ma_syncgroup_id = ma_group["id"]
                        if not entry["group_name"] and ma_group.get("name"):
                            entry["group_name"] = ma_group["name"]
            if ma_syncgroup_id and ma_syncgroup_id in syncgroup_map:
                target = syncgroup_map[ma_syncgroup_id]
                target["members"].extend(entry["members"])
                if not target["group_name"] and entry.get("group_name"):
                    target["group_name"] = entry["group_name"]
            else:
                if ma_syncgroup_id:
                    syncgroup_map[ma_syncgroup_id] = entry
                    entry_syncgroup[len(merged)] = ma_syncgroup_id
                merged.append(entry)
    else:
        merged = list(groups.values())

    result: list[GroupSnapshot] = []
    for idx, entry in enumerate(merged):
        members = entry["members"]
        volumes = [member.volume for member in members]
        external_members: list[dict[str, Any]] = []
        external_count = 0

        ma_syncgroup_id = entry_syncgroup.get(idx)
        if ma_syncgroup_id and ma_groups:
            ma_group = next((group for group in ma_groups if group["id"] == ma_syncgroup_id), None)
            if ma_group:
                local_ids = {member.player_id for member in members if member.player_id}
                local_names = {(member.player_name or "").lower() for member in members if member.player_name}
                external_members = [
                    {"name": member["name"], "available": member.get("available", True)}
                    for member in ma_group.get("members", [])
                    if member.get("id", "") not in local_ids and (member.get("name") or "").lower() not in local_names
                ]
                external_count = len(external_members)

        result.append(
            GroupSnapshot(
                group_id=entry["group_id"],
                group_name=entry["group_name"],
                members=members,
                avg_volume=round(sum(volumes) / len(volumes)) if volumes else 100,
                playing=any(member.playing for member in members),
                external_members=external_members,
                external_count=external_count,
            )
        )

    return result


def build_bridge_snapshot(clients: list[Any]) -> BridgeSnapshot:
    """Build the normalized bridge snapshot for `/api/status`-style responses."""
    ma_url, _token = state.get_ma_api_credentials()
    update_available = state.get_update_available()
    mock_runtime = build_mock_runtime_snapshot()
    configured_enabled = _configured_enabled_by_player_name()
    if not clients:
        return BridgeSnapshot(
            devices=[],
            groups=[],
            ma_connected=state.is_ma_connected(),
            ma_web_url=ma_url or None,
            disabled_devices=state.get_disabled_devices(),
            system_info=state.get_bridge_system_info(),
            update_available=update_available,
            startup_progress=build_startup_progress_snapshot(),
            runtime_mode=mock_runtime.mode,
            mock_runtime=mock_runtime,
            error="No clients",
        )

    snapshot_pairs = build_device_snapshot_pairs(clients, configured_enabled=configured_enabled)
    devices = [device for _client, device in snapshot_pairs]
    return BridgeSnapshot(
        devices=devices,
        groups=build_group_snapshots(clients, snapshot_pairs=snapshot_pairs),
        ma_connected=state.is_ma_connected(),
        ma_web_url=ma_url or None,
        disabled_devices=state.get_disabled_devices(),
        update_available=update_available,
        startup_progress=build_startup_progress_snapshot(),
        runtime_mode=mock_runtime.mode,
        mock_runtime=mock_runtime,
    )
