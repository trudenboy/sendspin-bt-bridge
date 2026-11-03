"""The phone source as the API shows it: capture, Music Assistant pairing and state."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from sendspin_bridge.application.models.base import ResponseModel

SourceStateName = Literal["off", "pairing", "waiting", "streaming", "error"]


class SourceCapture(ResponseModel):
    """How the phone's audio is captured, or why it cannot be."""

    kind: Literal["pipewire_node", "pulse_source", "unavailable"]
    node: str | None = None


class SourceMa(ResponseModel):
    """The bridge's Sendspin source-client link to the Music Assistant server."""

    server: str | None = Field(default=None, description="The Sendspin server URL the source dials.")
    connected: bool = Field(default=False, description="The source client is admitted by the server.")
    paired: bool = Field(default=False, description="A long-term pairing PSK is stored.")
    pairing_required: bool = Field(default=False, description="No PSK: the operator must enter a code in MA.")
    pairing_code: str | None = Field(default=None, description="The 8-digit code, while a pairing window is open.")
    pairing_seconds_left: int = Field(default=0, description="Seconds the MA pairing window stays open.")
    error: str | None = Field(default=None, description="Stable code: ma_unreachable, ma_pairing_rejected, …")


class SourceDevice(ResponseModel):
    """A paired Bluetooth device that can feed the source."""

    mac: str
    name: str = ""
    connected: bool = False
    active: bool = False


class SourceInputStatus(ResponseModel):
    """One selectable source input: a Bluetooth bond or a local audio input."""

    id: str
    kind: Literal["bluetooth", "local"]
    name: str
    description: str = ""
    active: bool = False
    available: bool = True
    connected: bool = False
    monitor: bool = False
    mac: str | None = None
    node: str | None = None


class SourceTarget(ResponseModel):
    """A Music Assistant player or group that can play the source."""

    player_id: str
    name: str
    type: Literal["player", "group"]
    available: bool = True
    supported: bool = Field(default=True, description="The player accepts play_media/AudioSource playback.")


class SourceTargets(ResponseModel):
    """The automatic-play target choices and the saved one."""

    targets: list[SourceTarget] = Field(default_factory=list)
    selected: str | None = None
    ma_api_available: bool = True
    error: str | None = Field(default=None, description="Why the target list could not be read, if it could not.")


class SourceInputs(ResponseModel):
    """Configured inputs and unconfigured local candidates for the add dialog."""

    active_input: str | None = None
    inputs: list[SourceInputStatus] = Field(default_factory=list)
    local_candidates: list[SourceInputStatus] = Field(default_factory=list)
    capture_available: bool = True
    capture_kind: str = "unknown"


class SourceFeed(ResponseModel):
    """The single Sendspin source: one capture, and the link to Music Assistant."""

    enabled: bool = Field(default=False, description="The source client is enabled by the operator.")
    state: SourceStateName = Field(
        default="off",
        description="off: disabled; pairing: a code must be entered in MA; waiting: connected without a phone; "
        "streaming: the phone's audio is reaching MA; error: it failed.",
    )
    phone_mac: str | None = Field(default=None, description="The paired phone currently streaming, if any.")
    paired_phone_mac: str | None = Field(default=None, description="A phone paired for the source, before it streams.")
    capture: SourceCapture = Field(description="The phone's capture node, or the reason there is none.")
    ma: SourceMa = Field(default_factory=SourceMa, description="The link to the Music Assistant server.")
    devices: list[SourceDevice] = Field(default_factory=list, description="Paired devices that can feed the source.")
    error: str | None = None
