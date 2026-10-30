"""Group sync tuning use cases behind ``/api/v1/sync/*``.

Everything here is synchronous and blocks on Music Assistant I/O (sessions hold their
lock across it), so it must run on a worker thread — FastAPI runs the sync ``def``
routes in its threadpool — and never on the event loop.

``ControlError`` / ``MaApiError`` codes map to a stable HTTP status (``_STATUS``) and
become the problem's ``code``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NoReturn

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.models.sync import (
    AirCalibrationCandidate,
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
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_groups, get_ma_player_ids
from sendspin_bridge.sync_tuning import capture_tokens, session_store
from sendspin_bridge.sync_tuning.bed_stream import BedStreams
from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_bridge.sync_tuning.profiles import ConfigProfileRepository
from sendspin_bridge.sync_tuning.session_store import get_store, run_in_background
from sendspin_sync import estimator
from sendspin_sync import sequence as seq
from sendspin_sync.models import Profile, Recording, to_dict
from sendspin_sync.planner import unwrap_lag_ms
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from sendspin_bridge.application.models.sync import Sound
    from sendspin_bridge.services.bluetooth.air_sensor_service import AirFoldResult, AirSensorService
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
    "not_eligible": 409,
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
    data = session.to_dict()
    return SyncSessionOut.model_validate({**data, **_air_extras(data)})


def _act(sid: str, action: Callable[[SyncSession], object]) -> SyncSessionOut:
    session = _session(sid)
    try:
        action(session)
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    return _session_out(session)


#: Keep-awake streams for speakers whose amplifier sleeps through the silence between clicks.
_BEDS = BedStreams()


def _keep_awake_sinks(group_id: str) -> list[str]:
    """The sinks of this group's members that asked to stay awake during tuning.

    Read from the configuration at session start: the flag is a property of the speaker, not of the
    session, and it is only ever consulted while a tuning session runs.
    """
    from sendspin_bridge.bridge import state as bridge_state
    from sendspin_bridge.config import load_config
    from sendspin_bridge.config.players import find_player_by_id

    try:
        members = _control().members(group_id)
    except (ControlError, MaApiError):
        return []
    clients = {str(getattr(c, "player_id", "") or ""): c for c in bridge_state.get_clients_snapshot()}
    config = load_config()
    sinks: list[str] = []
    for member in members:
        client_id = getattr(member, "client_id", None)
        if not client_id:
            continue
        entry = find_player_by_id(config, str(client_id))
        if not entry or not entry.get("calibration_bed"):
            continue
        client = clients.get(str(client_id))
        if client is None:
            continue
        manager = getattr(client, "bt_manager", None)
        sink = getattr(manager, "bluetooth_sink_name", None) if manager is not None else None
        if not sink:
            sink = (getattr(client, "status", {}) or {}).get("sink")
        if isinstance(sink, str) and sink:
            sinks.append(sink)
    return sinks


def create_session(body: SessionCreate) -> SyncSessionOut:
    try:
        session = get_store().create(body.group_id, body.mode, body.point)
    except (ControlError, MaApiError) as exc:
        _raise(exc)
    try:
        _BEDS.sync(_keep_awake_sinks(body.group_id))
    except Exception:  # a keep-awake stream must never cost the session
        logger.warning("Could not start the tuning keep-awake streams", exc_info=True)
    return _session_out(session)


def get_session(sid: str) -> SyncSessionOut:
    return _session_out(_session(sid))


def end_session(sid: str) -> None:
    """Cancel (a session already restoring or finished keeps its outcome); the store restores the group."""
    try:
        _BEDS.stop_all()
    finally:
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
    """Pick the reference (proposals and the air calibration); published, since the core does not."""
    out = _act(sid, lambda s: s.set_reference(member))
    _publish_session(sid)
    return out


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
    _start_air_watch(session)
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
    _start_air_watch(session)
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


# -- a speaker's air-to-sound latency (air sensor) -----------------------------------------
#
# While the phone's sequence plays, a watcher thread stamps each solo's start and end in RAW time.
# For a bridge member the air sensor is effective for, it arms an air click fold at the solo's start
# and, at its end, folds the air span (the solo's sound as it actually left over the air) on a worker
# thread, then asks the speaker's daemon for the written fold of the same audio. After the upload,
# ``L_as = A - H_cal``: the acoustic lag against the session's reference minus the host placement
# ``H_cal = unwrap(φ_air - φ_T)`` (both under the same output delay, which cancels). Nothing here
# runs on the bridge loop except the one stdin write of the fold request.

#: Added to the daemon's host latency (write head to port) for the air span.
AIR_SPAN_EXTRA_MS = 20.0
#: The written fold's span, ending where the air span ends (the written PCM carries every click).
WRITTEN_FOLD_SPAN_S = 12.0
#: The stored latency's range (the config's ``air_to_sound_latency`` entries).
AIR_LATENCY_RANGE_MS = (0.0, 2000.0)
_AIR_POLL_S = 0.1
_AIR_MARGIN_S = 0.3  # the air span's last frames have reached the fold this long after its end
_AIR_REPLY_S = 2.0  # the daemon's written fold answers within this
_AIR_SESSIONS_MAX = 8


@dataclass(frozen=True)
class AirMemberFold:
    """Both folds of one member's solo."""

    air: AirFoldResult | None  # None when the fold worker failed (fold_failed)
    written_phase_ms: float | None  # φ_T, None when the written fold found no clicks or failed
    written_reason: str | None  # no_written_clicks, air_delay_changed, or fold_failed (refused or unanswered request)
    delay_report_ms: float | None  # the speaker's A2DP delay report at the time (a hint)
    stream_codec: str | None = None  # the codec the speaker played then (daemon snapshot, else connection)
    written_d_out_ms: float | None = None  # the delay the written PCM went out with (the calibration's d_ref_ms)


