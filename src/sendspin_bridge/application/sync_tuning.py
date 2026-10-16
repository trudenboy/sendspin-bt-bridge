"""Group sync tuning use cases behind ``/api/v1/sync/*``.

Everything here is synchronous and blocks on Music Assistant I/O (sessions hold their
lock across it), so it must run on a worker thread — FastAPI runs the sync ``def``
routes in its threadpool — and never on the event loop.

``ControlError`` / ``MaApiError`` codes map to a stable HTTP status (``_STATUS``) and
become the problem's ``code``.
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NoReturn

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.models.sync import (
    CaptureIn,
    CaptureLinkOut,
    CaptureMemberOut,
    CaptureResultOut,
    CaptureStateOut,
    MeasurementOut,
    PendingRestoreOut,
    ProfileApplyOut,
    ProfileCreate,
    ProfileOut,
    ProfilePatch,
    ProposalOut,
    SessionCreate,
    SyncGroup,
    SyncMember,
    SyncSessionOut,
)
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_groups
from sendspin_bridge.sync_tuning import capture_tokens
from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_bridge.sync_tuning.profiles import ConfigProfileRepository
from sendspin_bridge.sync_tuning.session_store import get_store, run_in_background
from sendspin_sync import estimator
from sendspin_sync import sequence as seq
from sendspin_sync.models import Profile, Recording, to_dict
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from sendspin_bridge.application.models.sync import Sound
    from sendspin_sync.models import Member
    from sendspin_sync.ports import GroupControl
    from sendspin_sync.session import SyncSession

_STATUS = {
    "ma_unavailable": 503,
    "ma_forbidden": 403,
    "session_active": 409,
    "session_expired": 404,
    "member_gone": 409,
    "not_adjustable": 409,
    "recording_invalid": 400,
    "restore_pending": 409,
    "session_state": 409,
    "no_measurement": 409,
    "group_playing": 409,
    "clicker_stopped": 409,
    "sync_mute_unsupported": 409,
    "no_sequence": 409,
    "sequence_running": 409,
    "upload_running": 409,
    "disk_full": 507,
    "track_unavailable": 503,
    "not_found": 404,
}

#: Largest recording body accepted: 20 s of 48 kHz int16 mono is 1.92 MB.
MAX_RECORDING_BYTES = 2_000_000
#: Largest phone upload: a few minutes of a phone recorder's AAC, or of WAV at 16 kHz.
MAX_CAPTURE_BYTES = 20_000_000

#: The phone capture's sequence waits with this (tests swap it for a clock that does not wait).
_sequence_sleep = time.sleep

logger = logging.getLogger(__name__)
#: One phone upload per session at a time, and one decode in the whole bridge (each costs tens of
#: MB and seconds of CPU; a forwarded link must not take the bridge down). Busy: 409 upload_running.
_upload_locks: dict[str, threading.Lock] = {}
_upload_locks_lock = threading.Lock()
_decode_gate = threading.BoundedSemaphore(1)


def _raise(exc: ControlError | MaApiError, *, extra: dict[str, Any] | None = None) -> NoReturn:
    """The problem for *exc*; a ``ControlError`` about one member names it in ``member``."""
    member = getattr(exc, "member", None)
    if member is not None:
        extra = {**(extra or {}), "member": member}
    raise UseCaseError(_STATUS.get(exc.code, 502), exc.code, exc.detail or exc.code, extra=extra) from exc


def _invalid_recording(detail: str) -> NoReturn:
    raise UseCaseError(400, "recording_invalid", detail)


def _not_found(detail: str) -> NoReturn:
    raise UseCaseError(404, "not_found", detail)


# -- group control without a session ---------------------------------------------------


def _control() -> GroupControl:
    """A control over the shared, long-lived Music Assistant connection; patched in tests."""
    from sendspin_bridge.sync_tuning.ma_group_control import shared_group_control

    return shared_group_control()


@contextlib.contextmanager
def _ephemeral_control() -> Iterator[GroupControl]:
    """A control for one request outside a session; errors become problems. Closing it at the
    end leaves the shared connection open."""
    control: GroupControl | None = None
    try:
        control = _control()
        yield control
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    finally:
        close = getattr(control, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def _group_row(group_id: str) -> dict[str, Any]:
    for g in get_ma_groups():
        if str(g.get("id")) == group_id:
            return g
    _not_found(f"No Music Assistant sync group {group_id}")


def _members_out(members: list[dict[str, Any]]) -> list[SyncMember]:
    return [SyncMember.model_validate(m) for m in members]


def _pending_out(p: Mapping[str, Any] | None) -> PendingRestoreOut | None:
    if p is None:
        return None
    return PendingRestoreOut(session_id=str(p["session_id"]), group_id=str(p["group_id"]), error=p.get("error"))


def _group_out(row: Mapping[str, Any], control: GroupControl, *, tolerant: bool = False) -> SyncGroup:
    """One group; with ``tolerant`` a group whose members cannot be read carries ``error`` instead
    of failing the whole list."""
    gid = str(row.get("id"))
    store = get_store()
    active = store.active_for(gid)
    error: str | None = None
    members: list[dict[str, Any]] = []
    if active is not None:
        members = active.to_dict()["members"]
    else:
        try:
            members = [to_dict(m) for m in control.members(gid)]
        except (ControlError, MaApiError) as exc:
            if not tolerant:
                raise
            error = exc.code
    measured = store.measured_at(gid)
    members = [
        {**m, "last_measured_at": measured.get(str(m["ma_player_id"]), m.get("last_measured_at"))} for m in members
    ]
    return SyncGroup(
        id=gid,
        name=str(row.get("name") or gid),
        members=_members_out(members),
        session_id=active.id if active else None,
        restore_pending=_pending_out(store.pending_for(gid)),
        error=error,
    )


def list_groups() -> list[SyncGroup]:
    rows = get_ma_groups()
    if not rows:
        return []
    with _ephemeral_control() as control:
        return [_group_out(row, control, tolerant=True) for row in rows]


def get_group(group_id: str) -> SyncGroup:
    row = _group_row(group_id)
    with _ephemeral_control() as control:
        return _group_out(row, control)


def _refuse_while_tuned(group_id: str) -> None:
    if get_store().active_for(group_id) is not None:
        raise UseCaseError(409, "session_active", "Tuning in progress for this group")


def _writable(members: list[Member], wanted: Mapping[str, int]) -> list[tuple[Member, int]]:
    """Check every target before writing any, so a refusal leaves nothing half-written."""
    by_id = {m.ma_player_id: m for m in members}
    out: list[tuple[Member, int]] = []
    for mid, ms in wanted.items():
        member = by_id.get(mid)
        if member is None:
            raise UseCaseError(409, "member_gone", f"{mid} is not in the group")
        if not member.adjustable:
            raise UseCaseError(409, "not_adjustable", f"{member.name} has no adjustable delay")
        out.append((member, ms))
    return out


def write_delays(group_id: str, delays: Mapping[str, int]) -> SyncGroup:
    """Manual write without a session (refused while a session tunes the group)."""
    row = _group_row(group_id)
    _refuse_while_tuned(group_id)
    with _ephemeral_control() as control:
        applied: dict[str, int] = {}
        for member, ms in _writable(control.members(group_id), delays):
            _write_or_report(control, member, ms, applied)
        return _group_out(row, control)


def _write_or_report(control: GroupControl, member: Member, ms: int, applied: dict[str, int]) -> None:
    """Write one delay; a failure names in the problem's ``applied`` what was already written."""
    try:
        control.set_delay(member, ms)
    except (ControlError, MaApiError) as exc:
        _raise(exc, extra={"applied": dict(applied)})
    applied[member.ma_player_id] = ms


