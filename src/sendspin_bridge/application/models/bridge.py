"""The bridge itself and the groups its speakers belong to."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

from sendspin_bridge.application.models.base import ResponseModel
from sendspin_bridge.application.models.devices import Device, DisabledDevice, LinkStatus
from sendspin_bridge.application.models.source import SourceStateName


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


class BridgeVerdict(ResponseModel):
    """The bridge's one-word health, from the same issue list as the guidance banner."""

    level: str = Field(description="ok, warning or error: the worst severity in the issue list.")
    count: int = Field(description="Issues at warning or error.")


class SourceDeviceStatus(ResponseModel):
    """A paired Bluetooth device that can stream into the bridge."""

    mac: str
    name: str = ""
    connected: bool = Field(default=False, description="BlueZ reports an active connection.")
    active: bool = Field(default=False, description="This device is the one currently being captured.")


class SendspinSourceStatus(ResponseModel):
    """The phone-to-Music-Assistant source: capture, pairing and connection state."""

    enabled: bool = Field(default=False, description="The source client is enabled by the operator.")
    state: SourceStateName = Field(
        default="off",
        description="off | pairing | waiting | streaming | error.",
    )
    phone: str | None = Field(default=None, description="The capture node of the phone that is playing, if any.")
    phone_mac: str | None = Field(default=None, description="That phone's address, when the node names it.")
    paired_phone_mac: str | None = Field(
        default=None, description="A phone paired during the window, before it streams."
    )
    streaming: bool = Field(default=False, description="The phone's audio is reaching Music Assistant.")
    server: str | None = Field(default=None, description="The Sendspin server URL the source dials.")
    ma_connected: bool = Field(default=False, description="The source client is admitted by Music Assistant.")
    ma_paired: bool = Field(default=False, description="A long-term pairing PSK is stored.")
    ma_pairing_required: bool = Field(default=False, description="A code must be entered in Music Assistant.")
    ma_pairing_code: str | None = Field(default=None, description="The code, while the pairing window is open.")
    ma_pairing_seconds_left: int = Field(default=0, description="Seconds the MA pairing window stays open.")
    ma_error: str | None = Field(default=None, description="Stable code: ma_unreachable, ma_pairing_rejected, …")
    pairing_seconds_left: int = Field(default=0, description="Seconds the phone-pairing window stays open.")
    pairing_adapters: list[str] = Field(default_factory=list, description="Adapters the pairing window opened.")
    devices: list[SourceDeviceStatus] = Field(
        default_factory=list, description="Paired Bluetooth devices that can feed the source."
    )
    target_player: str | None = Field(
        default=None, description="The MA player/group chosen to play the source automatically; null = choose in MA."
    )
    target_error: str | None = Field(default=None, description="The last automatic target-play failure, if any.")
    error: str | None = Field(default=None, description="A terminal source error (capture_unavailable, …).")


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
    music_assistant_account: LinkStatus = Field(
        description="The bridge's sign-in to the Music Assistant API: signed_in, signed_out or not_configured."
    )
    verdict: BridgeVerdict
    ma_web_url: str | None = None
    device_count: int = 0
    disabled_devices: list[DisabledDevice] = Field(default_factory=list)
    startup: StartupProgress | None = None
    update_available: UpdateInfo | None = None
    mock_runtime: dict[str, Any] | None = None
    sendspin_source: SendspinSourceStatus | None = Field(
        default=None, description="A phone streaming into the bridge as a Sendspin source (#563)."
    )
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