#: The running sequence watcher per session.
_air_watches: dict[str, _AirWatch] = {}
#: Per session: (the sequence's token, {member: folds}); a new sequence starts a new token.
_air_records: dict[str, tuple[object, dict[str, AirMemberFold]]] = {}
_air_lock = threading.Lock()


def _raw_now_us() -> float:
    from sendspin_bridge.services.bluetooth.hci_clock import _raw_ns

    return _raw_ns() / 1000.0


def _air_wait_until(raw_us: float) -> None:
    """Block the calling worker thread until RAW time reaches *raw_us*."""
    while (left_us := raw_us - _raw_now_us()) > 0:
        time.sleep(min(left_us / 1e6, 0.5))


def air_span(stamps: tuple[float, float], host_latency_ms: float | None) -> tuple[float, float]:
    """The RAW µs span in which a solo stamped ``(start, end)`` was on air: the microphone's settle
    and tail cut, shifted by the host latency (read at the solo's end) plus ``AIR_SPAN_EXTRA_MS``."""
    start, end = stamps
    h = host_latency_ms if host_latency_ms is not None and math.isfinite(host_latency_ms) else 0.0
    h_us = (max(0.0, h) + AIR_SPAN_EXTRA_MS) * 1e3
    return start + seq.SETTLE_S * 1e6 + h_us, end - seq.TAIL_S * 1e6 + h_us


def _air_service() -> AirSensorService | None:
    """The running air sensor service when it can fold (switch on and started), else None."""
    from sendspin_bridge.services.bluetooth.air_sensor_service import get_air_sensor_service

    service = get_air_sensor_service()
    return service if callable(getattr(service, "arm_fold", None)) else None  # type: ignore[return-value]


def _bridge_client(member: Member | Mapping[str, Any]) -> Any | None:
    """The running bridge speaker that is this Music Assistant member, if any."""
    from sendspin_bridge.services.bluetooth import device_registry

    get = member.get if isinstance(member, dict) else lambda key: getattr(member, key, None)
    mid, client_id = get("ma_player_id"), get("client_id")
    ids = get_ma_player_ids()
    for client in device_registry.get_device_registry_snapshot().active_clients:
        pid = getattr(client, "player_id", None)
        if pid and (ids.get(pid) == mid or pid == client_id):
            return client
    return None