def list_pending_restores() -> list[PendingRestoreOut]:
    return [p for p in map(_pending_out, get_store().pending()) if p is not None]


def retry_restore(group_id: str) -> PendingRestoreOut:
    store = get_store()
    pending = store.pending_for(group_id)
    if pending is None or not store.retry_pending(group_id):
        _not_found("Nothing to restore for this group")
    out = _pending_out(pending)
    assert out is not None
    return out


# -- sessions ----------------------------------------------------------------------------


def _session(sid: str) -> SyncSession:
    try:
        return get_store().get(sid)
    except ControlError as exc:
        _raise(exc)


def _session_out(session: SyncSession) -> SyncSessionOut:
    return SyncSessionOut.model_validate(session.to_dict())


def _act(sid: str, action: Callable[[SyncSession], object]) -> SyncSessionOut:
    session = _session(sid)
    try:
        action(session)
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    return _session_out(session)


def create_session(body: SessionCreate) -> SyncSessionOut:
    try:
        session = get_store().create(body.group_id, body.mode, body.point)
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    return _session_out(session)


def get_session(sid: str) -> SyncSessionOut:
    return _session_out(_session(sid))


def end_session(sid: str) -> None:
    """Cancel (a session already restoring or finished keeps its outcome); the store restores the group."""
    try:
        get_store().end(sid)
    except ControlError as exc:
        _raise(exc)


