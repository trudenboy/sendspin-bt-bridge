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


class EndpointOwner(ResponseModel):
    """What an audio endpoint belongs to: a card, a Bluetooth link, or the bridge.

    Exactly one of *card*, *bluetooth* and *internal* is set; a Bluetooth
    speaker's sink belongs to the speaker, not to the ``bluez_card`` object.
    """

    card: str | None = Field(default=None, description="The card that owns this endpoint.")
    bluetooth: str | None = Field(default=None, description="The speaker MAC, when the endpoint is a BT link.")
    internal: bool | None = Field(
        default=None, description="true for the bridge's own sendspin_* sinks; null otherwise."
    )


class EndpointRef(ResponseModel):
    """One endpoint belonging to a card, without its detail fields."""

    kind: Literal["sink", "source"]
    name: str


class CardProfile(ResponseModel):
    """One profile a card can take; ``available=null`` means the server said "unknown"."""

    name: str
    description: str = ""
    available: bool | None = None


class CardPort(ResponseModel):
    """One port a card exposes; ``available=null`` means the server said "unknown"."""

    name: str
    description: str = ""
    available: bool | None = None
    active: bool = False
    profiles: list[str] = Field(
        default_factory=list, description="Profiles this port can be part of; empty when the server omits them."
    )


class EndpointSummary(ResponseModel):
    """A sink (output) or source (input) as the audio server names it."""

    kind: Literal["sink", "source"]
    name: str
    description: str = ""
    owner: EndpointOwner | None = Field(default=None, description="null when no card or link claims it.")
    state: str = Field(default="unknown", description="running | idle | suspended | unknown")
    sample_spec: str = ""
    muted: bool | None = Field(default=None, description="Sinks only; null for sources.")
    volume: int | None = Field(default=None, description="Sinks only, 0-100; null for sources.")
    in_use_by: str | None = Field(default=None, description="Player id using this sink, from the config.")
    active_port: str | None = None
    active_port_available: str | None = Field(
        default=None, description="available | not available | availability unknown"
    )
    is_default: bool = Field(
        default=False,
        description="True only for the audio server's current default sink; always false for sources.",
    )


class EndpointDetail(EndpointSummary):
    """One endpoint with its stable identifiers and the owning card's ports."""

    identifiers: dict[str, str] = Field(default_factory=dict)
    ports: list[CardPort] = Field(default_factory=list, description="All ports of the owning card.")
    monitor_of: str | None = Field(default=None, description="The sink a source monitors, if any.")
    as_of: str = Field(description="ISO-8601 UTC when the read behind this detail was taken.")
    stale: bool = Field(
        description="True when a failed refresh made this detail show an earlier read than the request."
    )


class CardSummary(ResponseModel):
    """An audio card: what it is, and a count of what it can do."""

    name: str
    driver: str = ""
    active_profile: str | None = None
    profile_count: int = 0
    port_count: int = 0
    endpoint_refs: list[EndpointRef] = Field(default_factory=list)
    managed_by: Literal["bridge"] | None = Field(
        default=None,
        description="bridge when the bridge itself chooses this card's profile (a Bluetooth card); null otherwise.",
    )


class CardDetail(CardSummary):
    """One card with every profile, every port and its endpoints."""

    profiles: list[CardProfile] = Field(default_factory=list)
    ports: list[CardPort] = Field(default_factory=list)
    endpoints: list[EndpointSummary] = Field(default_factory=list)
    as_of: str = Field(description="ISO-8601 UTC when the read behind this detail was taken.")
    stale: bool = Field(
        description="True when a failed refresh made this detail show an earlier read than the request."
    )


class AudioSection(ResponseModel):
    """The audio half of the read model: one list per entity kind."""

    cards: list[CardSummary] = Field(default_factory=list)
    sinks: list[EndpointSummary] = Field(default_factory=list)
    sources: list[EndpointSummary] = Field(default_factory=list)


class SourceCapture(ResponseModel):
    """How the phone's audio is captured, or why it cannot be."""

    kind: Literal["pipewire_node", "pulse_source", "unavailable"]
    node: str | None = None


class SourceFeed(ResponseModel):
    """The single Sendspin source (phone → bridge): one phone, one capture node, its relays."""

    enabled: bool = Field(default=False, description="The source server is running.")
    port: int | None = Field(default=None, description="The port players connect to.")
    state: Literal["off", "waiting", "streaming", "error"] = Field(
        description="off: not running; waiting: running without a phone; streaming: audio is flowing; error: it failed."
    )
    phone_mac: str | None = Field(default=None, description="The paired peer currently streaming, if any.")
    capture: SourceCapture = Field(description="The phone's capture node, or the reason there is none.")
    relays: list[str] = Field(default_factory=list, description="Player ids armed to play the phone.")
    error: str | None = None


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
    audio: AudioSection
    source: SourceFeed | None = Field(
        default=None,
        description="null when this bridge does not report a source (the SENDSPIN_SOURCE feature is off).",
    )
    degraded: list[DegradedSection] = Field(default_factory=list)