def _air_target(session: SyncSession, mid: str) -> tuple[Any, str] | None:
    """(client, MAC) of a bridge member whose daemon runs the air sensor; None when not effective."""
    from sendspin_bridge.services.bluetooth.air_sensor_service import air_accepts

    member = next((m for m in session.members if m.ma_player_id == mid), None)
    if member is None or member.origin != "bridge":
        return None
    client = _bridge_client(member)
    if client is None or not air_accepts(client):
        return None
    mac = getattr(getattr(client, "bt_manager", None), "mac_address", None)
    return (client, str(mac).upper()) if mac else None


def _status_number(client: Any, *path: str) -> float | None:
    value: Any = getattr(client, "status", None)
    for key in path:
        value = value.get(key) if hasattr(value, "get") else None
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok else None


def _written_fold(
    loop: asyncio.AbstractEventLoop, client: Any, to_us: float
) -> tuple[float | None, str | None, float | None]:
    """Ask the daemon for the written click phase over ``WRITTEN_FOLD_SPAN_S`` ending at *to_us* and
    wait (this worker thread) for its answer in ``air_sensor.fold``. A refused request or no answer
    in time is ``fold_failed``; an answer without clicks is ``no_written_clicks`` and one over two delays
    ``air_delay_changed``; an ok answer carries ``d_out_ms`` (None when the daemon sent none)."""
    from sendspin_bridge.services.ipc.commands import AirFold

    fold_id = uuid.uuid4().hex
    cmd = AirFold(id=fold_id, from_us=to_us - WRITTEN_FOLD_SPAN_S * 1e6, to_us=to_us)

    async def _send() -> bool:
        return bool(client.send_air_command(cmd))

    try:
        sent = asyncio.run_coroutine_threadsafe(_send(), loop).result(timeout=_AIR_REPLY_S)
    except TimeoutError:
        sent = False
    if not sent:
        return None, "fold_failed", None
    deadline = time.monotonic() + _AIR_REPLY_S
    while True:
        info = client.status.get("air_sensor")
        fold = info.get("fold") if isinstance(info, dict) else None
        if isinstance(fold, dict) and fold.get("id") == fold_id:
            phase = fold.get("phase_ms")
            if fold.get("ok") is True and isinstance(phase, (int, float)) and math.isfinite(phase):
                d_out = fold.get("d_out_ms")
                finite = isinstance(d_out, (int, float)) and math.isfinite(d_out)
                return float(phase), None, float(d_out) if finite else None  # type: ignore[arg-type]
            return None, "air_delay_changed" if fold.get("reason") == "air_delay_changed" else "no_written_clicks", None
        if time.monotonic() >= deadline:
            return None, "fold_failed", None
        time.sleep(0.05)


def _store_air_fold(sid: str, token: object, member: str, fold: AirMemberFold) -> None:
    """Keep a member's folds and publish the session, so a fold that finishes after the upload's
    event still reaches the UI."""
    with _air_lock:
        entry = _air_records.get(sid)
        if entry is None or entry[0] is not token:  # a newer sequence replaced this one: dropped
            return
        entry[1][member] = fold
    _publish_session(sid)


def _publish_session(sid: str) -> None:
    """A ``sync`` event for the session now (best effort; a session that ended publishes nothing)."""
    try:
        store = get_store()
        store.publish_change(store.get(sid))
    except Exception:
        logger.debug("Sync session %s: publishing after an air fold failed", sid, exc_info=True)


def _stream_codec(client: Any) -> str | None:
    info = client.status.get("air_sensor")
    codec = info.get("codec") if isinstance(info, dict) else None
    if not isinstance(codec, str) or not codec:
        codec = client.status.get("bt_codec_name")
    return codec.lower() if isinstance(codec, str) and codec else None


