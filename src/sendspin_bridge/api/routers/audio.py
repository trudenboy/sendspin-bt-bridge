"""``/audio`` — local audio outputs and the safe commands on them."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import audio as audio_uc
from sendspin_bridge.application.models.audio import AudioSinkOut, SinkStateOut

router = APIRouter(
    prefix="/audio", tags=["audio"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES
)


class VolumeIn(BaseModel):
    volume: int = Field(description="0-100 inclusive; anything else is a 400 invalid_volume.")


class MuteIn(BaseModel):
    muted: bool


class DefaultSinkIn(BaseModel):
    name: str = Field(description="The sink that becomes the audio server's default.")


@router.get("/sinks", response_model=list[AudioSinkOut], summary="Local audio outputs")
async def list_sinks() -> list[AudioSinkOut]:
    """Sinks a ``pulse_sink`` player can use (Bluetooth and bridge-internal sinks left out)."""
    return await audio_uc.list_sinks()


@router.put("/sinks/{name}/volume", response_model=SinkStateOut, summary="Set an output's volume")
async def set_sink_volume(name: str, body: VolumeIn) -> SinkStateOut:
    """Set the sink's level, 0-100 inclusive."""
    return await audio_uc.set_sink_volume(name, body.volume)


@router.put("/sinks/{name}/mute", response_model=SinkStateOut, summary="Mute or unmute an output")
async def set_sink_mute(name: str, body: MuteIn) -> SinkStateOut:
    """Mute (``true``) or unmute (``false``) the sink."""
    return await audio_uc.set_sink_mute(name, body.muted)


@router.put("/default-sink", response_model=SinkStateOut, summary="Set the default output")
async def set_default_sink(body: DefaultSinkIn) -> SinkStateOut:
    """Make the named sink the audio server's default."""
    return await audio_uc.set_default_sink(body.name)


@router.post("/sinks/{name}/suspend", response_model=SinkStateOut, summary="Suspend an output")
async def suspend_sink(name: str) -> SinkStateOut:
    """Suspend the sink: it keeps existing, but no longer plays."""
    return await audio_uc.suspend_sink(name)


@router.post("/sinks/{name}/resume", response_model=SinkStateOut, summary="Resume an output")
async def resume_sink(name: str) -> SinkStateOut:
    """Resume a suspended sink."""
    return await audio_uc.resume_sink(name)