def signal(sid: str, action: str) -> SyncSessionOut:
    """``start``: play the clicker on the group (refused while it plays anything else); ``stop``: stop
    the group's playback (the preparation's "Stop group"); ``stop_clicker``: stop the group only while
    it plays this session's click track."""
    actions = {"start": lambda s: s.start_clicker(), "stop": lambda s: s.stop_group()}
    return _act(sid, actions.get(action, lambda s: s.stop_clicker()))


def session_sound(sid: str, sound: Sound) -> SyncSessionOut:
    """Manual tuning's listening sound; while the clicker plays it starts over with the new sound."""
    return _act(sid, lambda s: s.set_sound(sound))


def session_volume(sid: str, member: str, level: int) -> SyncSessionOut:
    """Set one member's volume (the clicks must be clearly audible where the operator measures)."""
    return _act(sid, lambda s: s.set_volume(member, level))


def solo(sid: str, member: str | None) -> SyncSessionOut:
    return _act(sid, lambda s: s.solo(member))


def ab(sid: str, a: str, b: str) -> SyncSessionOut:
    return _act(sid, lambda s: s.ab(a, b))


def set_reference(sid: str, member: str) -> SyncSessionOut:
    return _act(sid, lambda s: s.set_reference(member))


def skip(sid: str, member: str) -> SyncSessionOut:
    return _act(sid, lambda s: s.skip(member))


def session_delay(sid: str, member: str, ms: int) -> SyncSessionOut:
    """Manual mode: write one member's delay live."""
    return _act(sid, lambda s: s.write_delay(member, ms))


def apply(sid: str, rows: Mapping[str, int] | None) -> SyncSessionOut:
    """Write the proposal (or ``rows``); the session goes on as the manual editor ("tuning"), whose
    playing clicks give way to its listening sound at once (best effort: the delays are written)."""

    def act(s: SyncSession) -> None:
        s.apply(rows)
        try:
            s.follow_sound()
        except ControlError as exc:
            logger.warning("Sync session %s: switching to the listening sound after the apply failed: %s", sid, exc)

    return _act(sid, act)


def reset(sid: str) -> SyncSessionOut:
    """Put back every delay this session wrote (applied or edited) to the value it had at the start."""
    return _act(sid, lambda s: s.reset())


def finish(sid: str) -> SyncSessionOut:
    """The operator is done: the delays stay, the group is restored and the session ends "done"."""
    return _act(sid, lambda s: s.finish())


def proposal(sid: str) -> ProposalOut:
    """Read-only: the session's current proposal (``no_measurement`` before one was computed)."""
    current = _session(sid).proposal
    if current is None:
        raise UseCaseError(409, "no_measurement", "No proposal yet: compute one with POST")
    return ProposalOut.model_validate(to_dict(current))


