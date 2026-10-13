"""``/audio`` — local audio outputs."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import audio as audio_uc
from sendspin_bridge.application.models.audio import AudioSinkOut

router = APIRouter(
    prefix="/audio", tags=["audio"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES
)


@router.get("/sinks", response_model=list[AudioSinkOut], summary="Local audio outputs")
async def list_sinks() -> list[AudioSinkOut]:
    """Sinks a ``pulse_sink`` player can use (Bluetooth and bridge-internal sinks left out)."""
    return await audio_uc.list_sinks()
