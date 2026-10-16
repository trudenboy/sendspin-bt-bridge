"""One tuning session: the state machine the spec's "Session" section describes."""

from __future__ import annotations

import dataclasses
import functools
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Literal

from sendspin_sync import click_track, estimator, planner
from sendspin_sync.models import Measurement, Member, to_dict
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Concatenate

    from sendspin_sync.models import Proposal, Recording, Snapshot
    from sendspin_sync.ports import ClickTrackHost, GroupControl

logger = logging.getLogger(__name__)

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
# The signal is one announcement of the click track per measurement round (stage 0, option A):
# Music Assistant plays the clip once, cannot stop it early and restores music and volumes when it
# ends. SIGNAL_TAIL_S covers its start latency and its own restore; the clicks count as running
# once SIGNAL_SETTLE_S has passed, so a solo is never set while the announcement still starts.
SIGNAL_CLIP_S = float(click_track.DURATION_S)
SIGNAL_TAIL_S = 5.0
SIGNAL_SETTLE_S = 3.0
# The clock is only an estimate of the clip's end (Music Assistant's start latency is not observed):
# past it, the clip still counts as playing while Music Assistant reports an announcement under way,
# polled every SIGNAL_POLL_S, for at most SIGNAL_OVERRUN_S more.
SIGNAL_OVERRUN_S = 60.0
SIGNAL_POLL_S = 2.0
# After the volumes are put back at a clip's end, they are checked once more this much later: Music
# Assistant's own restore (or a kiosk re-reporting its level) may land after ours.
RESTORE_RECHECK_S = 5.0
_ENDED: tuple[State, ...] = ("done", "cancelled", "failed")
_ACTIVE: tuple[State, ...] = ("signal_starting", "measuring", "proposed", "applying", "verifying")