def compute_proposal(sid: str) -> ProposalOut:
    """Plan from the measurements so far. A write: it moves the session on (proposed)."""
    session = _session(sid)
    try:
        result = session.propose()
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    return ProposalOut.model_validate(to_dict(result))


def upload_recording(sid: str, member: str, start_time_s: float, sample_rate: int, body: bytes) -> MeasurementOut:
    if not body or len(body) > MAX_RECORDING_BYTES or len(body) % 2:
        _invalid_recording("A recording is non-empty int16 PCM of at most 2 MB")
    if not math.isfinite(start_time_s):
        _invalid_recording("start_time_s must be a finite number")
    if not estimator.MIN_SAMPLE_RATE <= sample_rate <= estimator.MAX_SAMPLE_RATE:
        _invalid_recording(
            f"Sample rate must be {estimator.MIN_SAMPLE_RATE}-{estimator.MAX_SAMPLE_RATE} Hz, got {sample_rate}"
        )
    session = _session(sid)
    try:
        m = session.record(
            Recording(member=member, start_time_s=float(start_time_s), sample_rate=int(sample_rate), pcm=body)
        )
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    if m.reason == "invalid_recording":
        _invalid_recording(f"A recording lasts {estimator.MIN_DURATION_S:g}-{estimator.MAX_DURATION_S:g} s")
    if m.status == "ok":
        get_store().mark_measured(session.group_id, m.member, _now())
    return MeasurementOut.model_validate(to_dict(m))


# -- the phone capture page ----------------------------------------------------------------


def _capture_base_url() -> str:
    """The bridge as a phone on the LAN reaches it (the address Music Assistant fetches the clicks from)."""
    from sendspin_bridge.sync_tuning.click_track_host import default_base_url

    return default_base_url()


def capture_link(sid: str, lang: str | None) -> CaptureLinkOut:
    """A link (and its token) to the phone capture page of a measuring session; a new link revokes
    the session's previous one."""
    session = _session(sid)
    if session.mode != "semi" or session.state not in ("measuring", "proposed"):
        raise UseCaseError(409, "session_state", "A capture link is only for a running measurement")
    tokens = capture_tokens.current()
    token = tokens.issue(session.id)
    query = f"?lang={lang}" if lang in ("en", "ru") else ""
    base = _capture_base_url().rstrip("/")
    http_url = f"{base}/sync/capture/{token}{query}"
    https = capture_https_base()
    url = f"{https}/sync/capture/{token}{query}" if https else http_url
    return CaptureLinkOut(
        url=url, http_url=http_url, secure=bool(https), token=token, expires_in_s=tokens.expires_in(session.id) or 0.0
    )


def capture_https_base() -> str | None:
    """https://<the bridge's LAN address>:<capture port> while the HTTPS capture server runs."""
    from sendspin_bridge.sync_tuning.capture_https import active_port

    port = active_port()
    if port is None:
        return None
    host = _capture_base_url().split("//", 1)[-1].rsplit(":", 1)[0]
    return f"https://{host}:{port}"


def start_sequence(sid: str, members: list[str]) -> SyncSessionOut:
    """Play the sequence for the phone (every member alone in turn), on a worker thread."""
    session = _session(sid)
    try:
        session.start_sequence(members)
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    run_in_background(lambda: session.run_sequence(sleep=_sequence_sleep), name=f"sync-sequence-{sid[:8]}")
    return _session_out(session)


def _capture_session(token: str) -> SyncSession:
    """The live measuring session a capture token stands for; 404 for anything else (unknown, expired,
    revoked, or a session that ended)."""
    sid = capture_tokens.current().session_for(token) if 20 <= len(token) <= 128 else None
    if sid is None:
        _not_found("This capture link is not valid any more")
    try:
        session = get_store().get(sid)
    except ControlError:
        _not_found("This capture link is not valid any more")
    if session.state in ("restoring", "done", "cancelled", "failed"):
        _not_found("This capture link is not valid any more")
    return session


