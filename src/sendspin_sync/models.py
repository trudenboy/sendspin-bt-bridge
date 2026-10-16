"""Plain data models shared by every part of the sync core."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

Origin = Literal["bridge", "sendspin", "other"]
MeasurementStatus = Literal["ok", "not_heard", "unstable", "skipped"]
ProfileKind = Literal["device", "position"]


@dataclass(frozen=True)
class Member:
    ma_player_id: str
    name: str
    adjustable: bool
    origin: Origin
    client_id: str | None = None
    delay_ms: int | None = None
    last_measured_at: str | None = None


@dataclass
class Recording:
    """int16 little-endian mono PCM."""

    member: str  # Member.ma_player_id
    start_time_s: float  # capture clock, seconds
    sample_rate: int
    pcm: bytes


@dataclass(frozen=True)
class Measurement:
    member: str
    status: MeasurementStatus
    phase_ms: float | None = None
    confidence: float = 0.0
    clicks: int = 0
    spread_ms: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class ProposalRow:
    member: str
    before_ms: int
    after_ms: int
    delta_ms: int
    clamped: bool


@dataclass(frozen=True)
class Proposal:
    reference: str
    rows: tuple[ProposalRow, ...]
    predicted_spread_ms: float
    unverified: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass
class Profile:
    id: str
    name: str
    group_id: str
    kind: ProfileKind
    delays: dict[str, int]
    created_at: str
    notes: str = ""


@dataclass
class Snapshot:
    """What the session reads when it starts: the delays (for reset) and which members were
    muted already (the session never touches those)."""

    group_id: str
    delays: dict[str, int]
    muted: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupPlayback:
    """What the group plays right now, as Music Assistant reports it."""

    state: str | None  # the group's playback_state ("playing", "idle", "paused"; None when unknown)
    media_uri: str | None  # uri of the group's current media
    volumes: dict[str, int | None] = field(default_factory=dict)  # member id -> volume
    muted: dict[str, bool] = field(default_factory=dict)  # member id -> muted right now

    def plays(self, url: str | None) -> bool:
        """The group plays the click track served at *url* (matched on its unguessable token too, in
        case Music Assistant reports the item under its own uri)."""
        if not url or self.state != "playing" or not self.media_uri:
            return False
        token = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".wav")
        return url in self.media_uri or (len(token) >= 16 and token in self.media_uri)


def to_dict(obj: Any) -> dict[str, Any]:
    # tuples become lists; bytes are refused on purpose (Recording is never serialised)
    result: dict[str, Any] = json.loads(json.dumps(dataclasses.asdict(obj)))
    return result


def profile_from_dict(d: Mapping[str, Any]) -> Profile:
    return Profile(
        id=str(d["id"]),
        name=str(d["name"]),
        group_id=str(d["group_id"]),
        kind=d.get("kind", "device"),
        delays={str(k): int(v) for k, v in dict(d.get("delays") or {}).items()},
        created_at=str(d.get("created_at") or ""),
        notes=str(d.get("notes") or ""),
    )


def snapshot_from_dict(d: Mapping[str, Any]) -> Snapshot:
    return Snapshot(
        group_id=str(d["group_id"]),
        delays={str(k): int(v) for k, v in dict(d.get("delays") or {}).items()},
        muted={str(k): bool(v) for k, v in dict(d.get("muted") or {}).items()},
    )