def _air_folds(sid: str) -> dict[str, AirMemberFold]:
    with _air_lock:
        entry = _air_records.get(sid)
        return dict(entry[1]) if entry is not None else {}


def _finish_member(
    sid: str,
    token: object,
    member: str,
    service: AirSensorService,
    fold_id: str,
    client: Any,
    span: tuple[float, float],
) -> None:
    """Worker thread: fold the air span once it is on air, then the written side; never the loop."""
    lo, hi = span
    hint, codec = _status_number(client, "bt_reported_delay_ms"), _stream_codec(client)
    try:
        try:
            _air_wait_until(hi + _AIR_MARGIN_S * 1e6)
        finally:
            air = service.finish_fold(fold_id, hi, from_us=lo)  # detached even when the wait failed
        phase, reason, d_out = _written_fold(service.loop, client, hi)
    except Exception:
        logger.warning("Sync session %s: the air fold of %s failed", sid, member, exc_info=True)
        _store_air_fold(sid, token, member, AirMemberFold(None, None, "fold_failed", hint, codec))
        return
    _store_air_fold(sid, token, member, AirMemberFold(air, phase, reason, hint, codec, d_out))


class _AirWatch:
    """Watches one sequence's phases (every ``_AIR_POLL_S``) and folds each effective member's solo."""

    def __init__(self, session: SyncSession, current: dict[str, Any], service: AirSensorService, token: object):
        self.session, self.current, self.service, self.token = session, current, service, token
        self.seen: tuple[str, int] | None = None
        # the member playing alone: (member, RAW start, (fold id, client) or None when not effective)
        self._solo: tuple[str, float, tuple[str, Any] | None] | None = None
        self._closed = False

    def run(self) -> None:
        deadline = time.monotonic() + seq.max_duration_s(len(self.current["members"]), self.session.point)
        try:
            while self.poll() and time.monotonic() < deadline:
                time.sleep(_AIR_POLL_S)
        except Exception:
            logger.warning("Sync session %s: the air watcher failed", self.session.id, exc_info=True)
        finally:
            self.close()

    def poll(self) -> bool:
        """One look at the sequence; False once it is over (done, failed, replaced or ended)."""
        current = self.session.sequence
        if current is not self.current:
            return False
        key = (str(current["phase"]), int(current["index"]))
        if key == self.seen:
            return True
        now = _raw_now_us()
        if self._solo is not None and (key[0] != "solo" or (self.seen is not None and key[1] != self.seen[1])):
            self._end_solo(now, played=key[0] in ("solo", "gap", "done"))
        if key[0] == "solo" and current.get("member"):
            self._start_solo(str(current["member"]), now)
        self.seen = key
        return key[0] not in ("done", "failed")

    def _start_solo(self, member: str, now: float) -> None:
        target = _air_target(self.session, member)
        armed = None
        if target is not None:
            client, mac = target
            armed = (self.service.arm_fold(mac, now + seq.SETTLE_S * 1e6), client)
        self._solo = (member, now, armed)

    def _end_solo(self, now: float, *, played: bool) -> None:
        assert self._solo is not None
        member, start, armed = self._solo
        self._solo = None
        if armed is None:
            return
        fold_id, client = armed
        if not played:
            self.service.finish_fold(fold_id, now)  # the sequence failed: detach and discard
            return
        span = air_span((start, now), _status_number(client, "playout", "host_latency_ms"))
        sid, token, service = self.session.id, self.token, self.service
        try:
            run_in_background(
                lambda: _finish_member(sid, token, member, service, fold_id, client, span),
                name=f"sync-air-fold-{sid[:8]}",
            )
        except RuntimeError:
            service.finish_fold(fold_id, now)

    def close(self) -> None:
        """Detach a fold still armed (the sequence ended mid-solo) and leave the registry."""
        if self._closed:
            return
        self._closed = True
        solo, self._solo = self._solo, None
        if solo is not None and solo[2] is not None:
            with contextlib.suppress(Exception):
                self.service.finish_fold(solo[2][0], _raw_now_us())
        with _air_lock:
            if _air_watches.get(self.session.id) is self:
                del _air_watches[self.session.id]