def short_name(name: str) -> str:
    """A speaker's name without the bridge suffix and trailing technical ids (as the UI shows it)."""
    import re

    out = name.strip()
    at = out.rfind(" @ ")
    if at > 0:
        out = out[:at]
    while True:
        nxt = re.sub(r"\s*\[[^\]\s]*\]$", "", out)
        nxt = re.sub(r"\s*\([^()\s]*[\d_.][^()\s]*\)$", "", nxt).strip()
        if nxt == out or not nxt:
            break
        out = nxt
    return out or name


def capture_state(token: str) -> CaptureStateOut:
    session = _capture_session(token)
    data = session.to_dict()
    names = {m["ma_player_id"]: short_name(str(m["name"])) for m in data["members"]}
    sequence = data["sequence"]
    if sequence is not None and sequence.get("member"):
        sequence = {**sequence, "member": names.get(sequence["member"], sequence["member"])}
    if sequence is not None:
        sequence = {**sequence, "members": [names.get(m, m) for m in sequence["members"]]}
    return CaptureStateOut(
        group=_group_name(session.group_id),
        point=session.point,
        measuring=session.state in ("measuring", "proposed"),
        clicks_playing=bool(data["clicker_playing"]),
        members=[
            CaptureMemberOut(
                name=names[m["ma_player_id"]],
                status=(data["measurements"].get(m["ma_player_id"]) or {}).get("status"),
            )
            for m in data["members"]
            if m["adjustable"]
        ],
        sequence=sequence,
    )


def _group_name(group_id: str) -> str:
    for g in get_ma_groups():
        if str(g.get("id")) == group_id:
            return str(g.get("name") or group_id)
    return group_id


def _refuse(status: int, code: str, detail: str) -> NoReturn:
    """A refused phone request: logged at INFO with its reason (never the token), then the problem."""
    logger.info("Sync capture request refused (%s): %s", code, detail)
    raise UseCaseError(status, code, detail)


def _measuring(session: SyncSession) -> None:
    if session.state not in ("measuring", "proposed"):
        _refuse(409, "session_state", "The measurement is not taking recordings now")


def check_capture_token(token: str) -> None:
    """404 unless *token* stands for a live session (the router calls it before reading a body)."""
    _capture_session(token)


def capture_start(token: str, mode: str) -> CaptureStateOut:
    """The phone starts the sequence itself (its only write besides the upload): every adjustable
    member still in the group and not skipped plays alone in turn; a longer lead-in when the phone's
    own recorder is started by hand. Needs a measuring session whose clicks play."""
    session = _capture_session(token)
    _measuring(session)
    data = session.to_dict()
    skipped = {mid for mid, m in data["measurements"].items() if m.get("status") == "skipped"}
    members = [m["ma_player_id"] for m in data["members"] if m["adjustable"] and m["ma_player_id"] not in skipped]
    lead = seq.RECORDER_LEAD_S if mode == "recorder" else seq.LEAD_S
    try:
        session.start_sequence(members, lead_s=lead)
    except ControlError as exc:
        logger.info("Sync capture start refused (%s): %s", exc.code, exc.detail)
        _raise(exc)
    run_in_background(lambda: session.run_sequence(sleep=_sequence_sleep), name=f"sync-sequence-{session.id[:8]}")
    return capture_state(token)


def capture_upload(token: str, body: bytes) -> CaptureResultOut:
    """Decode the phone's recording of the last sequence (an audio file in a known container) and
    measure every member of it. One upload per session and one decode in the bridge at a time."""
    session = _capture_session(token)
    _measuring(session)
    with _upload_locks_lock:
        lock = _upload_locks.setdefault(session.id, threading.Lock())
    if not lock.acquire(blocking=False):
        _refuse(409, "upload_running", "A recording is being measured; wait a moment")
    try:
        if not _decode_gate.acquire(blocking=False):
            _refuse(409, "upload_running", "Another recording is being measured; try again in a moment")
        try:
            return _capture_upload(session, body)
        finally:
            _decode_gate.release()
    finally:
        lock.release()


