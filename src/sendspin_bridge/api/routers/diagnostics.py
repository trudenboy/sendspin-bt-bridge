"""``/diagnostics``, ``/latency``, ``/calibration``, ``/updates``, ``/hooks``."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from sendspin_bridge.api.auth import auth_settings, client_id, require_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES
from sendspin_bridge.application import calibration as cal
from sendspin_bridge.application import config as cfg
from sendspin_bridge.application import diagnostics as diag
from sendspin_bridge.application.jobs import Job

router = APIRouter(dependencies=[Depends(require_principal)], responses=PROBLEM_RESPONSES)


def _attachment(text: str, filename: str, media_type: str) -> Response:
    return Response(text, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# -- diagnostics ------------------------------------------------------------


@router.get("/diagnostics", response_model=dict[str, Any], tags=["diagnostics"], summary="Structured diagnostics")
def diagnostics(request: Request) -> dict[str, Any]:
    return diag.diagnostics(auth_enabled=auth_settings(request).enabled)


@router.get("/diagnostics/report", response_class=Response, tags=["diagnostics"], summary="Full text report")
def report(request: Request) -> Response:
    text, filename = diag.diagnostics_text_report(auth_enabled=auth_settings(request).enabled)
    return _attachment(text, filename, "text/plain")


@router.get("/diagnostics/guidance", response_model=dict[str, Any], tags=["diagnostics"], summary="Operator guidance")
def guidance() -> dict[str, Any]:
    return diag.operator_guidance()


@router.get("/diagnostics/onboarding", response_model=dict[str, Any], tags=["diagnostics"], summary="Setup checklist")
def onboarding() -> dict[str, Any]:
    return diag.onboarding_assistant()


@router.get("/diagnostics/recovery", response_model=dict[str, Any], tags=["diagnostics"], summary="Recovery guidance")
def recovery() -> dict[str, Any]:
    return diag.recovery_assistant()


@router.get("/diagnostics/timeline", response_model=dict[str, Any], tags=["diagnostics"], summary="Recovery timeline")
def timeline() -> dict[str, Any]:
    return diag.recovery_timeline()


@router.get("/diagnostics/timeline.csv", response_class=Response, tags=["diagnostics"], summary="Timeline as CSV")
def timeline_csv() -> Response:
    text, filename = diag.recovery_timeline_csv()
    return _attachment(text, filename, "text/csv; charset=utf-8")


class CheckIn(BaseModel):
    device_names: list[str] | None = None


@router.post(
    "/diagnostics/checks/{check_key}/run",
    response_model=dict[str, Any],
    tags=["diagnostics"],
    summary="Rerun a safe check",
)
def run_check(check_key: str, body: CheckIn | None = None) -> dict[str, Any]:
    return diag.rerun_check(check_key, body.device_names if body else None)


@router.get("/diagnostics/logs", response_model=dict[str, Any], tags=["diagnostics"], summary="Service log")
def logs(lines: int = 150) -> dict[str, Any]:
    return cfg.service_logs(lines)


@router.get("/diagnostics/logs.txt", response_class=Response, tags=["diagnostics"], summary="Service log as a file")
def logs_txt() -> Response:
    text, filename = cfg.service_logs_text()
    return _attachment(text, filename, "text/plain")


@router.get(
    "/diagnostics/version", response_model=dict[str, Any], tags=["diagnostics"], summary="Version and dependencies"
)
def version() -> dict[str, Any]:
    return cfg.version_info()


@router.get(
    "/diagnostics/bug-report", response_model=dict[str, Any], tags=["diagnostics"], summary="A prefilled bug report"
)
def bug_report(request: Request) -> dict[str, Any]:
    return diag.bug_report(auth_enabled=auth_settings(request).enabled)


class ProxyAvailability(BaseModel):
    available: bool


@router.get(
    "/diagnostics/bug-report/proxy",
    response_model=ProxyAvailability,
    tags=["diagnostics"],
    summary="Can reports be sent without a GitHub account",
)
def bug_report_proxy() -> ProxyAvailability:
    return ProxyAvailability(available=diag.bug_report_proxy_available())


class BugReportIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    description: str = Field(min_length=10, max_length=5000)
    email: str
    diagnostics_text: str = ""


class BugReportOut(BaseModel):
    issue_url: str
    issue_number: int


@router.post(
    "/diagnostics/bug-report",
    response_model=BugReportOut,
    status_code=201,
    tags=["diagnostics"],
    summary="Open a GitHub issue",
)
def submit_bug_report(body: BugReportIn, request: Request) -> BugReportOut:
    return BugReportOut(
        **diag.submit_bug_report(
            title=body.title,
            description=body.description,
            email=body.email,
            diagnostics_text=body.diagnostics_text,
            client_ip=client_id(request),
        )
    )


# -- latency ------------------------------------------------------------------


class PulseLatencyIn(BaseModel):
    pulse_latency_msec: int = Field(ge=1, le=5000)


@router.get("/latency/recommendations", response_model=dict[str, Any], tags=["latency"], summary="Latency assistant")
def latency_recommendations() -> dict[str, Any]:
    return diag.latency_recommendations()


@router.post(
    "/latency/recommendations/apply",
    response_model=dict[str, Any],
    tags=["latency"],
    summary="Save a PulseAudio latency",
)
def apply_latency(body: PulseLatencyIn) -> dict[str, Any]:
    return diag.apply_latency_recommendation(body.pulse_latency_msec)


# -- calibration --------------------------------------------------------------


class DeviceRef(BaseModel):
    device_id: str


class MetronomeIn(BaseModel):
    device_id: str
    action: Literal["start", "stop"]


class MetronomeOut(BaseModel):
    active: bool


class SessionOut(BaseModel):
    session_id: str
    expires_in_seconds: int


class RecordingIn(BaseModel):
    role: Literal["reference", "target"]
    samples: list[float]
    sample_rate: int


@router.get("/calibration/tone.wav", response_class=Response, tags=["calibration"], summary="Click track")
def tone() -> Response:
    return Response(cal.tone_wav(), media_type="audio/wav", headers={"Cache-Control": "public, max-age=86400"})


@router.post("/calibration/play", status_code=204, tags=["calibration"], summary="Play the click track on a speaker")
def play(body: DeviceRef) -> Response:
    cal.play_tone(body.device_id)
    return Response(status_code=204)


@router.post(
    "/calibration/metronome", response_model=MetronomeOut, tags=["calibration"], summary="Start or stop the metronome"
)
def metronome(body: MetronomeIn) -> MetronomeOut:
    return MetronomeOut(active=cal.set_metronome(body.device_id, body.action))


@router.post(
    "/calibration/sessions",
    response_model=SessionOut,
    status_code=201,
    tags=["calibration"],
    summary="Start a microphone session",
)
def create_session() -> SessionOut:
    return SessionOut(**cal.create_session())


@router.post(
    "/calibration/sessions/{session_id}/audio",
    response_model=dict[str, Any],
    tags=["calibration"],
    summary="Upload a recording",
)
def upload(session_id: str, body: RecordingIn) -> dict[str, Any]:
    return cal.upload_recording(session_id, body.role, body.samples, body.sample_rate)


@router.delete("/calibration/sessions/{session_id}", status_code=204, tags=["calibration"], summary="End a session")
def delete_session(session_id: str) -> Response:
    cal.delete_session(session_id)
    return Response(status_code=204)


# -- updates ------------------------------------------------------------------


class UpdateCheckIn(BaseModel):
    channel: Literal["stable", "rc", "beta"] | None = None


class UpdateApplyIn(BaseModel):
    channel: Literal["stable", "rc", "beta"] | None = None
    ref: str | None = Field(
        default=None, description="A release tag, e.g. v2.76.2; default: the newest on the channel."
    )


@router.get("/updates", response_model=dict[str, Any], tags=["updates"], summary="Update availability and method")
def updates() -> dict[str, Any]:
    return cfg.update_info()


@router.post("/updates/check", status_code=202, response_model=Job, tags=["updates"], summary="Check GitHub now")
def check(body: UpdateCheckIn | None = None) -> Job:
    return cfg.start_update_check(body.channel if body else None)


@router.post(
    "/updates/apply",
    status_code=202,
    response_model=dict[str, Any],
    tags=["updates"],
    summary="Install (LXC / bare metal)",
)
def apply(body: UpdateApplyIn | None = None) -> dict[str, Any]:
    return cfg.apply_update(channel=body.channel if body else None, ref=body.ref if body else None)


# -- hooks --------------------------------------------------------------------


class HookIn(BaseModel):
    url: str
    categories: list[str] | None = None
    event_types: list[str] | None = None
    timeout_sec: float = Field(default=5.0, gt=0, le=60)


@router.get("/hooks", response_model=dict[str, Any], tags=["hooks"], summary="Webhooks and recent deliveries")
def hooks() -> dict[str, Any]:
    return diag.hooks_snapshot()


@router.post("/hooks", response_model=dict[str, Any], status_code=201, tags=["hooks"], summary="Register a webhook")
def register_hook(body: HookIn) -> dict[str, Any]:
    return diag.register_hook(
        url=body.url, categories=body.categories, event_types=body.event_types, timeout_sec=body.timeout_sec
    )


@router.delete("/hooks/{hook_id}", status_code=204, tags=["hooks"], summary="Remove a webhook")
def unregister_hook(hook_id: str) -> Response:
    diag.unregister_hook(hook_id)
    return Response(status_code=204)
