"""Commands on speakers and groups."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, StrictBool, model_validator

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import devices as dev
from sendspin_bridge.application import playback
from sendspin_bridge.application.jobs import Job
from sendspin_bridge.application.models.devices import Device

router = APIRouter(tags=["devices"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)


def supports(action: str) -> Any:
    """Route dependency: 409 unless the device's backend supports *action*."""

    def _check(device_id: str) -> None:
        dev.require_action(device_id, action)

    return Depends(_check)


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
    value: Any = Field(
        default=None,
        description="volume: 0-100; mute: bool; seek: absolute position in ms within the current track.",
    )


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


class DevicePatchIn(BaseModel):
    enabled: StrictBool | None = Field(default=None, description="Enable or disable the speaker.")
    playout_sync_enabled: StrictBool | None = Field(
        default=None,
        description="false runs this speaker the way it worked before playout sync; true follows the bridge setting.",
    )

    @model_validator(mode="after")
    def _something_to_change(self) -> DevicePatchIn:
        if self.enabled is None and self.playout_sync_enabled is None:
            raise ValueError("send enabled and/or playout_sync_enabled")
        return self


class DevicePatchOut(BaseModel):
    enabled: bool | None = None
    restart_required: bool | None = None
    playout_sync_enabled: bool | None = None
    device: Device | None = Field(default=None, description="The updated speaker, when it is running.")


@router.patch(
    "/devices/{device_id}",
    response_model=DevicePatchOut,
    response_model_exclude_none=True,
    summary="Change a speaker's settings",
)
def patch_device(device_id: str, body: DevicePatchIn) -> DevicePatchOut:
    """Enable or disable the speaker, and/or opt it out of (or back into) playout sync."""
    result: dict[str, Any] = {}
    if body.enabled is not None:
        result.update(dev.set_enabled(device_id, body.enabled))
    if body.playout_sync_enabled is not None:
        result.update(dev.set_playout_sync(device_id, body.playout_sync_enabled))
    return DevicePatchOut(**result)


class RemovedOut(BaseModel):
    removed: bool
    reconfig: dict[str, Any] = Field(default_factory=dict)


@router.delete("/devices/{device_id}", response_model=RemovedOut, summary="Remove a speaker from the bridge")
def delete_device(device_id: str) -> RemovedOut:
    """Stops its player and removes its Bluetooth bond, as removing it from the settings does."""
    return RemovedOut(**dev.remove(device_id))


@router.put(
    "/devices/{device_id}/volume", response_model=VolumeOut, summary="Set volume", dependencies=[supports("volume")]
)
def put_volume(device_id: str, body: VolumeIn) -> VolumeOut:
    return VolumeOut(**playback.set_device_volume(device_id, body.level))


@router.put(
    "/devices/{device_id}/mute",
    response_model=MuteOut,
    summary="Mute, unmute or toggle",
    dependencies=[supports("volume")],
)
def put_mute(device_id: str, body: MuteIn) -> MuteOut:
    return MuteOut(**playback.set_device_mute(device_id, body.muted))


@router.post("/devices/{device_id}/unmute-sink", status_code=204, summary="Recovery: unmute the audio sink itself")
def post_unmute_sink(device_id: str) -> Response:
    playback.unmute_sink(device_id)
    return Response(status_code=204)


@router.post(
    "/devices/{device_id}/playback", status_code=202, summary="Pause or play", dependencies=[supports("transport")]
)
def post_playback(device_id: str, body: PlaybackIn) -> Response:
    playback.device_playback(device_id, body.action)
    return Response(status_code=202)


@router.post(
    "/devices/{device_id}/transport",
    status_code=204,
    summary="Native Sendspin transport command",
    dependencies=[supports("transport")],
)
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


@router.put(
    "/devices/{device_id}/power-save",
    status_code=204,
    summary="Suspend or resume the audio sink",
    dependencies=[supports("power_save")],
)
def put_power_save(device_id: str, body: PowerSaveIn) -> Response:
    dev.set_power_save(device_id, body.enabled)
    return Response(status_code=204)


@router.post(
    "/devices/{device_id}/standby",
    status_code=204,
    summary="Disconnect and park the player",
    dependencies=[supports("standby")],
)
def post_standby(device_id: str) -> Response:
    dev.standby(device_id)
    return Response(status_code=204)


@router.post(
    "/devices/{device_id}/wake", status_code=204, summary="Wake from standby", dependencies=[supports("standby")]
)
def post_wake(device_id: str) -> Response:
    dev.wake(device_id)
    return Response(status_code=204)


@router.post(
    "/devices/{device_id}/reconnect",
    status_code=202,
    response_model=Job,
    summary="Reconnect",
    dependencies=[supports("reconnect")],
)
def post_reconnect(device_id: str) -> Job:
    return dev.reconnect(device_id)


@router.post(
    "/devices/{device_id}/repair",
    status_code=202,
    response_model=Job,
    summary="Pair again and connect",
    dependencies=[supports("repair")],
)
def post_repair(device_id: str, body: RepairIn | None = None) -> Job:
    return dev.repair(device_id, quiesce_adapter=bool(body and body.quiesce_adapter))


@router.post(
    "/devices/{device_id}/claim",
    status_code=204,
    summary="Become the multipoint speaker's active source",
    dependencies=[supports("claim")],
)
def post_claim(device_id: str) -> Response:
    dev.claim_audio(device_id)
    return Response(status_code=204)


@router.post(
    "/devices/{device_id}/pairing-window",
    status_code=202,
    summary="Let Music Assistant pair (Sendspin)",
    dependencies=[supports("pairing_window")],
)
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
