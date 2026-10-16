"""What the sync core needs from the outside world; the bridge supplies the adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sendspin_sync.models import GroupPlayback, Member, Profile, Snapshot


class GroupControl(Protocol):
    def members(self, group_id: str) -> list[Member]: ...
    def delays(self, members: Sequence[Member]) -> dict[str, int | None]: ...
    def snapshot(self, group_id: str) -> Snapshot: ...
    def playback(self, group_id: str) -> GroupPlayback: ...
    def stop_group(self, group_id: str) -> None: ...
    def start_clicker(self, group_id: str, url: str) -> None:
        """Play the click track at *url* on the group, replacing what its queue holds (user decision
        2026-10-16: the queue is not kept; when the track ends the queue ends and the group stops)."""
        ...

    def mute(self, member: str, muted: bool) -> None:
        """Mute or unmute one member's player; a player that cannot be muted raises
        ``sync_mute_unsupported`` naming it (unmuting such a player is a no-op)."""
        ...

    def set_volume(self, member: str, level: int) -> None: ...
    def set_delay(self, member: Member, ms: int) -> None: ...


class ProfileRepository(Protocol):
    def list(self, group_id: str) -> list[Profile]: ...
    def get(self, profile_id: str) -> Profile | None: ...
    def save(self, profile: Profile) -> None: ...
    def delete(self, profile_id: str) -> None: ...


class ClickTrackHost(Protocol):
    def url_for(self, session_id: str) -> str: ...
    def release(self, session_id: str) -> None: ...


class ControlError(Exception):
    """Raised by the session and the adapters; ``code`` is stable and maps to an API error.
    ``member`` names the member the error is about, when there is one."""

    def __init__(self, code: str, detail: str = "", *, member: str | None = None) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.member = member
