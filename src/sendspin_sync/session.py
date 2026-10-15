"""One tuning session: the state machine the spec's "Session" section describes."""

from __future__ import annotations

import dataclasses
import time
from typing import TYPE_CHECKING, Any, Literal

from sendspin_sync import estimator, planner
from sendspin_sync.models import Measurement, Member, to_dict
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sendspin_sync.models import Proposal, Recording, Snapshot
    from sendspin_sync.ports import ClickTrackHost, GroupControl

Mode = Literal["manual", "semi", "auto"]
Point = Literal["device", "position"]
State = Literal[
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
UNDO_WINDOW_S = 30.0
INACTIVITY_S = 900.0
MAX_ROUNDS = 2
MIN_DELAY_MS = 0
MAX_DELAY_MS = planner.MAX_DELAY_MS


class SyncSession:
    def __init__(
        self,
        *,
        session_id: str,
        group_id: str,
        mode: Mode,
        point: Point,
        control: GroupControl,
        host: ClickTrackHost,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[SyncSession], None] | None = None,
    ) -> None:
        self.id, self.group_id, self.mode, self.point = session_id, group_id, mode, point
        self._control, self._host, self._clock, self._on_change = control, host, clock, on_change
        self.state: State = "created"
        self.members: list[Member] = []
        self.snapshot: Snapshot | None = None
        self.measurements: dict[str, Measurement] = {}
        self.proposal: Proposal | None = None
        self.history: list[dict[str, Any]] = []
        self.error: str | None = None
        self.signal_running = False
        self.soloed: str | None = None
        self.undo_until: float | None = None
        self.round = 1
        self.reference: str | None = None
        self._final: State = "done"
        self._undo_delays: dict[str, int] = {}
        self._written: dict[str, int] = {}
        self.created_at = clock()
        self.last_activity = clock()

    # -- helpers -------------------------------------------------------------------
    def _set(self, state: State) -> None:
        self.state = state
        self._changed()

    def _changed(self) -> None:
        if self._on_change:
            self._on_change(self)

    def touch(self) -> None:
        self.last_activity = self._clock()

    def expired(self) -> bool:
        return self._clock() - self.last_activity > INACTIVITY_S

    def _member(self, mid: str) -> Member:
        for m in self.members:
            if m.ma_player_id == mid:
                return m
        raise ControlError("member_gone", f"{mid} is not in the group")

    def _require(self, *states: State) -> None:
        if self.state not in states:
            if self.state == "restoring":
                raise ControlError("restore_pending", "the session is restoring the group")
            raise ControlError("session_state", f"not allowed in {self.state}")

    # -- lifecycle -----------------------------------------------------------------
    def start(self) -> None:
        self._require("created")
        self.members = self._control.members(self.group_id)
        self.snapshot = self._control.snapshot(self.group_id)
        if self.mode == "manual":
            self._set("measuring")
        else:
            self._begin_signal()

    def _begin_signal(self) -> None:
        self._set("signal_starting")
        self.start_signal()
        self._set("measuring")

    def start_signal(self) -> None:
        self._control.start_signal(self.group_id, self._host.url_for(self.id))
        self.signal_running = True
        self.touch()

    def stop_signal(self) -> None:
        self._control.stop_signal(self.group_id)
        self.signal_running = False
        self.touch()

    def solo(self, member: str | None) -> None:
        if member is not None:
            self._member(member)
        self._control.solo(self.group_id, member)
        self.soloed = member
        self.touch()

    def ab(self, a: str, b: str) -> None:
        self._member(a)
        self._member(b)
        self.solo(a)

    def set_reference(self, member: str) -> None:
        self._member(member)
        self.reference = member
        self.touch()

    # -- measuring -----------------------------------------------------------------
    def record(self, recording: Recording) -> Measurement:
        self._require("measuring", "verifying")
        self._member(recording.member)
        m = estimator.estimate(recording)
        self.measurements[recording.member] = m
        self.touch()
        self._changed()
        return m

    def skip(self, member: str) -> None:
        self._require("measuring", "verifying")
        self._member(member)
        self.measurements[member] = Measurement(member=member, status="skipped", reason="skipped")
        self.touch()
        self._changed()

    def propose(self) -> Proposal:
        self._require("measuring", "verifying")
        spread = planner.spread_ms(self.measurements.values())
        if self.state == "verifying":
            self.history[-1]["spread_after_ms"] = round(spread, 1)
            assert self.proposal is not None
            if self.mode == "auto" and spread > planner.VERIFY_SPREAD_MS and self.round <= MAX_ROUNDS:
                self.proposal = planner.plan(self.measurements.values(), self.members, reference=self.reference)
                self.history.append({"round": self.round, "spread_before_ms": round(spread, 1)})
                self._set("proposed")
                self.apply()
                return self.proposal
            self._begin_restore("done")
            return self.proposal
        try:
            self.proposal = planner.plan(self.measurements.values(), self.members, reference=self.reference)
        except planner.PlanError as exc:
            raise ControlError("no_measurement", str(exc)) from exc
        self.history.append({"round": self.round, "spread_before_ms": round(spread, 1)})
        self._set("proposed")
        return self.proposal

    # -- applying ------------------------------------------------------------------
    def apply(self, rows: Mapping[str, int] | None = None, *, verify: bool = False) -> None:
        """Write the proposal (or ``rows``). Automatic mode always verifies and keeps an undo;
        semi-automatic verifies when asked to, otherwise the session restores and ends."""
        self._require("proposed")
        assert self.proposal is not None
        wanted = dict(rows) if rows is not None else {r.member: r.after_ms for r in self.proposal.rows}
        self._set("applying")
        try:
            for mid, ms in wanted.items():
                member = self._member(mid)
                if not member.adjustable:
                    raise ControlError("not_adjustable", member.name)
                before = member.delay_ms or 0
                if ms != before:
                    self._control.set_delay(member, ms)
                    self._undo_delays.setdefault(mid, before)
                    self._written[mid] = ms
        except ControlError:
            self._set("proposed")
            raise
        self.members = [dataclasses.replace(m, delay_ms=wanted.get(m.ma_player_id, m.delay_ms)) for m in self.members]
        self.touch()
        if self.mode == "auto" and self.round <= MAX_ROUNDS:
            self.undo_until = self._clock() + UNDO_WINDOW_S
            self.verify()
        elif verify:
            self.verify()
        else:
            self._begin_restore("done")

    def verify(self) -> None:
        self._require("applying", "proposed")
        self.round += 1
        self.measurements = {}
        if not self.signal_running:
            self.start_signal()
        self._set("verifying")

    def undo(self) -> None:
        if self.undo_until is None or self._clock() > self.undo_until:
            raise ControlError("undo_expired")
        for mid, ms in self._undo_delays.items():
            self._control.set_delay(self._member(mid), ms)
        self._undo_delays.clear()
        self.undo_until = None
        self.touch()

    # -- manual --------------------------------------------------------------------
    def write_delay(self, member: str, ms: int) -> None:
        m = self._member(member)
        if not m.adjustable:
            raise ControlError("not_adjustable", m.name)
        value = max(MIN_DELAY_MS, min(MAX_DELAY_MS, int(ms)))
        self._control.set_delay(m, value)
        self._written[member] = value
        self.touch()

    def reset(self) -> None:
        assert self.snapshot is not None
        for mid in list(self._written):
            self._control.set_delay(self._member(mid), self.snapshot.delays.get(mid, 0))
        self._written.clear()
        self.touch()

    # -- ending --------------------------------------------------------------------
    def _begin_restore(self, final: State) -> None:
        self._final = final
        self._set("restoring")

    def cancel(self) -> None:
        # A restore already under way keeps its outcome: a DELETE or Retry after a successful apply
        # must not turn "done" into "cancelled".
        if self.state in ("restoring", "done", "cancelled", "failed"):
            return
        self._begin_restore("cancelled")

    def fail(self, detail: str) -> None:
        self.error = detail
        self._begin_restore("failed")

    def restore_tick(self) -> bool:
        self._require("restoring")
        try:
            if self.soloed is not None:
                self._control.solo(self.group_id, None)
                self.soloed = None
            if self.signal_running:
                self._control.stop_signal(self.group_id)
                self.signal_running = False
            if self.snapshot is not None:
                self._control.restore(self.snapshot)
        except ControlError as exc:
            self.error = exc.code
            return False
        self._host.release(self.id)
        self._set(self._final)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group_id": self.group_id,
            "mode": self.mode,
            "point": self.point,
            "state": self.state,
            "members": [to_dict(m) for m in self.members],
            "measurements": {k: to_dict(v) for k, v in self.measurements.items()},
            "proposal": to_dict(self.proposal) if self.proposal else None,
            "history": list(self.history),
            "error": self.error,
            "signal_running": self.signal_running,
            "soloed": self.soloed,
            "reference": self.reference,
            "round": self.round,
            "undo_until_s": None if self.undo_until is None else max(0.0, self.undo_until - self._clock()),
        }