def _locked[**P, R](fn: Callable[Concatenate[SyncSession, P], R]) -> Callable[Concatenate[SyncSession, P], R]:
    @functools.wraps(fn)
    def wrapper(self: SyncSession, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


def _clamp(ms: int) -> int:
    return max(MIN_DELAY_MS, min(MAX_DELAY_MS, int(ms)))


class SyncSession:
    """One tuning session of one group.

    Thread safety: transitions run under an internal ``threading.RLock``, so API worker
    threads, the store's restore loop and timers may call into the same session concurrently;
    transitions are atomic. The lock is re-entrant because ``on_change`` is invoked from inside
    transitions and may read the session (``to_dict()``).

    Done without the lock: ``expired()`` (a plain read of ``last_activity``, so the store's
    ticker never waits behind a busy session); the membership read of ``refresh_members()``,
    ``record()``, ``skip()`` and ``propose()`` (``GroupControl.members()`` runs first, the diff
    is applied under the lock); and the estimator in ``record()``, which re-checks the state and
    the measurement round it started in before storing the result (``session_state`` if a new
    round began).

    Lock invariant: apart from that membership read, the lock is held across port I/O
    (``GroupControl`` calls: signal, solo, delays, restore) and across ``on_change``. So never
    call session methods from the event-loop thread (they may block on network I/O or on
    another thread holding the lock), and ``on_change`` must not block waiting on another
    thread: callers snapshot ``to_dict()`` on the calling thread and post it.

    ``error`` is the detail given to ``fail()`` and is never overwritten; ``restore_error``
    is the code of the last failed restore attempt and is cleared when a restore succeeds.
    """

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
        self.departed: list[str] = []  # members that left the group mid-session
        self._gone: dict[str, Member] = {}
        self.snapshot: Snapshot | None = None
        self.measurements: dict[str, Measurement] = {}
        self.proposal: Proposal | None = None
        self.history: list[dict[str, Any]] = []
        self.error: str | None = None
        self.restore_error: str | None = None
        self.soloed: str | None = None
        self.silenced = False  # stop_signal: every member at volume 0 until the clip ends
        self.signal_waiting = False  # a new round's clip starts once the current one ended
        self.signal_clip = 0  # clips started so far; measurements never mix two of them
        self._clip_start: float | None = None
        self._clip_end: float | None = None
        self._published_running = False  # signal_running as the last change reported it
        self._levels_at: float | None = None  # when the snapshot's volumes are due again (after a clip)
        self._levels_recheck = False  # after that restore, check once more RESTORE_RECHECK_S later
        self.undo_until: float | None = None
        self.round = 1
        self.reference: str | None = None
        self._final: State = "done"
        self._undo_delays: dict[str, int] = {}
        self._written: dict[str, int] = {}
        self._lock = threading.RLock()
        self._generation = 0  # bumped whenever a new measurement round starts
        self.created_at = clock()
        self.last_activity = clock()

    # -- helpers -------------------------------------------------------------------
    def _set(self, state: State) -> None:
        self.state = state
        self._changed()

    def _changed(self) -> None:
        self._published_running = self.signal_running
        if self._on_change:
            self._on_change(self)

    @_locked
    def touch(self) -> None:
        self.last_activity = self._clock()

    def expired(self) -> bool:
        # lock-free on purpose: the store's ticker must not wait behind a session busy in MA I/O
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

    def _require_active(self) -> None:
        """Side operations that touch the group are only allowed while the session runs."""
        self._require(*_ACTIVE)

    def _known(self, mid: str) -> Member:
        """A current or departed member: putting back a delay must still reach a member that left."""
        return self._gone[mid] if mid in self._gone else self._member(mid)

    def _before(self, member: Member) -> int:
        if member.delay_ms is not None:
            return member.delay_ms
        return self.snapshot.delays.get(member.ma_player_id, 0) if self.snapshot else 0

    def _set_member_delay(self, mid: str, ms: int) -> None:
        self.members = [dataclasses.replace(m, delay_ms=ms) if m.ma_player_id == mid else m for m in self.members]

    # -- lifecycle -----------------------------------------------------------------
    @_locked
    def start(self) -> None:
        self._require("created")
        self.members = self._control.members(self.group_id)
        self.snapshot = self._control.snapshot(self.group_id)
        if self.mode == "manual":
            self._set("measuring")
            return
        try:
            self._set("signal_starting")
            self._start_clip()
        except Exception:
            if self.clip_playing:  # the click track plays: keep tracking it so a restore waits for it
                raise
            # nothing reached the group: back to created so start() can be retried
            self.members, self.snapshot = [], None
            self._set("created")
            raise
        self._set("measuring")

    # -- the signal ------------------------------------------------------------------
    @property
    def clip_playing(self) -> bool:
        """An announcement of the click track is under way (audible or silenced)."""
        return self._clip_end is not None and self._clock() < self._clip_end

    @property
    def signal_running(self) -> bool:
        """The clicks are audible: a clip plays, has settled and is not silenced."""
        if not self.clip_playing or self.silenced:
            return False
        assert self._clip_start is not None
        return self._clock() >= self._clip_start + SIGNAL_SETTLE_S

    def music_returns_in_s(self) -> float | None:
        """Seconds until the current clip ends and Music Assistant brings the music back."""
        if not self.clip_playing:
            return None
        assert self._clip_end is not None
        return max(0.0, self._clip_end - self._clock())

    def _clip_still_announced(self) -> None:
        """The clock says the clip is over: keep it open while Music Assistant still reports an
        announcement (it started late, or its own restore still runs), up to SIGNAL_OVERRUN_S past the
        clock's end. Unreachable means unknown: the clock decides."""
        if self._clip_end is None or self._clip_start is None or self.clip_playing:
            return
        now = self._clock()
        cap = self._clip_start + SIGNAL_CLIP_S + SIGNAL_TAIL_S + SIGNAL_OVERRUN_S
        if now >= cap:
            return
        try:
            active = self._control.announcement_active(self.group_id)
        except ControlError as exc:
            logger.debug("Reading the announcement state of sync session %s failed: %s", self.id, exc)
            return
        if active:
            self._clip_end = min(cap, now + SIGNAL_POLL_S)

    def _forget_ended_clip(self) -> bool:
        if self._clip_end is None or self.clip_playing:
            return False
        self._clip_start = self._clip_end = None
        self.silenced = False
        # Music Assistant restored every member's own volume (never their Sendspin protocol players'):
        # the solo is gone, and the snapshot's levels are put back by the next tick
        self.soloed = None
        self._levels_at, self._levels_recheck = self._clock(), True
        return True

    def _restore_levels(self) -> bool:
        """Put the snapshot's volumes back after a clip ended mid-session (best effort: a failure is
        retried on the next tick), then once more RESTORE_RECHECK_S later."""
        if self._levels_at is None or self.snapshot is None or self.clip_playing or self._clock() < self._levels_at:
            return False
        try:
            self._control.restore(self.snapshot)
        except ControlError as exc:
            logger.warning("Putting the volumes back in sync session %s failed (retrying): %s", self.id, exc)
            return False
        if self._levels_recheck:
            self._levels_at, self._levels_recheck = self._clock() + RESTORE_RECHECK_S, False
        else:
            self._levels_at = None
        return True

    def _start_clip(self) -> None:
        self._forget_ended_clip()
        if self._levels_at is not None and self._levels_recheck:
            # the last clip's end was not handled yet: MA snapshots the volumes for the new one
            self._restore_levels()
        self._control.start_signal(self.group_id, self._host.url_for(self.id))
        self._levels_at, self._levels_recheck = None, False
        now = self._clock()
        self._clip_start, self._clip_end = now, now + SIGNAL_CLIP_S + SIGNAL_TAIL_S
        self.silenced = self.signal_waiting = False
        self.soloed = None  # a new announcement starts from Music Assistant's volumes
        self.signal_clip += 1
        if self.mode != "manual" and self.state in ("measuring", "verifying") and self.measurements:
            # phases from two playbacks cannot be compared: the round starts again
            self.measurements = {}
            self._generation += 1

    def _silence(self) -> None:
        if self.clip_playing and not self.silenced:
            self._control.stop_signal(self.group_id)
            self.silenced = True

    @_locked
    def start_signal(self) -> None:
        """Play the clicks: unsilence a clip that still runs, else start a new announcement."""
        self._require_active()
        if self.signal_waiting:
            pass  # the next clip starts by itself once the current one ended
        elif self.clip_playing:
            if self.silenced:
                self._control.solo(self.group_id, self.soloed)
                self.silenced = False
        else:
            self._start_clip()
        self.touch()
        self._changed()

    @_locked
    def stop_signal(self) -> None:
        """Silence every member until the clip ends: an announcement cannot be stopped early."""
        self._require_active()
        self.signal_waiting = False
        self._silence()
        self.touch()
        self._changed()

    @_locked
    def solo(self, member: str | None) -> None:
        self._require_active()
        if member is None:
            if self.signal_running:
                self._control.solo(self.group_id, None)
            self.soloed = None
            self.touch()
            self._changed()
            return
        self._member(member)
        if not self.signal_running:
            if self.signal_waiting:
                raise ControlError("signal_waiting", "the next round starts when the previous clip ends")
            if self.clip_playing and not self.silenced:
                raise ControlError("signal_starting", "Music Assistant is starting the clicks")
            raise ControlError("signal_stopped", "play the clicks first")
        self._control.solo(self.group_id, member)
        self.soloed = member
        self.touch()
        self._changed()

    @_locked
    def tick(self) -> None:
        """Called about once a second: report clicks that became audible (settled) or a clip that
        ended, and start the clip of a round that waited for the previous one."""
        if self.state in _ENDED or self.state == "restoring":
            return
        self._clip_still_announced()
        changed = self._forget_ended_clip() or self.signal_running != self._published_running
        self._restore_levels()
        if self.signal_waiting and not self.clip_playing:
            try:
                self._start_clip()
                changed = True
            except ControlError as exc:  # still waiting: the next tick tries again
                logger.debug("Starting the next clip of sync session %s failed: %s", self.id, exc)
            else:
                if self.undo_until is not None and self._clip_start is not None:
                    # the check round's clicks are audible only now: the undo window counts from then
                    self.undo_until = max(self.undo_until, self._clip_start + SIGNAL_SETTLE_S + UNDO_WINDOW_S)
        if changed:
            self._changed()

    @_locked
    def ab(self, a: str, b: str) -> None:
        self._require_active()
        self._member(a)
        self._member(b)
        self.solo(a)

    @_locked
    def set_reference(self, member: str) -> None:
        self._require_active()
        self._member(member)
        self.reference = member
        self.touch()

    # -- measuring -----------------------------------------------------------------
    def _membership(self) -> set[str] | None:
        """Read the group's membership without the lock (it is Music Assistant I/O); None when unknown
        or when the session is not measuring."""
        if self.state not in ("measuring", "verifying"):
            return None
        try:
            return {m.ma_player_id for m in self._control.members(self.group_id)}
        except ControlError:
            return None

    def refresh_members(self) -> None:
        """Drop members that left the group (with their measurements) so planning goes on without
        them; they are listed in ``departed`` and refused with ``member_gone``. Best effort: when the
        membership cannot be read, the session keeps the members it has."""
        current = self._membership()
        with self._lock:
            self._apply_membership(current)

    def _apply_membership(self, current: set[str] | None) -> None:
        if current is None:
            return
        gone = [m for m in self.members if m.ma_player_id not in current]
        if not gone:
            return
        for m in gone:
            self._gone[m.ma_player_id] = m
            self.departed.append(m.ma_player_id)
            self.measurements.pop(m.ma_player_id, None)
            if self.reference == m.ma_player_id:
                self.reference = None
        self.members = [m for m in self.members if m.ma_player_id in current]
        self._changed()

    def record(self, recording: Recording) -> Measurement:
        current = self._membership()
        with self._lock:
            self._require("measuring", "verifying")
            if self.signal_waiting:
                raise ControlError("signal_waiting", "the next round starts when the previous clip ends")
            self._apply_membership(current)
            self._member(recording.member)
            generation = self._generation
        m = estimator.estimate(recording)  # slow: outside the lock
        with self._lock:
            self._require("measuring", "verifying")
            if self._generation != generation:
                raise ControlError("session_state", "the measurement round changed while estimating")
            self._member(recording.member)
            self.measurements[recording.member] = m
            self.touch()
            self._changed()
        return m

    def skip(self, member: str) -> None:
        current = self._membership()
        self._skip(current, member)

    @_locked
    def _skip(self, current: set[str] | None, member: str) -> None:
        self._require("measuring", "verifying")
        self._apply_membership(current)
        self._member(member)
        self.measurements[member] = Measurement(member=member, status="skipped", reason="skipped")
        self.touch()
        self._changed()

    def propose(self) -> Proposal:
        current = self._membership()
        return self._propose(current)

    @_locked
    def _propose(self, current: set[str] | None) -> Proposal:
        self._require("measuring", "verifying")
        self._apply_membership(current)
        self.touch()
        spread = planner.spread_ms(self.measurements.values())
        if self.state == "verifying":
            self.history[-1]["spread_after_ms"] = round(spread, 1)
            assert self.proposal is not None
            if self.mode == "auto" and spread > planner.VERIFY_SPREAD_MS and self.round <= MAX_ROUNDS:
                try:
                    self.proposal = planner.plan(self.measurements.values(), self.members, reference=self.reference)
                except planner.PlanError as exc:
                    raise ControlError("no_measurement", str(exc)) from exc
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
    @_locked
    def apply(self, rows: Mapping[str, int] | None = None, *, verify: bool = False) -> None:
        """Write the proposal (or ``rows``). Automatic mode always verifies and keeps an undo;
        semi-automatic verifies when asked to, otherwise the session restores and ends."""
        self._require("proposed")
        assert self.proposal is not None
        source = rows if rows is not None else {r.member: r.after_ms for r in self.proposal.rows}
        wanted = {mid: _clamp(ms) for mid, ms in source.items()}
        targets: list[tuple[Member, int]] = []
        for mid, ms in wanted.items():  # refuse before the first write, so a refusal writes nothing
            member = self._member(mid)
            if not member.adjustable:
                raise ControlError("not_adjustable", member.name)
            targets.append((member, ms))
        self._set("applying")
        done: list[tuple[Member, int]] = []  # (member, value before) written by this call
        try:
            for member, ms in targets:
                before = self._before(member)
                if ms != before:
                    self._control.set_delay(member, ms)
                    done.append((member, before))
        except ControlError:
            self._roll_back(done, wanted)
            self._set("proposed")
            raise
        for member, before in done:
            self._undo_delays.setdefault(member.ma_player_id, before)
            self._written[member.ma_player_id] = wanted[member.ma_player_id]
        self.members = [dataclasses.replace(m, delay_ms=wanted.get(m.ma_player_id, m.delay_ms)) for m in self.members]
        self.touch()
        if self.mode == "auto" and self.round <= MAX_ROUNDS:
            # provisional: undo must stay possible even if verify() does not get through
            self.undo_until = self._clock() + SIGNAL_SETTLE_S + UNDO_WINDOW_S
            self.verify()
            # 30 s from when the check round's clicks are audible: its clip starts now, or when the
            # clip still playing ends (it cannot be stopped), so the operator can hear the result first
            starts = self._clip_end if self.signal_waiting and self._clip_end is not None else self._clip_start
            self.undo_until = (starts if starts is not None else self._clock()) + SIGNAL_SETTLE_S + UNDO_WINDOW_S
        elif verify:
            self.verify()
        else:
            self._begin_restore("done")

    def _roll_back(self, done: list[tuple[Member, int]], wanted: Mapping[str, int]) -> None:
        """Best effort: put back what a failed apply already wrote. A member whose value cannot be put
        back keeps the written value in ``members`` (and in the undo/reset bookkeeping), so the session
        shows what Music Assistant holds."""
        for member, before in reversed(done):
            mid = member.ma_player_id
            try:
                self._control.set_delay(member, before)
            except ControlError:
                self._undo_delays.setdefault(mid, before)
                self._written[mid] = wanted[mid]
                self._set_member_delay(mid, wanted[mid])

    @_locked
    def verify(self) -> None:
        self._require("applying", "proposed")
        self.round += 1
        self.measurements = {}
        self._generation += 1
        if self.clip_playing:
            # a round needs a clip of its own and this one cannot be stopped: silence it and start
            # the next one when it ends (tick). Best effort: the delays are written already, and a
            # member left audible only hears the old clicks until the clip ends.
            try:
                self._silence()
            except ControlError as exc:
                logger.warning("Silencing the clip of sync session %s failed: %s", self.id, exc)
            self.signal_waiting = True
        else:
            try:
                self._start_clip()
            except ControlError as exc:  # the delays are written: wait, the tick retries the start
                logger.warning("Starting the check clip of sync session %s failed (retrying): %s", self.id, exc)
                self.signal_waiting = True
        self._set("verifying")

    @_locked
    def undo(self) -> None:
        """Put back the delays from before the automatic apply(s). Allowed in any state while the
        window is open (including restoring); during verification it ends the session without
        refining, since a refinement would be planned against the undone delays."""
        if self.undo_until is None or self._clock() > self.undo_until:
            raise ControlError("undo_expired")
        for mid, ms in list(self._undo_delays.items()):
            self._control.set_delay(self._known(mid), ms)
            self._set_member_delay(mid, ms)
            self._written.pop(mid, None)
            del self._undo_delays[mid]
        self.undo_until = None
        self.touch()
        if self.state == "verifying":
            self._begin_restore("done")
        else:
            self._changed()

    # -- manual --------------------------------------------------------------------
    @_locked
    def write_delay(self, member: str, ms: int) -> None:
        self._require_active()
        m = self._member(member)
        if not m.adjustable:
            raise ControlError("not_adjustable", m.name)
        value = _clamp(ms)
        self._control.set_delay(m, value)
        self._written[member] = value
        self._set_member_delay(member, value)
        self.touch()
        self._changed()

    @_locked
    def reset(self) -> None:
        self._require_active()
        assert self.snapshot is not None
        for mid in list(self._written):
            value = self.snapshot.delays.get(mid, 0)
            self._control.set_delay(self._known(mid), value)
            self._set_member_delay(mid, value)
            del self._written[mid]
        self.touch()
        self._changed()

    # -- ending --------------------------------------------------------------------
    def _begin_restore(self, final: State) -> None:
        self._final = final
        self._set("restoring")

    @_locked
    def cancel(self) -> None:
        # A restore already under way keeps its outcome: a DELETE or Retry after a successful apply
        # must not turn "done" into "cancelled".
        if self.state in ("restoring", "done", "cancelled", "failed"):
            return
        self._begin_restore("cancelled")

    @_locked
    def fail(self, detail: str) -> None:
        if self.state in _ENDED:
            return
        if self.error is None:
            self.error = detail
        if self.state == "restoring":  # a restore under way keeps its outcome, like cancel()
            self._changed()
            return
        self._begin_restore("failed")

    @_locked
    def restore_tick(self) -> bool:
        """One restore attempt; True once the group is restored. While the clip still plays, every
        member is kept at volume 0 and the attempt waits (False, no ``restore_error``): Music
        Assistant restores music and volumes at the clip's end, then the volumes are checked."""
        self._require("restoring")
        try:
            self._clip_still_announced()
            if self.clip_playing:
                self.signal_waiting = False
                silent = self.silenced
                self._silence()
                if self.restore_error is not None or not silent:
                    self.restore_error = None
                    self._changed()
                return False
            self._forget_ended_clip()
            self.signal_waiting = False
            self.soloed = None
            if self.snapshot is not None:
                self._control.restore(self.snapshot)
        except ControlError as exc:
            self.restore_error = exc.code
            self._changed()
            return False
        self.restore_error = None
        self._host.release(self.id)
        self._set(self._final)
        return True

    @_locked
    def hand_back(self) -> None:
        """At shutdown while a clip plays: every member (and its Sendspin protocol player) back at its
        level from the snapshot instead of silent, so nothing stays at 0 if the bridge never restarts;
        Music Assistant restores the music when the clip ends, a next boot still checks the volumes."""
        self._require("restoring")
        if not self.clip_playing:
            return
        self._control.solo(self.group_id, None)
        self.silenced = False
        self.soloed = None
        self._changed()

    @_locked
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "group_id": self.group_id,
            "mode": self.mode,
            "point": self.point,
            "state": self.state,
            "members": [to_dict(m) for m in self.members],
            "departed": list(self.departed),
            "measurements": {k: to_dict(v) for k, v in self.measurements.items()},
            "proposal": to_dict(self.proposal) if self.proposal else None,
            "history": list(self.history),
            "error": self.error,
            "restore_error": self.restore_error,
            "signal_running": self.signal_running,
            "signal_waiting": self.signal_waiting,
            "signal_clip": self.signal_clip,
            "music_returns_in_s": self.music_returns_in_s(),
            "soloed": self.soloed,
            "reference": self.reference,
            "round": self.round,
            "undo_until_s": None if self.undo_until is None else max(0.0, self.undo_until - self._clock()),
        }
