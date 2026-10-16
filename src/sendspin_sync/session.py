"""One tuning session: the state machine the spec's "Session" section describes."""

from __future__ import annotations

import dataclasses
import functools
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Literal

from sendspin_sync import estimator, planner
from sendspin_sync.models import Measurement, Member, to_dict
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Concatenate

    from sendspin_sync.models import GroupPlayback, Proposal, Recording, Snapshot
    from sendspin_sync.ports import ClickTrackHost, GroupControl

logger = logging.getLogger(__name__)

Mode = Literal["manual", "semi", "auto"]
Point = Literal["device", "position"]
State = Literal[
    "created",
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
# The signal path (option C, user decision 2026-10-16): no music plays during a session. The operator
# stops the group, starts the clicker (the click track queued on the group like any URL) and sets the
# volumes; solo mutes the other members. The tick reads the group's playback this often, so the page
# follows a group stopped or started from Music Assistant and a click track that played to its end.
PLAYBACK_POLL_S = 2.0
_ENDED: tuple[State, ...] = ("done", "cancelled", "failed")
_ACTIVE: tuple[State, ...] = ("measuring", "proposed", "applying", "verifying")


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
    (``GroupControl`` calls: playback, clicker, mutes, volumes, delays) and across ``on_change``. So never
    call session methods from the event-loop thread (they may block on network I/O or on
    another thread holding the lock), and ``on_change`` must not block waiting on another
    thread: callers snapshot ``to_dict()`` on the calling thread and post it.

    ``error`` is the detail given to ``fail()`` and is never overwritten; ``restore_error``
    is the code of the last failed restore attempt and is cleared when a restore succeeds.

    ``muted`` holds the members this session muted (and only those: a member muted before the
    session is never touched). A member is added before its mute is sent, so a crash in between
    leaves at most a needless unmute; the end unmutes every one of them, then stops the group if
    the session's clicker is what it plays.
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
        self.muted: set[str] = set()  # members this session muted (unmuted at the end)
        self.group_state: str | None = None  # the group's playback state as last read
        self.clicker_playing = False  # the group plays this session's click track (as last read)
        self.clicker_starts = 0  # clicker starts so far; measurements never mix two playbacks
        self.clicker_url: str | None = None
        self.volumes: dict[str, int | None] = {}
        self._polled_at: float | None = None
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
        """Read the group (members, delays, mutes, playback); nothing is written to it."""
        self._require("created")
        try:
            self.members = self._control.members(self.group_id)
            self.snapshot = self._control.snapshot(self.group_id)
            self._observe(self._control.playback(self.group_id))
        except Exception:
            self.members, self.snapshot = [], None
            raise
        self._polled_at = self._clock()
        self._set("measuring")

    # -- the group and the clicker ---------------------------------------------------
    def _observe(self, playback: GroupPlayback) -> bool:
        """Take one reading of the group; True when anything the page shows changed."""
        playing = playback.plays(self.clicker_url)
        volumes = dict(playback.volumes)
        changed = (playback.state, playing, volumes) != (self.group_state, self.clicker_playing, self.volumes)
        self.group_state, self.clicker_playing, self.volumes = playback.state, playing, volumes
        return changed

    def _require_clicker(self) -> None:
        """Solo and recordings need the clicks: read the group now, refuse when the clicker stopped."""
        if self._observe(self._control.playback(self.group_id)):
            self._changed()
        if not self.clicker_playing:
            raise ControlError("clicker_stopped", "start the clicker first")

    @_locked
    def stop_group(self) -> None:
        """Stop whatever the group plays (the music before the clicker starts, or the clicker)."""
        self._require_active()
        self._control.stop_group(self.group_id)
        try:
            self._observe(self._control.playback(self.group_id))
        except ControlError:
            self.group_state, self.clicker_playing = "idle", False
        self.touch()
        self._changed()

    @_locked
    def start_clicker(self) -> None:
        """Play the click track on the group; refused (``group_playing``) while the group plays
        anything else. Starting it again while it plays (or after it ended) starts it over."""
        self._require_active()
        self._observe(self._control.playback(self.group_id))
        if self.group_state == "playing" and not self.clicker_playing:
            self._changed()
            raise ControlError("group_playing", "stop the group's playback first")
        url = self._host.url_for(self.id)
        self._control.start_clicker(self.group_id, url)
        self.clicker_url = url
        self.clicker_playing, self.group_state = True, "playing"
        self.clicker_starts += 1
        if self.mode != "manual" and self.state in ("measuring", "verifying") and self.measurements:
            # phases from two playbacks cannot be compared: the round starts again
            self.measurements = {}
            self._generation += 1
        self.touch()
        self._changed()

    @_locked
    def set_volume(self, member: str, level: int) -> None:
        self._require_active()
        self._member(member)
        level = max(0, min(100, int(level)))
        self._control.set_volume(member, level)
        self.volumes = {**self.volumes, member: level}
        self.touch()
        self._changed()

    def _user_muted(self, mid: str) -> bool:
        return bool(self.snapshot and self.snapshot.muted.get(mid))

    def _unmute(self, mids: list[str]) -> None:
        for mid in mids:
            self._control.mute(mid, False)
            self.muted.discard(mid)

    @_locked
    def solo(self, member: str | None) -> None:
        """Only *member* audible: every other member muted (a member muted before the session is left
        as it is). ``None`` unmutes every member the session muted. A member whose player cannot be
        muted fails the solo with ``sync_mute_unsupported`` naming it; the mutes this call sent are
        taken back first."""
        self._require_active()
        if member is None:
            self._unmute(sorted(self.muted))
            self.soloed = None
            self.touch()
            self._changed()
            return
        self._member(member)
        self._require_clicker()
        wanted = [
            m.ma_player_id
            for m in self.members
            if m.ma_player_id != member and m.ma_player_id not in self.muted and not self._user_muted(m.ma_player_id)
        ]
        self.muted.update(wanted)
        self._changed()  # persisted before any mute is sent
        sent: list[str] = []
        try:
            for mid in wanted:
                self._control.mute(mid, True)
                sent.append(mid)
        except ControlError:
            self.muted.difference_update(m for m in wanted if m not in sent)
            try:
                self._unmute(sent)
            except ControlError as exc:  # left in ``muted``: the end unmutes them
                logger.warning("Taking back the mutes of sync session %s failed: %s", self.id, exc)
            self._changed()
            raise
        if member in self.muted:
            self._unmute([member])
        self.soloed = member
        self.touch()
        self._changed()

    @_locked
    def tick(self) -> None:
        """Called about once a second: read the group every PLAYBACK_POLL_S and report changes (a
        group stopped or started from Music Assistant, a click track that played to its end)."""
        if self.state not in _ACTIVE:
            return
        now = self._clock()
        if self._polled_at is not None and now - self._polled_at < PLAYBACK_POLL_S:
            return
        self._polled_at = now
        try:
            changed = self._observe(self._control.playback(self.group_id))
        except ControlError as exc:
            logger.debug("Reading the group of sync session %s failed: %s", self.id, exc)
            return
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
            self._apply_membership(current)
            self._member(recording.member)
            self._require_clicker()
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
            # a plain window from the apply; the check round measures with the clicker (started
            # again by the operator if it stopped meanwhile)
            self.undo_until = self._clock() + UNDO_WINDOW_S
            self.verify()
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
        """One restore attempt; True once the group is restored: every member this session muted is
        unmuted, then the group is stopped if this session's clicker is what it plays (music someone
        started meanwhile plays on). A failure keeps what is left for the next attempt."""
        self._require("restoring")
        try:
            self._unmute(sorted(self.muted))
            self.soloed = None
            if self.clicker_url is not None:
                try:
                    playing = self._control.playback(self.group_id).plays(self.clicker_url)
                except ControlError as exc:
                    if exc.code != "member_gone":  # the group is gone: nothing plays on it
                        raise
                    playing = False
                if playing:
                    self._control.stop_group(self.group_id)
                self.clicker_playing = False
        except ControlError as exc:
            self.restore_error = exc.code
            self._changed()
            return False
        self.restore_error = None
        self._host.release(self.id)
        self._set(self._final)
        return True

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
            "group_state": self.group_state,
            "clicker_playing": self.clicker_playing,
            "clicker_starts": self.clicker_starts,
            "volumes": dict(self.volumes),
            "muted": sorted(self.muted),
            "soloed": self.soloed,
            "reference": self.reference,
            "round": self.round,
            "undo_until_s": None if self.undo_until is None else max(0.0, self.undo_until - self._clock()),
        }
