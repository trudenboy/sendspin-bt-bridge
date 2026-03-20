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
from config import BUILD_DATE, VERSION
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
    version: str = VERSION
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
    bt_management_enabled: bool = True
    battery_level: int | None = None
    runtime: str | None = None
    uptime: str | None = None
    ma_syncgroup_id: str | None = None
    ma_now_playing: dict[str, Any] | None = None
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    health_summary: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
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


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def _derive_event_reasons(events: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if not events:
        return reasons
    event_types = [str(event.get("event_type") or "") for event in events]
    if "runtime-error" in event_types:
        _append_reason(reasons, "recent_runtime_error")
    if "bluetooth-reconnect-failed" in event_types:
        _append_reason(reasons, "recent_reconnect_failure")
    if event_types.count("bluetooth-reconnect-failed") >= 2:
        _append_reason(reasons, "repeated_reconnect_failures")
    if "bluetooth-reconnected" in event_types or "reconnecting" in event_types:
        _append_reason(reasons, "recent_reconnect_activity")
    if "audio-stream-stalled" in event_types:
        _append_reason(reasons, "recent_audio_stall")
    if "reanchoring" in event_types:
        _append_reason(reasons, "recent_reanchor")
    if "bt-management-disabled" in event_types:
        _append_reason(reasons, "management_auto_disabled")
    if "ma-monitor-stale" in event_types:
        _append_reason(reasons, "ma_monitor_stale")
    return reasons


def _capability_payload(
    *,
    supported: bool,
    currently_available: bool,
    blocked_reason: str | None = None,
    safe_actions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "supported": supported,
        "currently_available": currently_available,
        "blocked_reason": blocked_reason,
        "safe_actions": list(safe_actions or []),
    }


def _capability_domain_payload(*capabilities: dict[str, Any]) -> dict[str, Any]:
    supported = any(bool(item.get("supported")) for item in capabilities)
    currently_available = any(bool(item.get("currently_available")) for item in capabilities)
    blocked_reason = None
    if supported and not currently_available:
        blocked_reason = next((item.get("blocked_reason") for item in capabilities if item.get("blocked_reason")), None)
    safe_actions: list[str] = []
    for item in capabilities:
        for action in item.get("safe_actions") or []:
            if action not in safe_actions:
                safe_actions.append(action)
    return _capability_payload(
        supported=supported,
        currently_available=currently_available,
        blocked_reason=blocked_reason,
        safe_actions=safe_actions,
    )


def _build_device_capabilities(device: DeviceSnapshot) -> dict[str, Any]:
    ma_connected = bool((device.ma_now_playing or {}).get("connected"))
    reconnecting = bool(device.extra.get("reconnecting"))
    stopping = bool(device.extra.get("stopping"))
    released = device.bt_management_enabled is False
    has_sink = bool(device.has_sink)

    if released:
        reconnect_blocked_reason = "Bluetooth management is released; reclaim it before reconnecting."
    elif reconnecting:
        reconnect_blocked_reason = "Reconnect is already in progress."
    elif stopping:
        reconnect_blocked_reason = "Device is stopping."
    else:
        reconnect_blocked_reason = None
    reconnect = _capability_payload(
        supported=bool(device.bluetooth_mac),
        currently_available=reconnect_blocked_reason is None and bool(device.bluetooth_mac),
        blocked_reason=reconnect_blocked_reason,
        safe_actions=["toggle_bt_management", "open_diagnostics"] if reconnect_blocked_reason else ["reconnect"],
    )

    if reconnecting:
        toggle_management_blocked_reason = "Wait for the current reconnect attempt to finish first."
    elif stopping:
        toggle_management_blocked_reason = "Device is stopping."
    else:
        toggle_management_blocked_reason = None
    toggle_bt_management = _capability_payload(
        supported=True,
        currently_available=toggle_management_blocked_reason is None,
        blocked_reason=toggle_management_blocked_reason,
        safe_actions=["open_diagnostics"] if toggle_management_blocked_reason else ["toggle_bt_management"],
    )

    play_pause = _capability_payload(
        supported=True,
        currently_available=bool(device.server_connected),
        blocked_reason=None if device.server_connected else "Sendspin is not connected.",
        safe_actions=["reconnect", "open_diagnostics"] if not device.server_connected else ["play_pause"],
    )

    volume = _capability_payload(
        supported=True,
        currently_available=has_sink,
        blocked_reason=(
            None if has_sink else "Bluetooth management is released." if released else "Audio sink is not configured."
        ),
        safe_actions=["reconnect", "open_diagnostics"] if not has_sink else ["volume"],
    )
    mute = _capability_payload(
        supported=True,
        currently_available=has_sink,
        blocked_reason=volume["blocked_reason"],
        safe_actions=["reconnect", "open_diagnostics"] if not has_sink else ["mute"],
    )

    queue_control = _capability_payload(
        supported=bool(device.server_connected),
        currently_available=bool(device.server_connected and ma_connected),
        blocked_reason=(
            "Sendspin is not connected."
            if not device.server_connected
            else "Music Assistant API is not connected."
            if not ma_connected
            else None
        ),
        safe_actions=["open_ma_settings", "open_diagnostics"] if not ma_connected else ["queue_control"],
    )

    diagnostics = _capability_payload(
        supported=True,
        currently_available=True,
        blocked_reason=None,
        safe_actions=["open_diagnostics", "download_diagnostics"],
    )
    disable_device = _capability_payload(
        supported=True,
        currently_available=not stopping,
        blocked_reason=None if not stopping else "Device is stopping.",
        safe_actions=["disable_device"] if not stopping else ["open_diagnostics"],
    )

    actions = {
        "reconnect": reconnect,
        "toggle_bt_management": toggle_bt_management,
        "play_pause": play_pause,
        "volume": volume,
        "mute": mute,
        "queue_control": queue_control,
        "diagnostics": diagnostics,
        "disable_device": disable_device,
    }
    domains = {
        "connectivity": _capability_domain_payload(reconnect, toggle_bt_management),
        "playback": _capability_domain_payload(play_pause, volume, mute),
        "music_assistant": _capability_domain_payload(queue_control),
        "recovery": _capability_domain_payload(reconnect, toggle_bt_management, diagnostics),
        "diagnostics": _capability_domain_payload(diagnostics),
    }
    return {
        "health_state": str((device.health_summary or {}).get("state") or "unknown"),
        "domains": domains,
        "actions": actions,
    }


def _build_health_summary(device: DeviceSnapshot) -> DeviceHealthSummary:
    reasons: list[str] = []
    event_reasons = _derive_event_reasons(device.recent_events)

    if not device.bt_management_enabled:
        return DeviceHealthSummary(
            state="disabled",
            severity="info",
            summary="Bluetooth management disabled",
            reasons=["bt_management_disabled", *event_reasons],
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    if device.extra.get("last_error"):
        _append_reason(reasons, "last_error")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthSummary(
            state="degraded",
            severity="error",
            summary=str(device.extra["last_error"]),
            reasons=reasons,
            last_event_at=device.extra.get("last_error_at")
            or (device.recent_events[0]["at"] if device.recent_events else None),
        )

    if device.extra.get("stopping"):
        _append_reason(reasons, "stopping")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthSummary(
            state="transitioning",
            severity="info",
            summary="Stopping playback service",
            reasons=reasons,
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    if device.extra.get("reconnecting"):
        _append_reason(reasons, "reconnecting")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthSummary(
            state="recovering",
            severity="warning",
            summary="Reconnect in progress",
            reasons=reasons,
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    if device.extra.get("reanchoring"):
        _append_reason(reasons, "reanchoring")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthSummary(
            state="recovering",
            severity="warning",
            summary="Audio sync re-anchor in progress",
            reasons=reasons,
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    if not device.bluetooth_connected:
        _append_reason(reasons, "bluetooth_disconnected")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthSummary(
            state="offline",
            severity="warning",
            summary="Bluetooth speaker disconnected",
            reasons=reasons,
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    if not device.server_connected:
        _append_reason(reasons, "daemon_disconnected")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthSummary(
            state="degraded",
            severity="warning",
            summary="Sendspin daemon disconnected",
            reasons=reasons,
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    if device.playing and not device.extra.get("audio_streaming", False):
        _append_reason(reasons, "playback_without_audio")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthSummary(
            state="degraded",
            severity="warning",
            summary="Playback active without audio stream",
            reasons=reasons,
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    if device.playing:
        return DeviceHealthSummary(
            state="streaming",
            severity="info",
            summary="Streaming audio",
            reasons=event_reasons,
            last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
        )

    return DeviceHealthSummary(
        state="ready" if device.connected else "idle",
        severity="info",
        summary="Connected and ready" if device.connected else "Idle",
        reasons=event_reasons,
        last_event_at=device.recent_events[0]["at"] if device.recent_events else None,
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


def build_device_snapshot_pairs(clients: list[Any]) -> list[tuple[Any, DeviceSnapshot]]:
    """Build `(client, snapshot)` pairs for routes that need reads plus runtime objects."""
    return [(client, build_device_snapshot(client)) for client in clients]


def build_device_snapshot(client) -> DeviceSnapshot:
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

    device = DeviceSnapshot(
        connected=connected,
        server_connected=bool(status.get("server_connected", False)),
        bluetooth_connected=bool(status.get("bluetooth_connected", False)),
        bluetooth_available=bool(status.get("bluetooth_available", False)),
        playing=bool(status.get("playing", False)),
        version=VERSION,
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
        bt_management_enabled=bool(getattr(client, "bt_management_enabled", True)),
        battery_level=getattr(bt_mgr, "battery_level", None) if bt_mgr else None,
        runtime=state._detect_runtime_type(),
        uptime=uptime,
        extra=dict(status),
    )
    device.extra["version"] = VERSION
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
    device.extra["bt_management_enabled"] = device.bt_management_enabled
    device.extra["battery_level"] = device.battery_level
    if uptime is not None:
        device.extra["uptime"] = uptime
    _enrich_device_snapshot_with_ma(device, client)
    device.recent_events = state.get_device_events(_get_device_event_id(client, device), limit=5)
    device.health_summary = _build_health_summary(device).to_dict()
    if device.ma_syncgroup_id:
        device.extra["ma_syncgroup_id"] = device.ma_syncgroup_id
    if device.ma_now_playing is not None:
        device.extra["ma_now_playing"] = device.ma_now_playing
    if device.recent_events:
        device.extra["recent_events"] = device.recent_events
    if device.health_summary is not None:
        device.extra["health_summary"] = device.health_summary
    device.capabilities = _build_device_capabilities(device)
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

    snapshot_pairs = build_device_snapshot_pairs(clients)
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
