"""In-memory fakes of the sync core's ports: a group control that records calls and a click-track host."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sendspin_sync.models import Member, Snapshot
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeControl:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.delays_now: dict[str, int] = {"a": 0, "b": 0}
        self.fail_restore = 0
        self.member_list = [
            Member("a", "A", True, "bridge", delay_ms=0),
            Member("b", "B", True, "sendspin", delay_ms=0),
            Member("cast", "Cast", False, "other"),
        ]

    def members(self, group_id: str) -> list[Member]:
        return list(self.member_list)

    def delays(self, members: Sequence[Member]) -> dict[str, int | None]:
        return {m.ma_player_id: self.delays_now.get(m.ma_player_id) for m in members}

    def snapshot(self, group_id: str) -> Snapshot:
        return Snapshot(
            group_id=group_id,
            delays=dict(self.delays_now),
            volumes={"a": 40, "b": 40},
            muted={"a": False, "b": False},
            playing=True,
        )

    def start_signal(self, group_id: str, url: str) -> None:
        self.calls.append(("start", url))

    def stop_signal(self, group_id: str) -> None:
        self.calls.append(("stop",))

    def solo(self, group_id: str, member: str | None) -> None:
        self.calls.append(("solo", member))

    def set_delay(self, member: Member, ms: int) -> None:
        self.delays_now[member.ma_player_id] = ms
        self.calls.append(("set", member.ma_player_id, ms))

    def restore(self, snapshot: Snapshot) -> None:
        if self.fail_restore:
            self.fail_restore -= 1
            raise ControlError("ma_unavailable")
        self.calls.append(("restore",))


class FakeHost:
    def __init__(self) -> None:
        self.released: list[str] = []

    def url_for(self, session_id: str) -> str:
        return f"http://bridge/api/v1/sync/signal/{session_id}.wav"

    def release(self, session_id: str) -> None:
        self.released.append(session_id)
