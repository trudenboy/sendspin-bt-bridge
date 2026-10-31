"""``/hardware`` — controllers, Bluetooth devices, audio and the host's capabilities."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import hardware as hardware_uc
from sendspin_bridge.application.models.hardware import (
    BtDeviceDetail,
    CardDetail,
    ControllerDetail,
    EndpointDetail,
    HardwareSnapshot,
)

router = APIRouter(
    prefix="/hardware",
    tags=["hardware"],
    dependencies=[Depends(require_principal)],
    responses=PROBLEM_RESPONSES,
)


@router.get("", response_model=HardwareSnapshot, summary="Hardware inventory")
async def get_hardware() -> HardwareSnapshot:
    """Every controller, every paired device, and what this host can see at all."""
    return await hardware_uc.snapshot()


@router.get(
    "/controllers/{controller_id}",
    response_model=ControllerDetail,
    summary="One Bluetooth controller",
)
async def get_controller(controller_id: str) -> ControllerDetail:
    """The adapter by ``hciN`` or by controller MAC, with the bonds it holds."""
    return await hardware_uc.controller(controller_id)


@router.get(
    "/bluetooth/{mac}",
    response_model=BtDeviceDetail,
    summary="One Bluetooth device",
)
async def get_bluetooth_device(mac: str) -> BtDeviceDetail:
    """A paired or known device: UUIDs, manufacturer and every bond."""
    return await hardware_uc.bluetooth_device(mac)


@router.get(
    "/audio/cards/{name}",
    response_model=CardDetail,
    summary="One audio card",
)
async def get_audio_card(name: str) -> CardDetail:
    """A card's profiles, ports and the endpoints it owns."""
    return await hardware_uc.audio_card(name)


@router.get(
    "/audio/sinks/{name}",
    response_model=EndpointDetail,
    summary="One audio sink",
)
async def get_audio_sink(name: str) -> EndpointDetail:
    """A sink by its audio-server name, with identifiers and its card's ports."""
    return await hardware_uc.audio_sink(name)


@router.get(
    "/audio/sources/{name}",
    response_model=EndpointDetail,
    summary="One audio source",
)
async def get_audio_source(name: str) -> EndpointDetail:
    """A source, including the sink it monitors when it is a monitor source."""
    return await hardware_uc.audio_source(name)
