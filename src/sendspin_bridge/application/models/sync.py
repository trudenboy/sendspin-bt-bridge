"""Group sync tuning resources (sync-contract v1, ``docs/contracts/sync-contract-v1.json``)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from sendspin_bridge.application.models.base import ResponseModel

#: A Sendspin static delay Music Assistant accepts, in milliseconds.
DelayMs = Annotated[int, Field(ge=0, le=5000)]

Mode = Literal["manual", "semi", "auto"]
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
    "undo_expired",
    "no_measurement",
    "not_found",
    # the announcement signal (one clip per round, cannot be stopped early)
    "signal_playing",
    "signal_waiting",
    "signal_starting",
    "signal_stopped",
]


class SyncMember(ResponseModel):
    ma_player_id: str
    name: str
    origin: Literal["bridge", "sendspin", "other"]
    adjustable: bool
    client_id: str | None = None
    delay_ms: int | None = None
    last_measured_at: str | None = None


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
        "signal_starting",
        "measuring",
        "proposed",
        "applying",
        "verifying",
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
    signal_running: bool = Field(description="The clicks are audible: a clip plays, has settled and is not silenced.")
    signal_waiting: bool = Field(
        description="A new round waits for the current clip to end before its own clip starts (it cannot be stopped)."
    )
    signal_clip: int = Field(description="Clips started in this session; recordings from two clips are never mixed.")
    music_returns_in_s: float | None = Field(
        description="Seconds until the current clip ends and Music Assistant restores music and volumes; null when no clip plays."
    )
    soloed: str | None
    reference: str | None
    round: int
    undo_until_s: float | None = Field(description="Seconds left to undo an automatic apply; null when closed.")


class SessionCreate(BaseModel):
    group_id: str
    mode: Mode
    point: Point = "device"


class SignalIn(BaseModel):
    action: Literal["start", "stop"]


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
    verify: bool = Field(default=False, description="Semi-automatic: measure again after applying.")


class DelaysIn(BaseModel):
    delays: dict[str, DelayMs]


class MemberDelayIn(BaseModel):
    ms: DelayMs


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
        ProfileOut,
        ProfileCreate,
        ProfilePatch,
        CaptureIn,
        ProfileApplyOut,
    )
}
