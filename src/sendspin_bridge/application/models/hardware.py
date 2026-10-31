"""The physical layer as API v1 shows it: controllers, Bluetooth devices, capabilities."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sendspin_bridge.application.models.base import ResponseModel


class RoleEvidence(ResponseModel):
    """One link role and the fact that proves it."""

    role: Literal["output", "input"]
    evidence: str = Field(
        description="transport:active, sink:<name>, capture:<node>, source:<name> or source_feed:<mac>."
    )


class RssiSummary(ResponseModel):
    """Live signal strength, with the time it was observed."""

    delta_db: int
    at: str | None = Field(default=None, description="ISO-8601 UTC observation time.")


class TransportSummary(ResponseModel):
    """What the A2DP transport is doing, when BlueZ can say."""

    codec: str | None = None
    codec_id: int | None = None
    state: str | None = Field(default=None, description="active | pending | idle")
    delay_ms: float | None = None


class BtDeviceSummary(ResponseModel):
    """A paired or known Bluetooth device."""

    mac: str
    name: str
    alias: str
    paired: bool | None = Field(default=None, description="null means BlueZ did not answer.")
    bonded: bool | None = None
    trusted: bool | None = None
    connected: bool = False
    controller_macs: list[str] = Field(default_factory=list, description="Every controller holding the bond.")
    battery_pct: int | None = None
    rssi: RssiSummary | None = None
    transport: TransportSummary | None = None
    roles: list[RoleEvidence] = Field(default_factory=list)
    player_id: str | None = Field(default=None, description="Config id of the player this bond backs.")
    source_phone: bool = Field(default=False, description="The phone currently streaming into the bridge.")


class BtDeviceDetail(BtDeviceSummary):
    """One device with what only a single-device read adds."""

    uuids: list[str] = Field(default_factory=list)
    services_resolved: bool | None = None
    modalias: str | None = None
    manufacturer: str = ""
    bonds: list[str] = Field(default_factory=list, description="Every controller MAC holding the bond.")


class AreaRef(ResponseModel):
    """The Home Assistant area an adapter was mapped to (read-only here)."""

    id: str
    name: str | None = None


class ControllerSummary(ResponseModel):
    """One physical Bluetooth adapter."""

    id: str = Field(description="Kernel name, e.g. hci0; synthetic when sysfs is absent.")
    id_synthetic: bool = False
    mac: str
    name: str
    alias: str
    powered: bool = False
    present: bool = True
    class_of_device: str | None = Field(default=None, description="Raw HCI Class of Device; null without CAP_NET_RAW.")
    area: AreaRef | None = None
    device_count: int = 0


class AddressForms(ResponseModel):
    """The same address in every spelling its consumers use."""

    colons: str = Field(description="AA:BB:CC:DD:EE:FF — BlueZ, bluetoothctl, the config.")
    underscores: str = Field(description="AA_BB_CC_DD_EE_FF — audio sink names and D-Bus paths.")
    bare: str = Field(description="AABBCCDDEEFF — the sysfs adapter map.")


class ControllerDetail(ControllerSummary):
    """One adapter with its bonds and address forms."""

    devices: list[BtDeviceSummary] = Field(default_factory=list)
    address_forms: AddressForms


class CapabilitySet(ResponseModel):
    """What this host can show, plus the reason code for every no."""

    bluetooth: bool
    dbus: bool
    audio_server: Literal["pipewire", "pulseaudio", "none"]
    capture: bool
    default_sink: bool
    reasons: dict[str, str] = Field(default_factory=dict, description="Stable reason code per unavailable capability.")


class DegradedSection(ResponseModel):
    """A section read in part, or not at all, with a code the UI branches on."""

    section: str = Field(description="controllers | bluetooth_devices | audio | hardware")
    code: str
    detail: str


class HardwareSnapshot(ResponseModel):
    """The aggregate read: one moment of the physical layer."""

    as_of: str = Field(description="ISO-8601 UTC when this read was taken.")
    stale_after_s: int = Field(description="Seconds after which the cached read is considered old.")
    stale: bool = Field(description="True when a failed refresh forced the last read to be served.")
    capabilities: CapabilitySet
    controllers: list[ControllerSummary] = Field(default_factory=list)
    bluetooth_devices: list[BtDeviceSummary] = Field(default_factory=list)
    degraded: list[DegradedSection] = Field(default_factory=list)
