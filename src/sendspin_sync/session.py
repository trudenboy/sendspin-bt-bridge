"""One tuning session: the state machine the spec's "Session" section describes."""

from __future__ import annotations

import dataclasses
import functools
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Literal

from sendspin_sync import click_track, estimator, planner
from sendspin_sync import sequence as seq
from sendspin_sync.models import Measurement, Member, to_dict
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Concatenate

    from sendspin_sync.models import GroupPlayback, Proposal, Recording, Snapshot
    from sendspin_sync.ports import ClickTrackHost, GroupControl

logger = logging.getLogger(__name__)

Mode = Literal["manual", "semi"]
Point = Literal["device", "position"]
State = Literal[
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
INACTIVITY_S = 900.0
MIN_DELAY_MS = 0
MAX_DELAY_MS = planner.MAX_DELAY_MS
# The signal path (option C, user decision 2026-10-16): no music plays during a session. The operator
# stops the group, starts the clicker (the click track queued on the group like any URL) and sets the
# volumes; solo mutes the other members. The tick reads the group's playback this often, so the page
# follows a group stopped or started from Music Assistant and a click track that played to its end.
PLAYBACK_POLL_S = 2.0
# A member Music Assistant does not list (its player has not registered again after a restart) keeps
# its unmute owed this long after the restore began; then the restore gives it up and finishes.
RESTORE_GIVE_UP_S = 600.0
_ENDED: tuple[State, ...] = ("done", "cancelled", "failed")
_ACTIVE: tuple[State, ...] = ("measuring", "proposed", "applying", "tuning")
# Recordings, skips and proposals: while measuring, and after a proposal (a member measured again
# before the operator applies). Once applied, the session is the manual editor (state "tuning").
_MEASURING: tuple[State, ...] = ("measuring", "proposed")


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

    ``muted`` holds the members this session muted (and only those: a member that is muted when a
    solo needs it silent — by the operator, before or during the session — is never touched). A
    member is added before its mute is sent and stays tracked when the mute fails (it may have taken
    effect), unless its player cannot be muted at all. The end unmutes every one of them that is still
    muted (one the operator unmuted meanwhile gets no extra command), then stops the group if the
    session's clicker is what it plays. The clicker's URL is recorded before ``play_media`` is sent,
    so a start that times out but plays is still stopped at the end.
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
        # each member's volume before this session first changed it; put back at the end
        self.volumes_before: dict[str, int] = {}
        self._clicker_started: float | None = None  # clock at the last clicker start
        # the phone capture's sequence (every member alone in turn, recorded once); None before one
        self.sequence: dict[str, Any] | None = None
        self._seq_slots: list[seq.Slot] = []
        self._seq_clicks = 0  # clicker starts when the sequence began: its recording needs that playback
        self._live_muted: dict[str, bool] = {}  # mutes as last read from Music Assistant
        self._restore_since: float | None = None
        self._polled_at: float | None = None
        self.reference: str | None = None
        self._final: State = "done"
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
        # manual tuning edits by ear from the start; semi-automatic measures first, then edits the same way
        self._set("tuning" if self.mode == "manual" else "measuring")

    # -- the group and the clicker ---------------------------------------------------
    def _observe(self, playback: GroupPlayback) -> bool:
        """Take one reading of the group; True when anything the page shows changed."""
        playing = playback.plays(self.clicker_url)
        volumes = dict(playback.volumes)
        changed = (playback.state, playing, volumes) != (self.group_state, self.clicker_playing, self.volumes)
        self.group_state, self.clicker_playing, self.volumes = playback.state, playing, volumes
        self._live_muted = dict(playback.muted)
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
        if url != self.clicker_url:
            self.clicker_url = url
            self._changed()  # persisted before play_media: a start that times out may still play
        self._control.start_clicker(self.group_id, url)
        self.clicker_playing, self.group_state = True, "playing"
        self.clicker_starts += 1
        self._clicker_started = self._clock()
        if self.state in _MEASURING and self.measurements:
            # phases from two playbacks cannot be compared: the round starts again
            self.measurements = {}
            self._generation += 1
        self.touch()
        self._changed()

    @_locked
    def stop_clicker(self) -> None:
        """Stop the group, but only while it plays this session's click track (never someone's music)."""
        self._require_active()
        if self._observe(self._control.playback(self.group_id)):
            self._changed()
        if self.clicker_playing:
            self._control.stop_group(self.group_id)
            self.group_state, self.clicker_playing = "idle", False
        self.touch()
        self._changed()

    @_locked
    def set_volume(self, member: str, level: int) -> None:
        """Set one member's volume. The first change of a member remembers (and persists, before the
        command is sent) the volume it had, so the end puts it back."""
        self._require_active()
        self._member(member)
        level = max(0, min(100, int(level)))
        before = self.volumes.get(member)
        if member not in self.volumes_before and before is not None:
            self.volumes_before[member] = before
            self._changed()
        self._control.set_volume(member, level)
        self.volumes = {**self.volumes, member: level}
        self.touch()
        self._changed()

    def _user_muted(self, mid: str) -> bool:
        """Muted by someone else: before the session (snapshot) or now (last reading), and not by us."""
        if mid in self.muted:
            return False
        return bool((self.snapshot and self.snapshot.muted.get(mid)) or self._live_muted.get(mid))

    def _owed(self, exc: ControlError, *, give_up_absent: bool) -> bool:
        """Whether a failed restore step stays owed (raise) or is given up: only a player Music Assistant
        does not list, once the restore ran for RESTORE_GIVE_UP_S."""
        since = self._restore_since
        if not (give_up_absent and exc.code == "member_gone" and since is not None):
            return True
        return self._clock() - since < RESTORE_GIVE_UP_S

    def _put_volumes_back(self) -> None:
        """Every volume this session changed goes back to what it was (sent while our clicks may still
        play: some players keep a volume set while they play). A failure stays owed, as for unmutes."""
        for mid, level in sorted(self.volumes_before.items()):
            try:
                self._control.set_volume(mid, level)
            except ControlError as exc:
                if self._owed(exc, give_up_absent=True):
                    raise
                logger.warning(
                    "Sync session %s gives up the volume of %s: Music Assistant does not list it", self.id, mid
                )
            del self.volumes_before[mid]
            self.volumes = {**self.volumes, mid: level}
            self._changed()

    def _unmute(self, mids: list[str], *, give_up_absent: bool = False) -> None:
        """Unmute each member (the adapter sends it only to a player that is muted now). With
        *give_up_absent*, a player Music Assistant no longer lists is dropped once the restore ran for
        RESTORE_GIVE_UP_S; before that, and otherwise, its error is raised and it stays owed."""
        for mid in mids:
            try:
                self._control.mute(mid, False)
            except ControlError as exc:
                if self._owed(exc, give_up_absent=give_up_absent):
                    raise
                logger.warning("Sync session %s gives up unmuting %s: Music Assistant does not list it", self.id, mid)
            self.muted.discard(mid)
            self._live_muted[mid] = False  # our own unmute: never mistaken for someone else's mute

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
        self._silence(member)
        self.touch()
        self._changed()

    def _silence(self, keep: str | None) -> None:
        """Mute every member but *keep* (``None``: every member), then unmute *keep* if this session
        muted it. Members muted by someone else are left alone. On a failure the mutes this call sent
        are taken back and the error raised (``sync_mute_unsupported`` names the member)."""
        wanted = [
            m.ma_player_id
            for m in self.members
            if m.ma_player_id != keep and m.ma_player_id not in self.muted and not self._user_muted(m.ma_player_id)
        ]
        self.muted.update(wanted)
        self._changed()  # persisted before any mute is sent
        sent: list[str] = []
        try:
            for mid in wanted:
                self._control.mute(mid, True)
                sent.append(mid)
        except ControlError as exc:
            # the failing member's mute may have taken effect (a late answer): it stays owed, unless its
            # player cannot be muted at all
            failed = wanted[len(sent)]
            not_sent = wanted[len(sent) + 1 :]
            if exc.code == "sync_mute_unsupported":
                not_sent = [failed, *not_sent]
            else:
                sent.append(failed)
            self.muted.difference_update(not_sent)
            try:
                self._unmute(sent)
            except ControlError as undo_exc:  # left in ``muted``: the end unmutes them
                logger.warning("Taking back the mutes of sync session %s failed: %s", self.id, undo_exc)
            self._changed()
            raise
        if keep is not None and keep in self.muted:
            self._unmute([keep])
        self.soloed = keep

    # -- one recording for every member (the phone capture page) ---------------------------------
    @_locked
    def start_sequence(self, members: list[str]) -> None:
        """Get a sequence ready: *members* will play alone in turn (``run_sequence`` plays it) while a
        phone records once. Needs the clicks; refused while another sequence runs."""
        self._require(*_MEASURING)
        if self.sequence is not None and self.sequence["phase"] in ("starting", "lead", "solo", "gap"):
            raise ControlError("sequence_running", "a sequence is playing")
        if not members:
            raise ControlError("member_gone", "no member to measure")
        for mid in members:
            m = self._member(mid)
            if not m.adjustable:
                raise ControlError("not_adjustable", m.name, member=mid)
        self._require_clicker()
        self._seq_slots = []
        self._seq_clicks = self.clicker_starts
        self.sequence = {
            "members": list(dict.fromkeys(members)),
            "phase": "starting",
            "index": -1,
            "member": None,
            "error": None,
            "error_member": None,
            "solo_s": seq.solo_seconds(self.point),
            "gap_s": seq.gap_seconds(self.point),
            "lead_s": seq.LEAD_S,
            "uploaded": False,
        }
        self.touch()
        self._changed()

    def _sequence_alive(self, current: dict[str, Any]) -> bool:
        return self.sequence is current and self.state in _MEASURING and self.clicker_starts == self._seq_clicks

    def _sequence_failed(self, current: dict[str, Any], exc: ControlError) -> None:
        current.update(phase="failed", error=exc.code, error_member=exc.member, member=None)
        try:
            self._unmute(sorted(self.muted))
            self.soloed = None
        except ControlError as undo_exc:  # left in ``muted``: the end unmutes them
            logger.warning("Sync session %s: unmuting after a failed sequence failed: %s", self.id, undo_exc)
        self._changed()

    def run_sequence(self, sleep: Callable[[float], None] = time.sleep) -> None:
        """Play the sequence (blocking; run it on a worker thread): everyone muted for a lead-in, then
        each member alone for ``solo_s`` with everyone muted ``gap_s`` in between, then everyone back.
        The lock is held for each step, never across the waits. Stops quietly when the session ends or
        the clicks start over; a failure is recorded in the sequence and everyone is unmuted."""
        with self._lock:
            current = self.sequence
            if current is None or current["phase"] != "starting" or not self._sequence_alive(current):
                return
            try:
                self._silence(None)
            except ControlError as exc:
                self._sequence_failed(current, exc)
                return
            t0 = self._clock()
            current["phase"] = "lead"
            self.touch()
            self._changed()
        sleep(seq.LEAD_S)
        for i, mid in enumerate(current["members"]):
            with self._lock:
                if not self._sequence_alive(current):
                    return
                try:
                    self._member(mid)
                    self._require_clicker()
                    self._silence(mid)
                except ControlError as exc:
                    self._sequence_failed(current, exc)
                    return
                start = self._clock() - t0
                current.update(phase="solo", index=i, member=mid)
                self.touch()
                self._changed()
            sleep(current["solo_s"])
            with self._lock:
                if not self._sequence_alive(current):
                    return
                try:
                    self._silence(None)
                except ControlError as exc:
                    self._sequence_failed(current, exc)
                    return
                self._seq_slots.append(seq.Slot(member=mid, start_s=start, end_s=self._clock() - t0))
                current.update(phase="gap", member=None)
                self._changed()
            sleep(current["gap_s"])
        with self._lock:
            if not self._sequence_alive(current):
                return
            try:
                self._unmute(sorted(self.muted))
                self.soloed = None
            except ControlError as exc:  # left in ``muted``: the end unmutes them
                logger.warning("Sync session %s: unmuting after the sequence failed: %s", self.id, exc)
            current.update(phase="done", member=None)
            self.touch()
            self._changed()

    def record_sequence(self, pcm: bytes, sample_rate: int) -> dict[str, Measurement]:
        """Measure every member of the finished sequence from one recording of it (int16 mono PCM):
        the clicks are lined up against the sequence's timing and each member's stretch is estimated
        on the recording's one clock. Refused (``session_state``) when the clicks started over since."""
        current_members = self._membership()
        with self._lock:
            self._require(*_MEASURING)
            self._apply_membership(current_members)
            current = self.sequence
            if current is None:
                raise ControlError("no_sequence", "play the sequence first")
            if current["phase"] != "done":
                raise ControlError("sequence_running", "the sequence has not finished")
            if self.clicker_starts != self._seq_clicks:
                raise ControlError("session_state", "the clicks started over since the sequence")
            slots = list(self._seq_slots)
            clicks = self._seq_clicks
        events = seq.click_times(pcm, sample_rate)  # slow: outside the lock
        offset = seq.align(events, slots, len(pcm) / 2 / sample_rate)
        results: dict[str, Measurement] = {}
        if offset is None:
            for s in slots:
                results[s.member] = Measurement(member=s.member, status="not_heard", reason="no_clicks")
        else:
            for rec in seq.windows(pcm, sample_rate, slots, offset):
                results[rec.member] = estimator.estimate(rec)
        with self._lock:
            self._require(*_MEASURING)
            if self.sequence is not current or self.clicker_starts != clicks:
                raise ControlError("session_state", "the measurement changed while estimating")
            present = {m.ma_player_id for m in self.members}
            results = {mid: m for mid, m in results.items() if mid in present}
            self.measurements.update(results)
            current["uploaded"] = True
            self.touch()
            self._changed()
        return results

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
        if self.state not in _MEASURING:
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
            self._require(*_MEASURING)
            self._apply_membership(current)
            self._member(recording.member)
            self._require_clicker()
            generation = self._generation
        m = estimator.estimate(recording)  # slow: outside the lock
        with self._lock:
            self._require(*_MEASURING)
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
        self._require(*_MEASURING)
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
        self._require(*_MEASURING)
        self._apply_membership(current)
        self.touch()
        spread = planner.spread_ms(self.measurements.values())
        try:
            self.proposal = planner.plan(self.measurements.values(), self.members, reference=self.reference)
        except planner.PlanError as exc:
            raise ControlError("no_measurement", str(exc)) from exc
        self.history.append({"spread_before_ms": round(spread, 1)})
        self._set("proposed")
        return self.proposal

    # -- applying ------------------------------------------------------------------
    @_locked
    def apply(self, rows: Mapping[str, int] | None = None) -> None:
        """Write the proposal (or ``rows``); the session then goes on as the manual editor ("tuning"):
        the clicks keep playing, the operator listens (solo, A/B), adjusts by hand and finishes."""
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
        for member, _before in done:
            self._written[member.ma_player_id] = wanted[member.ma_player_id]
        self.members = [dataclasses.replace(m, delay_ms=wanted.get(m.ma_player_id, m.delay_ms)) for m in self.members]
        self.touch()
        self._set("tuning")

    def _roll_back(self, done: list[tuple[Member, int]], wanted: Mapping[str, int]) -> None:
        """Best effort: put back what a failed apply already wrote. A member whose value cannot be put
        back keeps the written value in ``members`` (and in the reset bookkeeping), so the session
        shows what Music Assistant holds."""
        for member, before in reversed(done):
            mid = member.ma_player_id
            try:
                self._control.set_delay(member, before)
            except ControlError:
                self._written[mid] = wanted[mid]
                self._set_member_delay(mid, wanted[mid])

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
        self._restore_since = self._clock()
        self._set("restoring")

    @_locked
    def finish(self) -> None:
        """The operator is done: the delays stay as they are now, the group is restored ("done")."""
        self._require(*_ACTIVE)
        self._begin_restore("done")

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

    def _forget_clicker(self) -> None:
        """Best effort: the click track does not stay in the group's queue (the user's items are kept)."""
        assert self.clicker_url is not None
        try:
            self._control.forget_clicker(self.group_id, self.clicker_url)
        except ControlError as exc:
            logger.info("Sync session %s could not take the click track out of the queue: %s", self.id, exc)

    @_locked
    def restore_tick(self) -> bool:
        """One restore attempt; True once the group is restored: the volumes this session changed are
        put back (while our clicks still play), every member it muted is unmuted, then the group is
        stopped if this session's clicker is what it plays (music someone started meanwhile plays on).
        A failure keeps what is left for the next attempt."""
        self._require("restoring")
        try:
            self._put_volumes_back()
            self._unmute(sorted(self.muted), give_up_absent=True)
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
                self._forget_clicker()
        except ControlError as exc:
            self.restore_error = exc.code
            self._changed()
            return False
        self.restore_error = None
        self._host.release(self.id)
        self._set(self._final)
        return True

    def _clicker_left(self) -> float | None:
        """Seconds of the click track left, while it plays (counted from its last start)."""
        if not self.clicker_playing or self._clicker_started is None:
            return None
        return max(0.0, round(click_track.DURATION_S - (self._clock() - self._clicker_started), 1))

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
            "volumes_before": dict(sorted(self.volumes_before.items())),
            "clicker_left_s": self._clicker_left(),
            "muted": sorted(self.muted),
            "soloed": self.soloed,
            "reference": self.reference,
            "sequence": dict(self.sequence) if self.sequence is not None else None,
        }
