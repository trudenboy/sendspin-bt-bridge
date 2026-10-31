"""``/bridge``, ``/devices`` (read side), ``/groups``, ``/jobs``, ``/health``."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from sendspin_bridge.api.auth import Principal, auth_settings, require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES, ApiError
from sendspin_bridge.application import diagnostics as diag
from sendspin_bridge.application.jobs import Job, jobs
from sendspin_bridge.application.models import Bridge, BridgeStatus, Device, Group
from sendspin_bridge.application.status import build_status

public = APIRouter(tags=["bridge"])
router = APIRouter(tags=["bridge"], dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)


class SourcePairingIn(BaseModel):
    seconds: int = 120


class SourceEnabledIn(BaseModel):
    enabled: bool


class SourcePlayerIn(BaseModel):
    device_id: str
    enabled: bool


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
    from sendspin_bridge.bridge.orchestrator import _resolve_config_dir
    from sendspin_bridge.config import load_config
    from sendspin_bridge.services.source import manager as source_manager

    if not body.enabled:
        return await source_manager.stop()
    return await source_manager.start(load_config(), _resolve_config_dir())


@router.post("/bridge/source/players", summary="Play the Bluetooth source on a speaker, or stop")
async def set_source_player(body: SourcePlayerIn) -> dict[str, Any]:
    """Point a speaker's player at the source, or send it back to Music Assistant (#563)."""
    from sendspin_bridge.application.status import find_client
    from sendspin_bridge.services.source import relay

    client = find_client(body.device_id)
    if not body.enabled:
        relay.disarm(body.device_id)
        return {"device_id": body.device_id, "playing_source": False}
    # The speaker the bridge drives itself: relay the phone's capture straight to its sink. The
    # device stays armed, so a phone that reconnects (or a bridge restart) resumes it.
    sink = getattr(client, "bluetooth_sink_name", None) or getattr(client, "sink_name", None)
    if not sink:
        raise ApiError(409, "source_sink_missing", "That speaker has no audio output")
    relay.arm(body.device_id, str(sink))
    return {"device_id": body.device_id, "playing_source": True}


@router.post("/bridge/source/pairing", summary="Open a Bluetooth pairing window for a phone")
async def open_source_pairing(body: SourcePairingIn | None = None) -> dict[str, Any]:
    """Make the adapters discoverable and pairable for a couple of minutes, with a no-PIN agent."""
    from sendspin_bridge.services.source.pairing import open_window

    return await open_window(body.seconds if body is not None else 120)


@router.delete("/bridge/source/pairing", status_code=204, summary="Close the phone pairing window")
async def close_source_pairing() -> Response:
    from sendspin_bridge.services.source.pairing import close_window

    await close_window()
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