def _capture_upload(session: SyncSession, body: bytes) -> CaptureResultOut:
    from sendspin_bridge.sync_tuning import audio_decode

    if not body or len(body) > MAX_CAPTURE_BYTES:
        _refuse(
            400,
            "recording_invalid",
            f"A recording is a non-empty audio file of at most {MAX_CAPTURE_BYTES // 1_000_000} MB",
        )
    current = session.sequence
    if current is None:
        _refuse(409, "no_sequence", "No sequence has played yet: press Start on the phone (or Play the sequence)")
    if current["phase"] != "done":
        _refuse(409, "sequence_running", "Wait until the sequence has finished")
    try:
        pcm, rate = audio_decode.decode_to_mono(
            body, max_seconds=seq.max_duration_s(len(current["members"]), session.point)
        )
        results = session.record_sequence(pcm, rate)
    except (ControlError, MaApiError) as exc:
        logger.info("Sync capture upload refused (%s): %s", exc.code, exc.detail)
        _raise(exc)
    for m in results.values():
        if m.status == "ok":
            get_store().mark_measured(session.group_id, m.member, _now())
    return CaptureResultOut(results=[MeasurementOut.model_validate(to_dict(m)) for m in results.values()])


# -- profiles ----------------------------------------------------------------------------


def _repo() -> ConfigProfileRepository:
    return ConfigProfileRepository()


def _profile(pid: str) -> Profile:
    p = _repo().get(pid)
    if p is None:
        _not_found("No such profile")
    return p


def _profile_out(p: Profile) -> ProfileOut:
    return ProfileOut.model_validate(to_dict(p))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def list_profiles(group_id: str) -> list[ProfileOut]:
    return [_profile_out(p) for p in _repo().list(group_id)]


def create_profile(body: ProfileCreate) -> ProfileOut:
    p = Profile(
        id=uuid.uuid4().hex,
        name=body.name,
        group_id=body.group_id,
        kind=body.kind,
        delays=dict(body.delays),
        created_at=_now(),
        notes=body.notes,
    )
    _repo().save(p)
    return _profile_out(p)


def patch_profile(pid: str, body: ProfilePatch) -> ProfileOut:
    p = _profile(pid)
    if body.name is not None:
        p.name = body.name
    if body.notes is not None:
        p.notes = body.notes
    _repo().save(p)
    return _profile_out(p)


def delete_profile(pid: str) -> None:
    _profile(pid)
    _repo().delete(pid)


def apply_profile(pid: str) -> ProfileApplyOut:
    """Write the profile's delays; members gone from the group or no longer adjustable are skipped."""
    p = _profile(pid)
    _refuse_while_tuned(p.group_id)
    applied: dict[str, int] = {}
    skipped: list[str] = []
    with _ephemeral_control() as control:
        members = {m.ma_player_id: m for m in control.members(p.group_id)}
        for mid, ms in p.delays.items():
            member = members.get(mid)
            if member is None or not member.adjustable:
                skipped.append(mid)
                continue
            _write_or_report(control, member, ms, applied)
    return ProfileApplyOut(applied=applied, skipped=skipped)


def capture_profile(group_id: str, body: CaptureIn) -> ProfileOut:
    """A profile of the delays the adjustable members have right now."""
    _group_row(group_id)
    with _ephemeral_control() as control:
        adjustable = [m for m in control.members(group_id) if m.adjustable]
        current = control.delays(adjustable)
    delays = {mid: max(0, min(5000, ms)) for mid, ms in current.items() if ms is not None}
    return create_profile(
        ProfileCreate(group_id=group_id, name=body.name, kind=body.kind, delays=delays, notes=body.notes)
    )
