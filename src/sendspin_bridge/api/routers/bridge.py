"""``/bridge``, ``/devices`` (read side), ``/groups``, ``/jobs``, ``/health``."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from sendspin_bridge.api.auth import Principal, auth_settings, require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES, ApiError
from sendspin_bridge.application import diagnostics as diag
from sendspin_bridge.application import source
from sendspin_bridge.application.jobs import Job, jobs
from sendspin_bridge.application.models import (
    Bridge,
    BridgeStatus,
    Device,
    Group,
    SourceInputs,
    SourceInputStatus,
    SourceTargets,
)
from sendspin_bridge.application.status import build_status

public = APIRouter(tags=["bridge"])
router = APIRouter(tags=["bridge"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)


class SourcePairingIn(BaseModel):
    seconds: int = 120


class SourceEnabledIn(BaseModel):
    enabled: bool


class SourceTargetIn(BaseModel):
    target_player: str | None = None


class SourceInputIn(BaseModel):
    node: str
    name: str | None = None
    allow_monitor: bool = False


class SourceInputNameIn(BaseModel):
    source_id: str
    name: str


class SourceInputRemoveIn(BaseModel):
    source_id: str


class SourceActiveIn(BaseModel):
    source_id: str


class SourceVolumeIn(BaseModel):
    volume: int


class SourceMuteIn(BaseModel):
    muted: bool


class Health(BaseModel):
    ok: bool = True


@public.get("/health", response_model=Health, summary="Liveness (public)")
def health() -> Health:
    return Health()


@public.get("/bridge/preflight", response_model=dict[str, Any], summary="Host checks, measured now (public)")
def preflight() -> dict[str, Any]:
    """Platform, audio, Bluetooth and D-Bus status for troubleshooting; no device details."""
    return diag.preflight()


def _status(request: Request) -> BridgeStatus:
    return build_status(auth_enabled=auth_settings(request).enabled)


@router.get("/status", response_model=BridgeStatus, summary="Everything the dashboard shows")
def get_status(request: Request) -> BridgeStatus:
    """Bridge, devices and groups in one document — the same payload as the ``status`` event."""
    return _status(request)


@router.get("/bridge", response_model=Bridge, summary="The bridge")
def get_bridge(request: Request) -> Bridge:
    return _status(request).bridge


@router.get("/bridge/runtime", response_model=dict[str, Any], summary="Runtime mode and mocked layers")
def get_runtime() -> dict[str, Any]:
    return diag.runtime_info()


@router.get("/bridge/startup", response_model=dict[str, Any], summary="Start-up progress")
def get_startup() -> dict[str, Any]:
    return diag.startup_progress()


@router.get("/bridge/telemetry", response_model=dict[str, Any], summary="Resource telemetry")
def get_telemetry() -> dict[str, Any]:
    return diag.bridge_telemetry()


class LogLevel(BaseModel):
    level: Literal["INFO", "DEBUG"]


@router.put("/bridge/log-level", response_model=LogLevel, summary="Change the log level now")
def put_log_level(body: LogLevel) -> LogLevel:
    from sendspin_bridge.application.bridge_control import set_log_level

    set_log_level(body.level)
    return body


@router.post("/bridge/restart", status_code=202, summary="Restart the bridge")
def restart_bridge() -> Response:
    from sendspin_bridge.application.bridge_control import restart

    restart()
    return Response(status_code=202)


@router.post("/bridge/source", summary="Start or stop the Bluetooth source now")
async def set_source_enabled(body: SourceEnabledIn) -> dict[str, Any]:
    """Start or stop the Sendspin source without restarting the bridge (#563)."""
    return await (source.start() if body.enabled else source.stop())


@router.post("/bridge/source/ma-pairing", summary="Open the Music Assistant pairing window")
async def open_ma_pairing() -> dict[str, Any]:
    """Return the 8-digit code the operator enters in Music Assistant."""
    return await source.open_ma_pairing()


@router.delete("/bridge/source/ma-pairing", status_code=204, summary="Forget the Music Assistant pairing")
async def cancel_ma_pairing() -> Response:
    """Drop the stored PSK and open a fresh pairing window."""
    await source.cancel_ma_pairing(forget=True)
    return Response(status_code=204)


@router.post("/bridge/source/pairing", summary="Open a Bluetooth pairing window for a phone")
async def open_source_pairing(body: SourcePairingIn | None = None) -> dict[str, Any]:
    """Make the adapters discoverable and pairable for a couple of minutes, with a no-PIN agent."""
    return await source.open_pairing_window(body.seconds if body is not None else 120)


@router.delete("/bridge/source/pairing", status_code=204, summary="Close the phone pairing window")
async def close_source_pairing() -> Response:
    await source.close_pairing_window()
    return Response(status_code=204)


@router.get("/bridge/source/inputs", response_model=SourceInputs, summary="Bluetooth and local source inputs")
async def get_source_inputs(include_monitors: bool = False) -> SourceInputs:
    """Configured inputs plus local audio sources the add dialog can offer."""
    return SourceInputs(**await source.inputs(include_monitors=include_monitors))


@router.post("/bridge/source/inputs", status_code=201, summary="Save a local audio input")
async def add_source_input(body: SourceInputIn) -> dict[str, Any]:
    return await source.add_local_input(body.node, body.name, allow_monitor=body.allow_monitor)


@router.patch("/bridge/source/inputs", summary="Rename a local audio input")
async def rename_source_input(body: SourceInputNameIn) -> dict[str, Any]:
    return await source.rename_local_input(body.source_id, body.name)


@router.delete("/bridge/source/inputs", status_code=204, summary="Remove a source input")
async def remove_source_input(body: SourceInputRemoveIn) -> Response:
    await source.remove_input(body.source_id)
    return Response(status_code=204)


@router.put("/bridge/source/active", summary="Select the active source input")
async def select_source_input(body: SourceActiveIn) -> dict[str, Any]:
    return await source.select_input(body.source_id)


@router.delete("/bridge/source/active", status_code=204, summary="Clear the active source input")
async def clear_source_input() -> Response:
    """Return the device to playback; the feed stays enabled and the choice is remembered."""
    await source.clear_active_input()
    return Response(status_code=204)


@router.put(
    "/bridge/source/active/volume",
    response_model=SourceInputStatus,
    summary="Set the active input's gain",
)
async def set_source_volume(body: SourceVolumeIn) -> SourceInputStatus:
    """Apply 0–100 to the active capture node and remember it per input id."""
    return SourceInputStatus(**await source.set_active_volume(body.volume))


@router.put(
    "/bridge/source/active/mute",
    response_model=SourceInputStatus,
    summary="Mute or unmute the active input",
)
async def set_source_mute(body: SourceMuteIn) -> SourceInputStatus:
    """Silence the capture without stopping the stream to Music Assistant."""
    return SourceInputStatus(**await source.set_active_mute(body.muted))


@router.get("/bridge/source/targets", response_model=SourceTargets, summary="MA players that can play the source")
async def get_source_targets() -> SourceTargets:
    """The players/groups eligible for automatic source playback, and the saved choice."""
    return SourceTargets(**await source.targets())


@router.put("/bridge/source/target", summary="Choose the MA player that plays the source")
async def put_source_target(body: SourceTargetIn) -> dict[str, Any]:
    """Persist a target queue; null means "choose in Music Assistant"."""
    return await source.set_target(body.target_player)


@router.post("/bridge/source/devices/{mac}/use", summary="Use a paired source device")
async def use_source_device(mac: str) -> dict[str, Any]:
    """Make a paired Bluetooth device the source that is captured now."""
    return await source.use_device(mac)


@router.delete("/bridge/source/devices/{mac}", status_code=204, summary="Forget a paired source device")
async def forget_source_device(mac: str) -> Response:
    """Stop capturing the device and remove its Bluetooth bond; the MA pairing is kept."""
    await source.forget_device(mac)
    return Response(status_code=204)


@router.get("/devices", response_model=list[Device], summary="Bridge speakers")
def list_devices(request: Request) -> list[Device]:
    return _status(request).devices


@router.get("/devices/{device_id}", response_model=Device, summary="One speaker")
def get_device(device_id: str, request: Request) -> Device:
    for device in _status(request).devices:
        if device.id == device_id:
            return device
    raise ApiError(404, "unknown_device", f"Unknown device: {device_id}")


@router.get("/devices/{device_id}/latency/history", response_model=dict[str, Any], summary="Timing history")
def get_latency_history(device_id: str) -> dict[str, Any]:
    return diag.latency_history(device_id)


@router.get("/groups", response_model=list[Group], summary="Groups and their members")
def list_groups() -> list[Group]:
    return [Group.from_snapshot(g) for g in diag.groups_summary()]


@router.get("/jobs/{job_id}", response_model=Job, summary="A long-running operation")
def get_job(job_id: str) -> Job:
    return jobs.get(job_id)


@router.delete("/jobs/{job_id}", response_model=Job, summary="Ask a job to stop")
def cancel_job(job_id: str, _principal: Principal = Depends(require_principal)) -> Job:
    return jobs.cancel(job_id)
