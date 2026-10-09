"""Commands on speakers and groups."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, StrictBool

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import devices as dev
from sendspin_bridge.application import playback
from sendspin_bridge.application.jobs import Job

router = APIRouter(tags=["devices"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)


class VolumeIn(BaseModel):
    level: int = Field(ge=0, le=100)


class VolumeOut(BaseModel):
    volume: int
    results: list[dict[str, Any]] | None = None


class MuteIn(BaseModel):
    muted: bool | None = Field(default=None, description="Omit to toggle.")


class MuteOut(BaseModel):
    muted: bool


class PlaybackIn(BaseModel):
    action: Literal["pause", "play"]


class TransportIn(BaseModel):
    command: playback.TransportAction
    value: Any = None


class EnabledIn(BaseModel):
    enabled: bool


class EnabledOut(BaseModel):
    enabled: bool
    restart_required: bool


class ManagementIn(BaseModel):
    enabled: bool = Field(description="false releases the speaker to the host, true reclaims it.")


class PowerSaveIn(BaseModel):
    enabled: bool


class RepairIn(BaseModel):
    quiesce_adapter: StrictBool = Field(
        default=False, description="Park other speakers on this adapter during the attempt (single-adapter hosts)."
    )


class LatencyIn(BaseModel):
    field: playback.LatencyField = "static_delay_ms"
    value: float
    source: str = "manual"
    recommendation_revision: str | None = None


class LatencyOut(BaseModel):
    field: str
    value: int


@router.patch("/devices/{device_id}", response_model=EnabledOut, summary="Enable or disable a speaker")
def patch_device(device_id: str, body: EnabledIn) -> EnabledOut:
    return EnabledOut(**dev.set_enabled(device_id, body.enabled))


@router.put("/devices/{device_id}/volume", response_model=VolumeOut, summary="Set volume")
def put_volume(device_id: str, body: VolumeIn) -> VolumeOut:
    return VolumeOut(**playback.set_device_volume(device_id, body.level))


@router.put("/devices/{device_id}/mute", response_model=MuteOut, summary="Mute, unmute or toggle")
def put_mute(device_id: str, body: MuteIn) -> MuteOut:
    return MuteOut(**playback.set_device_mute(device_id, body.muted))


@router.post("/devices/{device_id}/unmute-sink", status_code=204, summary="Recovery: unmute the audio sink itself")
def post_unmute_sink(device_id: str) -> Response:
    playback.unmute_sink(device_id)
    return Response(status_code=204)


@router.post("/devices/{device_id}/playback", status_code=202, summary="Pause or play")
def post_playback(device_id: str, body: PlaybackIn) -> Response:
    playback.device_playback(device_id, body.action)
    return Response(status_code=202)


@router.post("/devices/{device_id}/transport", status_code=204, summary="Native Sendspin transport command")
def post_transport(device_id: str, body: TransportIn) -> Response:
    playback.transport(device_id, body.command, body.value)
    return Response(status_code=204)


@router.put("/devices/{device_id}/latency", response_model=LatencyOut, summary="Set a latency value")
def put_latency(device_id: str, body: LatencyIn) -> LatencyOut:
    return LatencyOut(
        **playback.set_latency(
            device_id,
            body.field,
            body.value,
            source=body.source,
            recommendation_revision=body.recommendation_revision,
        )
    )


@router.put("/devices/{device_id}/management", status_code=204, summary="Release or reclaim the speaker")
def put_management(device_id: str, body: ManagementIn) -> Response:
    dev.set_management(device_id, body.enabled)
    return Response(status_code=204)


@router.put("/devices/{device_id}/power-save", status_code=204, summary="Suspend or resume the audio sink")
def put_power_save(device_id: str, body: PowerSaveIn) -> Response:
    dev.set_power_save(device_id, body.enabled)
    return Response(status_code=204)


@router.post("/devices/{device_id}/standby", status_code=204, summary="Disconnect and park the player")
def post_standby(device_id: str) -> Response:
    dev.standby(device_id)
    return Response(status_code=204)


@router.post("/devices/{device_id}/wake", status_code=204, summary="Wake from standby")
def post_wake(device_id: str) -> Response:
    dev.wake(device_id)
    return Response(status_code=204)


@router.post("/devices/{device_id}/reconnect", status_code=202, response_model=Job, summary="Reconnect")
def post_reconnect(device_id: str) -> Job:
    return dev.reconnect(device_id)


@router.post("/devices/{device_id}/repair", status_code=202, response_model=Job, summary="Pair again and connect")
def post_repair(device_id: str, body: RepairIn | None = None) -> Job:
    return dev.repair(device_id, quiesce_adapter=bool(body and body.quiesce_adapter))


@router.post("/devices/{device_id}/claim", status_code=204, summary="Become the multipoint speaker's active source")
def post_claim(device_id: str) -> Response:
    dev.claim_audio(device_id)
    return Response(status_code=204)


@router.post("/devices/{device_id}/pairing-window", status_code=202, summary="Let Music Assistant pair (Sendspin)")
def post_pairing_window(device_id: str) -> Response:
    playback.open_pairing_window(device_id)
    return Response(status_code=202)


@router.put("/groups/{group_id}/volume", response_model=VolumeOut, summary="Set volume for every member")
def put_group_volume(group_id: str, body: VolumeIn) -> VolumeOut:
    return VolumeOut(**playback.set_group_volume(group_id, body.level))


@router.post("/groups/{group_id}/playback", response_model=dict[str, Any], summary="Pause or resume a group")
def post_group_playback(group_id: str, body: PlaybackIn) -> dict[str, Any]:
    return playback.group_playback(group_id, body.action)


@router.post("/playback", response_model=dict[str, Any], summary="Pause or resume everything")
def post_all_playback(body: PlaybackIn) -> dict[str, Any]:
    return playback.all_playback(body.action)