def _start_air_watch(session: SyncSession) -> None:
    """A new sequence: forget the last one's folds and, with the air sensor running, watch this one."""
    token = object()
    with _air_lock:
        _air_records.pop(session.id, None)
        _air_records[session.id] = (token, {})
        while len(_air_records) > _AIR_SESSIONS_MAX:
            del _air_records[next(iter(_air_records))]
    service = _air_service()
    current = session.sequence
    if service is None or current is None:
        return
    watch = _AirWatch(session, current, service, token)
    with _air_lock:
        _air_watches[session.id] = watch
    run_in_background(watch.run, name=f"sync-air-{session.id[:8]}")


def _ok_phase(measurement: Mapping[str, Any] | None) -> float | None:
    if not measurement or measurement.get("status") != "ok":
        return None
    phase = measurement.get("phase_ms")
    return float(phase) if isinstance(phase, (int, float)) and math.isfinite(phase) else None


def _air_candidate(
    mid: str, reference: str | None, measurements: Mapping[str, Any], fold: AirMemberFold | None
) -> AirCalibrationCandidate:
    air = fold.air if fold is not None else None

    def out(reason: str | None, lag: float | None = None, placement: float | None = None) -> AirCalibrationCandidate:
        latency = None if reason is not None or lag is None or placement is None else round(lag - placement, 1)
        return AirCalibrationCandidate.model_validate(
            {
                "member": mid,
                "eligible": reason is None,
                "reason": reason,
                "latency_ms": latency,
                "acoustic_lag_ms": None if lag is None else round(lag, 1),
                "host_placement_ms": None if placement is None else round(placement, 1),
                "codec": air.codec if air is not None else None,
                "rate": air.rate if air is not None else None,
                "adapter": air.adapter if air is not None else None,
                "delay_report_ms": fold.delay_report_ms if fold is not None else None,
            }
        )

    # The spec's order; the first failing check gives the reason. No live lock is required.
    # fold_failed (the worker failed) and no_air_frames (an SBC stream left no frame in the span)
    # sit where the air checks begin, before not_sbc.
    if fold is None:
        return out("not_effective")
    if reference is None:
        return out("no_reference")
    speaker, ref = _ok_phase(measurements.get(mid)), _ok_phase(measurements.get(reference))
    if speaker is None or ref is None:
        return out("not_measured")
    if air is None:
        return out("fold_failed")
    stream = fold.stream_codec
    if (stream is not None and stream != "sbc") or (air.frames > 0 and air.codec != "sbc"):
        return out("not_sbc")
    if air.frames == 0:
        return out("no_air_frames" if stream == "sbc" else "not_sbc")
    if air.epochs > 1 or air.reason == "air_epoch_changed":
        return out("air_epoch_changed")
    if fold.written_reason == "air_delay_changed":
        return out("air_delay_changed")
    if air.phase_ms is None or not air.found:
        return out("no_air_clicks")
    if fold.written_phase_ms is None:
        return out("fold_failed" if fold.written_reason == "fold_failed" else "no_written_clicks")
    lag = unwrap_lag_ms(speaker, ref)
    placement = unwrap_lag_ms(air.phase_ms, fold.written_phase_ms)  # into ±5 s of the 10 s cycle
    return out(None, lag, placement)


def air_calibration_candidates(
    session: Mapping[str, Any], folds: Mapping[str, AirMemberFold]
) -> list[AirCalibrationCandidate]:
    """For every bridge member of the last measured sequence (the reference excepted), its latency or
    the first reason there is none. *session* is the session's ``to_dict()``. With the air sensor
    off (no service running and no fold recorded for the session) there are none at all."""
    current = session.get("sequence")
    if not current or not current.get("uploaded"):
        return []
    if not folds and _air_service() is None:
        return []
    reference = session.get("reference")
    played = set(current.get("members") or ())
    measurements = session.get("measurements") or {}
    return [
        _air_candidate(m["ma_player_id"], reference, measurements, folds.get(m["ma_player_id"]))
        for m in session.get("members") or []
        if m.get("origin") == "bridge" and m["ma_player_id"] in played and m["ma_player_id"] != reference
    ]


