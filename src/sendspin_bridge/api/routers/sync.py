"""``/sync`` — group sync tuning. ``public`` serves the click track to Music Assistant.

``router`` needs the bridge's normal authentication and CSRF. Its handlers are plain
``def`` on purpose: the use cases block on Music Assistant I/O and on session locks, so
FastAPI must run them in its threadpool, never on the event loop.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES, ApiError
from sendspin_bridge.application import sync_tuning as uc
from sendspin_bridge.application.models.sync import (
    AbIn,
    ApplyIn,
    CaptureIn,
    CaptureLinkIn,
    CaptureLinkOut,
    CaptureResultOut,
    CaptureStartIn,
    CaptureStateOut,
    DelaysIn,
    MeasurementOut,
    MemberDelayIn,
    MemberVolumeIn,
    PendingRestoreOut,
    ProfileApplyOut,
    ProfileCreate,
    ProfileOut,
    ProfilePatch,
    ProposalOut,
    ReferenceIn,
    SequenceIn,
    SessionCreate,
    SignalIn,
    SkipIn,
    SoloIn,
    SoundIn,
    SyncGroup,
    SyncSessionOut,
)
from sendspin_bridge.sync_tuning import capture_page
from sendspin_bridge.sync_tuning import click_track_host as host
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from sendspin_sync.ports import Track

public = APIRouter(tags=["sync"])
#: The phone capture page, outside /api/v1 (registered before the SPA's catch-all).
page = APIRouter(include_in_schema=False)
#: The capture token's endpoints (under /api/v1): served by the main app and by the HTTPS capture server.
capture = APIRouter(tags=["sync"])


def _serve(token: str, kind: str) -> Response:
    if len(token) != 64 or host.tokens.session_for(token) is None:
        raise ApiError(404, "not_found", "No such signal")
    # Streamed from the file written when the clicker started (no render, nothing held in memory);
    # "identity" keeps the gzip middleware from compressing 35 MB on the event loop.
    try:
        path = host.current_track(cast("Track", kind)).ensure()  # the callers checked *kind*
    except ControlError as exc:
        raise ApiError(507 if exc.code == "disk_full" else 503, exc.code, exc.detail) from exc
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store", "Content-Encoding": "identity"},
    )


@public.get("/sync/signal/{token}.wav", response_class=Response, summary="Click track for a running session (public)")
def signal(token: str) -> Response:
    return _serve(token, "measure")


@public.get(
    "/sync/signal/{token}/{sound}.wav",
    response_class=Response,
    summary="A listening sound of manual tuning for a running session (public)",
    description="sound: clicks, accent or noise; the same token as the session's click track.",
)
def listening_signal(token: str, sound: str) -> Response:
    if sound not in host.tracks:
        raise ApiError(404, "not_found", "No such signal")
    return _serve(token, sound)


async def _capture_body(request: Request, token: str) -> bytes:
    """The raw upload: the token is checked before a byte is read (404), and the body is refused once
    it passes the cap instead of being read in full."""
    uc.check_capture_token(token)
    limit = uc.MAX_CAPTURE_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ApiError(400, "recording_invalid", f"A recording is at most {limit // 1_000_000} MB")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiError(400, "recording_invalid", f"A recording is at most {limit // 1_000_000} MB")
        chunks.append(chunk)
    return b"".join(chunks)


_CAPTURE_BODY = {
    "requestBody": {
        "required": True,
        "description": "The phone's recording of the sequence: any audio file FFmpeg reads (m4a/AAC, wav, webm, …).",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


@capture.get(
    "/sync/capture/{token}",
    response_model=CaptureStateOut,
    summary="Capture state for the phone page (capture token, no sign-in)",
    responses={404: {"description": "Unknown, expired or revoked token"}},
)
def capture_state(token: str, response: Response) -> CaptureStateOut:
    response.headers["Cache-Control"] = "no-store"
    return uc.capture_state(token)


@capture.post(
    "/sync/capture/{token}/start",
    response_model=CaptureStateOut,
    status_code=202,
    summary="The phone starts the sequence (capture token, no sign-in)",
    description=(
        "The capture token's only write besides the upload: every adjustable member not skipped plays "
        "alone in turn. Needs a measuring session whose clicks play (409 clicker_stopped otherwise)."
    ),
)
def capture_start(token: str, body: CaptureStartIn, response: Response) -> CaptureStateOut:
    response.headers["Cache-Control"] = "no-store"
    return uc.capture_start(token, body.mode)


@capture.post(
    "/sync/capture/{token}/recording",
    response_model=CaptureResultOut,
    summary="Upload the phone's recording of the sequence (capture token, no sign-in)",
    openapi_extra=_CAPTURE_BODY,
)
def capture_upload(token: str, body: Annotated[bytes, Depends(_capture_body)], response: Response) -> CaptureResultOut:
    response.headers["Cache-Control"] = "no-store"
    return uc.capture_upload(token, body)


@page.get("/sync/capture/{token}", response_class=HTMLResponse)
def capture_page_html(request: Request, token: str, lang: str | None = None) -> HTMLResponse:
    chosen = capture_page.pick_lang(lang, request.headers.get("accept-language"))
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"}
    try:
        uc.capture_state(token)
    except uc.UseCaseError:
        return HTMLResponse(capture_page.render_gone(chosen), status_code=404, headers=headers)
    nonce = secrets.token_urlsafe(16)
    headers["Content-Security-Policy"] = capture_page.content_security_policy(nonce)
    https_url = None
    if request.url.scheme == "http":
        base = uc.capture_https_base()
        if base:
            https_url = f"{base}/sync/capture/{token}?lang={chosen}"
    return HTMLResponse(capture_page.render(token, chosen, nonce, https_url), headers=headers)


router = APIRouter(
    prefix="/sync",
    tags=["sync"],
    dependencies=[Depends(require_principal)],
    responses=PROBLEM_RESPONSES,
)


# -- groups -------------------------------------------------------------------------------


@router.get("/groups", response_model=list[SyncGroup], summary="Music Assistant sync groups with their members")
def list_groups() -> list[SyncGroup]:
    return uc.list_groups()


@router.get("/groups/{group_id}", response_model=SyncGroup, summary="One sync group")
def get_group(group_id: str) -> SyncGroup:
    return uc.get_group(group_id)


@router.put("/groups/{group_id}/delays", response_model=SyncGroup, summary="Write members' delays (no session)")
def write_delays(group_id: str, body: DelaysIn) -> SyncGroup:
    return uc.write_delays(group_id, body.delays)


@router.post(
    "/groups/{group_id}/capture",
    response_model=ProfileOut,
    status_code=201,
    summary="Save the group's current delays as a profile",
)
def capture_profile(group_id: str, body: CaptureIn) -> ProfileOut:
    return uc.capture_profile(group_id, body)


@router.get("/restores", response_model=list[PendingRestoreOut], summary="Restores left from before a restart")
def list_restores() -> list[PendingRestoreOut]:
    return uc.list_pending_restores()


@router.post(
    "/groups/{group_id}/restore/retry",
    response_model=PendingRestoreOut,
    status_code=202,
    summary="Retry the group's pending restore now",
)
def retry_restore(group_id: str) -> PendingRestoreOut:
    return uc.retry_restore(group_id)


# -- sessions -----------------------------------------------------------------------------


@router.post("/sessions", response_model=SyncSessionOut, status_code=201, summary="Start tuning a group")
def create_session(body: SessionCreate) -> SyncSessionOut:
    return uc.create_session(body)


@router.get("/sessions/{session_id}", response_model=SyncSessionOut, summary="One tuning session")
def get_session(session_id: str) -> SyncSessionOut:
    return uc.get_session(session_id)


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    response_class=Response,
    summary="End a session (the group is restored)",
)
def end_session(session_id: str) -> Response:
    uc.end_session(session_id)
    return Response(status_code=204)


@router.post(
    "/sessions/{session_id}/signal",
    response_model=SyncSessionOut,
    summary="Start the clicker, stop the group, or stop only the clicker",
    description=(
        "start: queue the click track on the group (409 group_playing while it plays anything else; "
        "again while it plays starts it over). stop: stop whatever the group plays. stop_clicker: stop the "
        "group only while it plays this session's click track."
    ),
)
def session_signal(session_id: str, body: SignalIn) -> SyncSessionOut:
    return uc.signal(session_id, body.action)


@router.put(
    "/sessions/{session_id}/sound",
    response_model=SyncSessionOut,
    summary="Pick the manual editor's listening sound",
    description=(
        "clicks, accent or noise, in the manual editor (state tuning: manual tuning, or semi-automatic tuning "
        "once applied). While the clicker plays it starts over with the new sound (the queue is replaced); "
        "mutes and the solo stay. 409 session_state while measuring (measuring, proposed), whose clicker "
        "always plays the measurement's click track. 507 disk_full / 503 track_unavailable when the sound's "
        "file cannot be written."
    ),
)
def session_sound(session_id: str, body: SoundIn) -> SyncSessionOut:
    return uc.session_sound(session_id, body.sound)


@router.put(
    "/sessions/{session_id}/volumes/{member}",
    response_model=SyncSessionOut,
    summary="Set one member's volume",
)
def session_volume(session_id: str, member: str, body: MemberVolumeIn) -> SyncSessionOut:
    return uc.session_volume(session_id, member, body.level)


@router.post(
    "/sessions/{session_id}/solo",
    response_model=SyncSessionOut,
    summary="Hear one member (null: all)",
    description=(
        "Mutes every other member; 409 clicker_stopped while the clicker does not play, 409 "
        "sync_mute_unsupported (with member) when a member's player cannot be muted."
    ),
)
def solo(session_id: str, body: SoloIn) -> SyncSessionOut:
    return uc.solo(session_id, body.member)


@router.post("/sessions/{session_id}/ab", response_model=SyncSessionOut, summary="A-B listening: solo A")
def ab(session_id: str, body: AbIn) -> SyncSessionOut:
    return uc.ab(session_id, body.a, body.b)


@router.post("/sessions/{session_id}/reference", response_model=SyncSessionOut, summary="Pick the reference member")
def set_reference(session_id: str, body: ReferenceIn) -> SyncSessionOut:
    return uc.set_reference(session_id, body.member)


@router.post("/sessions/{session_id}/skip", response_model=SyncSessionOut, summary="Skip measuring a member")
def skip(session_id: str, body: SkipIn) -> SyncSessionOut:
    return uc.skip(session_id, body.member)


@router.put(
    "/sessions/{session_id}/delays/{member}",
    response_model=SyncSessionOut,
    summary="Write one member's delay live (manual tuning)",
)
def session_delay(session_id: str, member: str, body: MemberDelayIn) -> SyncSessionOut:
    return uc.session_delay(session_id, member, body.ms)


async def _recording_body(request: Request) -> bytes:
    """The raw body, refused once it passes the cap instead of being read in full."""
    limit = uc.MAX_RECORDING_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ApiError(400, "recording_invalid", "A recording is at most 2 MB")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiError(400, "recording_invalid", "A recording is at most 2 MB")
        chunks.append(chunk)
    return b"".join(chunks)


_RECORDING_BODY = {
    "requestBody": {
        "required": True,
        "description": "Mono int16 little-endian PCM, at most 2 MB.",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}


@router.post(
    "/sessions/{session_id}/recordings",
    response_model=MeasurementOut,
    summary="Upload one member's recording",
    openapi_extra=_RECORDING_BODY,
)
def upload_recording(
    session_id: str,
    member: Annotated[str, Query(description="Music Assistant player id of the member that played.")],
    start_time_s: Annotated[float, Query(description="Capture clock at the first sample, in seconds.")],
    sample_rate: Annotated[int, Query(description="Samples per second.")],
    body: Annotated[bytes, Depends(_recording_body)],
) -> MeasurementOut:
    return uc.upload_recording(session_id, member, start_time_s, sample_rate, body)


@router.get("/sessions/{session_id}/proposal", response_model=ProposalOut, summary="The current proposal (read-only)")
def proposal(session_id: str) -> ProposalOut:
    return uc.proposal(session_id)


@router.post(
    "/sessions/{session_id}/proposal",
    response_model=ProposalOut,
    summary="Compute the proposal from the measurements so far",
)
def compute_proposal(session_id: str) -> ProposalOut:
    return uc.compute_proposal(session_id)


@router.post(
    "/sessions/{session_id}/apply",
    response_model=SyncSessionOut,
    summary="Write the proposal (or rows); the session goes on as the manual editor",
    description="Clicks that play give way to the editor's listening sound (clicks, unless one was picked).",
)
def apply(session_id: str, body: ApplyIn) -> SyncSessionOut:
    return uc.apply(session_id, body.rows)


@router.post(
    "/sessions/{session_id}/reset",
    response_model=SyncSessionOut,
    summary="Put back every delay this session wrote",
)
def reset(session_id: str) -> SyncSessionOut:
    return uc.reset(session_id)


@router.post(
    "/sessions/{session_id}/finish",
    response_model=SyncSessionOut,
    summary="Finish: keep the delays, restore the group, end the session as done",
)
def finish(session_id: str) -> SyncSessionOut:
    return uc.finish(session_id)


@router.post(
    "/sessions/{session_id}/capture-link",
    response_model=CaptureLinkOut,
    status_code=201,
    summary="A link (and QR) to the phone capture page of this measurement",
    description=(
        "The link carries a short-lived, unguessable token for this session only; it allows reading the "
        "capture state and uploading a recording, nothing else. A new link revokes the previous one; the "
        "token is revoked when the session ends."
    ),
)
def capture_link(session_id: str, body: CaptureLinkIn) -> CaptureLinkOut:
    return uc.capture_link(session_id, body.lang)


@router.post(
    "/sessions/{session_id}/sequence",
    response_model=SyncSessionOut,
    status_code=202,
    summary="Play the sequence for the phone: every member alone in turn",
)
def start_sequence(session_id: str, body: SequenceIn) -> SyncSessionOut:
    return uc.start_sequence(session_id, body.members)


# -- profiles -----------------------------------------------------------------------------


@router.get("/profiles", response_model=list[ProfileOut], summary="A group's delay profiles")
def list_profiles(group_id: str) -> list[ProfileOut]:
    return uc.list_profiles(group_id)


@router.post("/profiles", response_model=ProfileOut, status_code=201, summary="Save a delay profile")
def create_profile(body: ProfileCreate) -> ProfileOut:
    return uc.create_profile(body)


@router.patch("/profiles/{profile_id}", response_model=ProfileOut, summary="Rename a profile or change its notes")
def patch_profile(profile_id: str, body: ProfilePatch) -> ProfileOut:
    return uc.patch_profile(profile_id, body)


@router.delete("/profiles/{profile_id}", status_code=204, response_class=Response, summary="Delete a profile")
def delete_profile(profile_id: str) -> Response:
    uc.delete_profile(profile_id)
    return Response(status_code=204)


@router.post("/profiles/{profile_id}/apply", response_model=ProfileApplyOut, summary="Write a profile's delays")
def apply_profile(profile_id: str) -> ProfileApplyOut:
    return uc.apply_profile(profile_id)
