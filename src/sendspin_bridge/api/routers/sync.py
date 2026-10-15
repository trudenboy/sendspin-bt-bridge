"""``/sync`` — group sync tuning. ``public`` serves the click track to Music Assistant.

``router`` needs the bridge's normal authentication and CSRF. Its handlers are plain
``def`` on purpose: the use cases block on Music Assistant I/O and on session locks, so
FastAPI must run them in its threadpool, never on the event loop.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES, ApiError
from sendspin_bridge.application import sync_tuning as uc
from sendspin_bridge.application.models.sync import (
    AbIn,
    ApplyIn,
    CaptureIn,
    DelaysIn,
    MeasurementOut,
    MemberDelayIn,
    PendingRestoreOut,
    ProfileApplyOut,
    ProfileCreate,
    ProfileOut,
    ProfilePatch,
    ProposalOut,
    ReferenceIn,
    SessionCreate,
    SignalIn,
    SkipIn,
    SoloIn,
    SyncGroup,
    SyncSessionOut,
)
from sendspin_bridge.sync_tuning import click_track_host as host

public = APIRouter(tags=["sync"])


@public.get("/sync/signal/{token}.wav", response_class=Response, summary="Click track for a running session (public)")
def signal(token: str) -> Response:
    if len(token) != 64 or host.tokens.session_for(token) is None:
        raise ApiError(404, "not_found", "No such signal")
    return Response(host.track_wav(), media_type="audio/wav", headers={"Cache-Control": "no-store"})


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


@router.post("/sessions/{session_id}/signal", response_model=SyncSessionOut, summary="Start or stop the click track")
def session_signal(session_id: str, body: SignalIn) -> SyncSessionOut:
    return uc.signal(session_id, body.action)


@router.post("/sessions/{session_id}/solo", response_model=SyncSessionOut, summary="Hear one member (null: all)")
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


@router.get(
    "/sessions/{session_id}/proposal",
    response_model=ProposalOut,
    summary="The proposal (computed once the round's measurements are in)",
)
def proposal(session_id: str) -> ProposalOut:
    return uc.proposal(session_id)


@router.post("/sessions/{session_id}/apply", response_model=SyncSessionOut, summary="Write the proposal (or rows)")
def apply(session_id: str, body: ApplyIn) -> SyncSessionOut:
    return uc.apply(session_id, body.rows, verify=body.verify)


@router.post("/sessions/{session_id}/undo", response_model=SyncSessionOut, summary="Undo an automatic apply")
def undo(session_id: str) -> SyncSessionOut:
    return uc.undo(session_id)


@router.post("/sessions/{session_id}/verify", response_model=SyncSessionOut, summary="Measure again")
def verify(session_id: str) -> SyncSessionOut:
    return uc.verify(session_id)


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
