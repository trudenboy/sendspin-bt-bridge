"""The bridge itself and the groups its speakers belong to."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from sendspin_bridge.application.models.base import ResponseModel
from sendspin_bridge.application.models.devices import Device, DisabledDevice


class GroupMember(ResponseModel):
    player_id: str
    player_name: str | None = None
    volume: int = 0
    playing: bool = False
    connected: bool = False
    server_connected: bool = False
    bluetooth_connected: bool = False


class Group(ResponseModel):
    """A Music Assistant sync group, or a solo speaker as a group of one (``id`` is null)."""

    id: str | None = None
    name: str | None = None
    members: list[GroupMember] = Field(default_factory=list)
    avg_volume: int = 0
    playing: bool = False
    external_members: list[dict[str, Any]] = Field(
        default_factory=list, description="Members on other bridges or other player providers."
    )
    external_count: int = 0

    @classmethod
    def from_snapshot(cls, d: dict[str, Any]) -> Group:
        return cls(
            id=d.get("group_id"),
            name=d.get("group_name"),
            members=[GroupMember(**m) for m in d.get("members") or []],
            avg_volume=int(d.get("avg_volume") or 0),
            playing=bool(d.get("playing")),
            external_members=list(d.get("external_members") or []),
            external_count=int(d.get("external_count") or 0),
        )


class StartupProgress(ResponseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    status: str
    phase: str
    current_step: int = 0
    total_steps: int = 0
    percent: int = 0
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class UpdateInfo(ResponseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True)

    version: str
    tag: str | None = None
    channel: str | None = None
    current_version: str | None = None
    prerelease: bool = False
    published_at: str | None = None
    url: str | None = None
    body: str | None = None


class Bridge(ResponseModel):
    name: str = Field(default="", description="The bridge's name (BRIDGE_NAME, or the host name).")
    version: str
    build_date: str
    hostname: str = ""
    ip_address: str = ""
    uptime: str = ""
    runtime: str = Field(default="unknown", description="ha_addon, docker, systemd or unknown")
    runtime_mode: str = Field(default="production", description="production, demo or a mocked runtime")
    config_schema_version: int | None = None
    ipc_protocol_version: int | None = None
    auth_enabled: bool = False
    ma_connected: bool = False
    ma_web_url: str | None = None
    device_count: int = 0
    disabled_devices: list[DisabledDevice] = Field(default_factory=list)
    startup: StartupProgress | None = None
    update_available: UpdateInfo | None = None
    mock_runtime: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = Field(
        default=None, description="Sampled host checks (BT, audio, D-Bus, memory)."
    )
    state_model: dict[str, Any] | None = None
    guidance: dict[str, Any] | None = Field(default=None, description="Unified operator guidance (banners, header).")
    onboarding: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None


class BridgeStatus(ResponseModel):
    """Everything the dashboard renders, in one document (also the ``status`` event)."""

    bridge: Bridge
    devices: list[Device]
    groups: list[Group]
