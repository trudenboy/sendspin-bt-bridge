"""``/music-assistant`` — sign-in, discovery, groups, now playing, queue, artwork."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from sendspin_bridge.api.auth import csrf_token, require_principal, resolve_principal
from sendspin_bridge.api.errors import PROBLEM_RESPONSES, ApiError
from sendspin_bridge.application.jobs import Job
from sendspin_bridge.application.music_assistant import auth as ma_auth
from sendspin_bridge.application.music_assistant import groups as ma_groups
from sendspin_bridge.application.music_assistant import playback as ma_playback
from sendspin_bridge.application.music_assistant.common import build_ma_integration_summary

router = APIRouter(
    prefix="/music-assistant",
    tags=["music-assistant"],
    dependencies=[Depends(require_principal)],
    responses=PROBLEM_RESPONSES,
)

_HA_OAUTH_SESSION_KEY = "_ha_oauth"


class Connection(BaseModel):
    url: str
    username: str = ""
    message: str = ""


class PasswordSignIn(BaseModel):
    url: str = Field(default="", description="Empty: find the server (saved URL, Sendspin peer, mDNS).")
    username: str
    password: str


class HaSignIn(BaseModel):
    ma_url: str
    step: Literal["init", "mfa"] = "init"
    username: str = ""
    password: str = ""
    code: str = ""


class HaSignInResult(BaseModel):
    step: Literal["done", "mfa"]
    url: str | None = None
    username: str | None = None
    message: str | None = None
    auth_mode: str | None = None
    mfa_module_id: str | None = None
    mfa_module_name: str | None = None


class HaSilentIn(BaseModel):
    ha_token: str
    ma_url: str


class ReloadOut(BaseModel):
    monitor_reloaded: bool
    job: Job


class QueueCommandIn(BaseModel):
    action: ma_playback.QueueAction
    value: Any = None
    device_id: str | None = None
    syncgroup_id: str | None = None
    group_id: str | None = None


class QueueCommandOut(BaseModel):
    job: Job
    op_id: str
    syncgroup_id: str | None = None
    queue_id: str | None = None
    ma_now_playing: dict[str, Any] | None = Field(default=None, description="Predicted state, shown at once.")


@router.get("", response_model=dict[str, Any], summary="Connection state")
def connection() -> dict[str, Any]:
    return build_ma_integration_summary()


@router.post("/session", response_model=Connection, summary="Sign in with Music Assistant credentials")
def sign_in(body: PasswordSignIn) -> Connection:
    return Connection(**ma_auth.sign_in_with_password(body.url, body.username, body.password))


@router.post(
    "/session/ha", response_model=HaSignInResult, summary="Sign in through Home Assistant (MA as an HA add-on)"
)
def sign_in_ha(body: HaSignIn, request: Request) -> HaSignInResult:
    if body.step == "init":
        result = ma_auth.ha_login_start(body.ma_url, body.username, body.password)
        context = result.pop("_context", None)
        if context is not None:
            request.session[_HA_OAUTH_SESSION_KEY] = context
        return HaSignInResult(**result)
    context = request.session.get(_HA_OAUTH_SESSION_KEY)
    try:
        result = ma_auth.ha_login_mfa(body.ma_url, body.code, context if isinstance(context, dict) else None)
    except ApiError as exc:
        if exc.code in ("flow_expired",):
            request.session.pop(_HA_OAUTH_SESSION_KEY, None)
        raise
    request.session.pop(_HA_OAUTH_SESSION_KEY, None)
    return HaSignInResult(**result)


@router.post("/session/ha-silent", response_model=Connection, summary="Add-on mode: sign in with the user's HA token")
def sign_in_ha_silent(body: HaSilentIn) -> Connection:
    return Connection(**ma_auth.ha_silent_auth(body.ha_token, body.ma_url))


@router.get("/session/ha-auth-page", response_class=HTMLResponse, include_in_schema=False)
def ha_auth_page(request: Request, ma_url: str = "") -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    principal = resolve_principal(request)
    token = csrf_token(request) if principal is not None and principal.needs_csrf else ""
    html = ma_auth.ha_auth_page(ma_url, nonce, token)
    csp = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'self'"
    )
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


@router.post("/discovery", status_code=202, response_model=Job, summary="Find Music Assistant servers")
def discovery() -> Job:
    return ma_groups.start_discovery()


@router.post("/groups/refresh", status_code=202, response_model=Job, summary="Re-read sync groups")
def refresh_groups() -> Job:
    return ma_groups.start_group_refresh()


@router.post("/reload", status_code=202, response_model=ReloadOut, summary="Reconnect with the saved credentials")
def reload() -> ReloadOut:
    return ReloadOut(**ma_groups.reload())


@router.get("/groups", response_model=list[dict[str, Any]], summary="Music Assistant sync groups")
def groups() -> list[dict[str, Any]]:
    return ma_groups.groups()


@router.get("/now-playing", response_model=dict[str, Any], summary="What is playing")
def now_playing() -> dict[str, Any]:
    return ma_playback.now_playing()


@router.post(
    "/queue-commands", status_code=202, response_model=QueueCommandOut, summary="Next, previous, shuffle, repeat, seek"
)
def queue_command(body: QueueCommandIn) -> QueueCommandOut:
    job, initial = ma_playback.start_queue_command(
        body.action, body.value, device_id=body.device_id, syncgroup_id=body.syncgroup_id, group_id=body.group_id
    )
    return QueueCommandOut(job=job, **initial)


@router.get(
    "/artwork",
    response_class=Response,
    responses={200: {"content": {"image/*": {}}, "description": "The image"}},
    summary="Signed artwork, same-origin",
)
def artwork(url: str = Query(...), sig: str = Query(default="")) -> Response:
    body, content_type = ma_playback.fetch_artwork(url, sig)
    return Response(body, media_type=content_type, headers={"Cache-Control": "private, max-age=60"})


@router.get("/debug", response_model=dict[str, Any], summary="Caches, groups and live queues", tags=["diagnostics"])
def debug() -> dict[str, Any]:
    return ma_groups.debug_snapshot()