def _air_extras(data: Mapping[str, Any]) -> dict[str, Any]:
    """``air_calibrations`` of a session's ``to_dict()`` (its responses and its ``sync`` events)."""
    candidates = air_calibration_candidates(data, _air_folds(str(data["id"])))
    return {"air_calibrations": [c.model_dump(mode="json") for c in candidates]}


session_store.set_publish_extras(_air_extras)


def _not_eligible(detail: str) -> NoReturn:
    raise UseCaseError(409, "not_eligible", detail)


def save_air_calibrations(sid: str, members: list[str]) -> list[AirCalibrationCandidate]:
    """Store the chosen members' latencies (each speaker's entry for its codec, SBC rate and adapter,
    replacing the one there) and apply them live. Every member is checked before any is written."""
    from sendspin_bridge.application import devices as devices_uc
    from sendspin_bridge.config.players import normalize_mac

    session = _session(sid)
    data = session.to_dict()
    member_folds = _air_folds(sid)
    candidates = {c.member: c for c in air_calibration_candidates(data, member_folds)}
    by_id = {m["ma_player_id"]: m for m in data["members"]}
    reference = by_id.get(str(data.get("reference")))
    low, high = AIR_LATENCY_RANGE_MS
    chosen: list[tuple[AirCalibrationCandidate, str, dict[str, Any]]] = []
    warned_no_d_out = False
    for mid in dict.fromkeys(members):
        c = candidates.get(mid)
        if c is None or not c.eligible or c.latency_ms is None or reference is None:
            _not_eligible(f"No air calibration to save for {mid}" + (f" ({c.reason})" if c and c.reason else ""))
        if not low <= c.latency_ms <= high:
            _not_eligible(f"{c.latency_ms:g} ms for {mid} is outside {low:g}-{high:g} ms")
        client = _bridge_client(by_id[mid])
        adapter = normalize_mac(c.adapter) or normalize_mac(
            getattr(getattr(client, "bt_manager", None), "effective_adapter_mac", None)
        )
        if client is None or not adapter or c.codec is None or c.rate is None:
            _not_eligible(f"{mid} is not a speaker of this bridge with a known codec, rate and adapter")
        spread = (data["measurements"].get(mid) or {}).get("spread_ms")
        entry: dict[str, Any] = {
            "codec": c.codec,
            "rate": int(c.rate),
            "adapter": adapter,
            "latency_ms": float(c.latency_ms),
            "measured_at": _now(),
            "reference": short_name(str(reference["name"]))[:80],
            "source": "sync_wizard",
        }
        if isinstance(spread, (int, float)) and 0 <= spread <= 500:
            entry["spread_ms"] = float(spread)
        d_out = member_folds[mid].written_d_out_ms if mid in member_folds else None
        if d_out is not None and 0 <= d_out <= 5000:
            entry["d_ref_ms"] = float(d_out)
        elif mid in member_folds:
            # Fixed now, so a later static-delay change cannot move the calibration's reference.
            current = float(getattr(client, "static_delay_ms", 0.0) or 0.0)
            entry["d_ref_ms"] = min(5000.0, max(0.0, current))
            if not warned_no_d_out:
                warned_no_d_out = True
                logger.warning(
                    "Sync session %s: the air fold of %s reported no d_out_ms; saving the speaker's current "
                    "static delay as d_ref_ms",
                    sid,
                    mid,
                )
        chosen.append((c, str(client.player_id), entry))
    for _c, device_id, entry in chosen:
        devices_uc.save_air_calibration(device_id, entry)
    return [c for c, _d, _e in chosen]


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
