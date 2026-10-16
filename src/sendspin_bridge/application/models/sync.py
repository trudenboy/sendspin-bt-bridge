"""Group sync tuning resources (sync-contract v1, ``docs/contracts/sync-contract-v1.json``)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from sendspin_bridge.application.models.base import ResponseModel

#: A Sendspin static delay Music Assistant accepts, in milliseconds.
DelayMs = Annotated[int, Field(ge=0, le=5000)]

Mode = Literal["manual", "semi"]
Point = Literal["device", "position"]
ProfileKind = Literal["device", "position"]

#: Stable ``code`` of every problem response the sync endpoints return.
ErrorCode = Literal[
    "ma_unavailable",
    "ma_forbidden",
    "ma_error",
    "session_active",
    "session_expired",
    "session_state",
    "member_gone",
    "not_adjustable",
    "recording_invalid",
    "restore_pending",
    "no_measurement",
    "not_found",
    # the clicker on the group (option C)
    "group_playing",
    "clicker_stopped",
    "sync_mute_unsupported",
]


class SyncMember(ResponseModel):
    ma_player_id: str
    name: str
    origin: Literal["bridge", "sendspin", "other"]
    adjustable: bool
    client_id: str | None = None
    delay_ms: int | None = None
    last_measured_at: str | None = None
    can_mute: bool | None = Field(
        default=None, description="The player can be muted (hearing one speaker alone mutes all the others)."
    )
    available: bool | None = Field(default=None, description="Music Assistant lists the player as available.")
    volume: int | None = Field(default=None, description="The player's volume (0-100) when the group was read.")
    muted: bool | None = Field(default=None, description="The player is muted right now.")


class PendingRestoreOut(ResponseModel):
    """A restore left from before a bridge restart that has not succeeded yet."""

    session_id: str
    group_id: str
    error: str | None = Field(default=None, description="Code of the last failed attempt; null before the first.")


class SyncGroup(ResponseModel):
    id: str
    name: str
    members: list[SyncMember]
    session_id: str | None = Field(default=None, description="The session tuning this group, if any.")
    restore_pending: PendingRestoreOut | None = Field(
        default=None, description="Set while the group still has to be restored after a restart."
    )
    error: str | None = Field(
        default=None, description="Error code when the group's members could not be read (members is then empty)."
    )


class MeasurementOut(ResponseModel):
    member: str
    status: Literal["ok", "not_heard", "unstable", "skipped"]
    phase_ms: float | None = None
    confidence: float = 0.0
    clicks: int = 0
    spread_ms: float | None = None
    reason: str = ""


class ProposalRowOut(ResponseModel):
    member: str
    before_ms: int
    after_ms: int
    delta_ms: int
    clamped: bool


class ProposalOut(ResponseModel):
    reference: str
    rows: list[ProposalRowOut]
    predicted_spread_ms: float
    unverified: list[str]
    warnings: list[str]


class SyncSessionOut(ResponseModel):
    """The session resource; also the payload of ``sync`` events."""

    id: str
    group_id: str
    mode: Mode
    point: Point
    state: Literal[
        "created",
        "measuring",
        "proposed",
        "applying",
        "tuning",
        "restoring",
        "done",
        "cancelled",
        "failed",
    ]
    members: list[SyncMember]
    departed: list[str] = Field(default_factory=list, description="Members that left the group mid-session.")
    measurements: dict[str, MeasurementOut]
    proposal: ProposalOut | None
    history: list[dict[str, Any]]
    error: str | None
    restore_error: str | None = Field(default=None, description="Code of the last failed restore attempt.")
    group_state: str | None = Field(
        description="The group's playback state as last read (playing, idle, paused); null when unknown."
    )
    clicker_playing: bool = Field(description="The group plays this session's click track (as last read).")
    clicker_starts: int = Field(
        description="Clicker starts in this session; recordings from two playbacks are never mixed."
    )
    volumes: dict[str, int | None] = Field(description="Each member's volume as last read or set.")
    volumes_before: dict[str, int] = Field(
        description="Members whose volume this session changed, with the volume from before; put back at the end."
    )
    clicker_left_s: float | None = Field(
        description="Seconds of the click track left while it plays (from its last start); null otherwise."
    )
    muted: list[str] = Field(description="Members this session muted; they are unmuted when it ends.")
    soloed: str | None
    reference: str | None


class SessionCreate(BaseModel):
    group_id: str
    mode: Mode
    point: Point = "device"


class SignalIn(BaseModel):
    action: Literal["start", "stop", "stop_clicker"] = Field(
        description="start: the clicker; stop: whatever the group plays; stop_clicker: only this session's clicker."
    )


class SoloIn(BaseModel):
    member: str | None


class AbIn(BaseModel):
    a: str
    b: str


class ReferenceIn(BaseModel):
    member: str


class SkipIn(BaseModel):
    member: str


class ApplyIn(BaseModel):
    rows: dict[str, DelayMs] | None = Field(default=None, description="Delays to write instead of the proposal.")


class DelaysIn(BaseModel):
    delays: dict[str, DelayMs]


class MemberDelayIn(BaseModel):
    ms: DelayMs


class MemberVolumeIn(BaseModel):
    level: int = Field(ge=0, le=100)


class ProfileOut(ResponseModel):
    id: str
    name: str
    group_id: str
    kind: ProfileKind
    delays: dict[str, int]
    created_at: str
    notes: str = ""


class ProfileCreate(BaseModel):
    group_id: str
    name: str = Field(min_length=1, max_length=100)
    kind: ProfileKind = "device"
    delays: dict[str, DelayMs]
    notes: str = Field(default="", max_length=1000)


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)


class CaptureIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: ProfileKind = "device"
    notes: str = Field(default="", max_length=1000)


class ProfileApplyOut(ResponseModel):
    applied: dict[str, int]
    skipped: list[str]


#: Every model of the contract, by name (the contract export walks this).
CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    m.__name__: m
    for m in (
        SyncMember,
        PendingRestoreOut,
        SyncGroup,
        MeasurementOut,
        ProposalRowOut,
        ProposalOut,
        SyncSessionOut,
        SessionCreate,
        SignalIn,
        SoloIn,
        AbIn,
        ReferenceIn,
        SkipIn,
        ApplyIn,
        DelaysIn,
        MemberDelayIn,
        MemberVolumeIn,
        ProfileOut,
        ProfileCreate,
        ProfilePatch,
        CaptureIn,
        ProfileApplyOut,
    )
}
