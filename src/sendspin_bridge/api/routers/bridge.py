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
