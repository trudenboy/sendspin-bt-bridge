"""``/auth`` — sessions for the SPA, bearer tokens for integrations."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from sendspin_bridge.api.auth import (
    Principal,
    auth_settings,
    client_id,
    csrf_token,
    is_trusted_ingress,
    peer_address,
    require_principal,
    resolve_principal,
    trust_policy,
)
from sendspin_bridge.api.errors import PROBLEM_RESPONSES, ApiError
from sendspin_bridge.application import auth as auth_uc

router = APIRouter(prefix="/auth", tags=["auth"], responses=PROBLEM_RESPONSES)

AuthMethodName = Literal["ha", "ha_via_ma", "ma", "password"]


class SessionState(BaseModel):
    authenticated: bool
    user: str | None = None
    principal: Literal["ingress", "session", "bearer", "anonymous"] | None = None
    auth_enabled: bool
    ha_addon: bool
    methods: list[AuthMethodName]
    csrf_token: str = Field(description="Echo in the X-CSRF-Token header on state-changing requests.")


class SignInRequest(BaseModel):
    method: AuthMethodName = "password"
    username: str = ""
    password: str = ""
    flow_id: str = Field(default="", description="Second step of an MFA sign-in: the flow from the first step.")
    code: str = Field(default="", description="Second step of an MFA sign-in: the one-time code.")


class SignInResult(BaseModel):
    status: Literal["signed_in", "mfa_required"]
    user: str | None = None
    flow_id: str | None = None
    mfa_module_id: str | None = None
    mfa_module_name: str | None = None
    message: str = ""
    csrf_token: str


class TokenRecord(BaseModel):
    id: str
    label: str
    created: str | None = None
    last_used: str | None = None


class TokenList(BaseModel):
    tokens: list[TokenRecord]


class NewToken(BaseModel):
    label: str = Field(default="ha-custom-component", max_length=64)


class IssuedToken(BaseModel):
    token: str = Field(description="The plaintext token. Returned once; store it now.")
    record: TokenRecord


class PasswordChange(BaseModel):
    current_password: str = Field(default="", description="Required when a password is already set.")
    password: str = Field(min_length=8)


def _session_state(request: Request) -> SessionState:
    settings = auth_settings(request)
    principal = resolve_principal(request)
    return SessionState(
        authenticated=principal is not None,
        user=principal.user if principal else None,
        principal=principal.kind if principal else None,
        auth_enabled=settings.enabled,
        ha_addon=settings.ha_addon,
        methods=auth_uc.available_methods(),
        csrf_token=csrf_token(request),
    )


def _require_ui_principal(principal: Principal = Depends(require_principal)) -> Principal:
    """Only a person behind the UI manages credentials (tokens, the password) — never a token."""
    if principal.kind == "bearer":
        raise ApiError(403, "forbidden", "Tokens cannot manage credentials.")
    return principal


@router.get("/session", response_model=SessionState, summary="Who am I")
def get_session(request: Request) -> SessionState:
    """Public: the SPA calls this first to decide between the app and the sign-in form."""
    return _session_state(request)


@router.post("/session", response_model=SignInResult, summary="Sign in")
def sign_in(body: SignInRequest, request: Request) -> SignInResult:
    """One step of a sign-in. HA accounts with MFA answer ``mfa_required`` first;
    send the same method again with ``flow_id`` and ``code``."""
    who = client_id(request)
    peer = peer_address(request)
    if peer and trust_policy(request).is_trusted(peer) and who == peer:
        # Behind a proxy that names no client: bucket by username, not by the proxy.
        who = f"proxy-login:{body.username.strip().casefold()}" if body.username.strip() else f"proxy:{peer}"
    limiter = auth_uc.rate_limiter
    if limiter.is_locked_out(who):
        duration = auth_uc.lockout_settings().lockout_s
        raise ApiError(
            429, "locked_out", f"Too many failed attempts — try again in {auth_uc.format_duration(int(duration))}"
        )

    pending_user = request.session.get("_ha_login_user", "") if body.flow_id else ""
    outcome = auth_uc.sign_in(
        body.method,
        username=body.username,
        password=body.password,
        flow_id=body.flow_id,
        code=body.code,
        pending_user=pending_user if isinstance(pending_user, str) else "",
    )
    if outcome.counts_as_failure:
        limiter.record_failure(who)

    if outcome.kind == "success":
        limiter.clear(who)
        bucket = request.session.get("_lockout_client_id")
        request.session.clear()
        if bucket:
            request.session["_lockout_client_id"] = bucket
        request.session["authenticated"] = True
        request.session["auth_method"] = body.method
        if outcome.user:
            request.session["ha_user"] = outcome.user
        if outcome.fallback_used:
            request.session["auth_fallback_used"] = True
        return SignInResult(status="signed_in", user=outcome.user, csrf_token=csrf_token(request))
    if outcome.kind == "mfa_required":
        if outcome.user:
            request.session["_ha_login_user"] = outcome.user
        return SignInResult(
            status="mfa_required",
            flow_id=outcome.flow_id,
            mfa_module_id=outcome.mfa_module_id,
            mfa_module_name=outcome.mfa_module_name,
            message=outcome.message,
            csrf_token=csrf_token(request),
        )
    if outcome.kind == "expired":
        request.session.pop("_ha_login_user", None)
        raise ApiError(400, "flow_expired", outcome.message)
    if outcome.kind == "unavailable":
        raise ApiError(503, "auth_unavailable", outcome.message)
    raise ApiError(401, "invalid_credentials", outcome.message)


@router.delete("/session", status_code=204, summary="Sign out")
def sign_out(request: Request, _principal: Principal = Depends(require_principal)) -> Response:
    bucket = request.session.get("_lockout_client_id")
    request.session.clear()
    if bucket:
        request.session["_lockout_client_id"] = bucket
    return Response(status_code=204)


@router.put("/password", status_code=204, summary="Set the local password")
def set_password(
    body: PasswordChange, request: Request, _principal: Principal = Depends(_require_ui_principal)
) -> Response:
    """Standalone only — in add-on mode Home Assistant manages the users.

    Changing an existing password needs the current one, so a borrowed session
    cannot lock the owner out.
    """
    from sendspin_bridge.application.errors import config_write_error
    from sendspin_bridge.config import check_password, hash_password, load_config, update_config

    if auth_settings(request).ha_addon:
        raise ApiError(400, "managed_by_ha", "Use HA user management in HA addon mode")
    stored = load_config().get("AUTH_PASSWORD_HASH", "")
    if stored and not check_password(body.current_password, stored):
        raise ApiError(403, "invalid_credentials", "The current password is wrong")
    hashed = hash_password(body.password)
    try:
        update_config(lambda cfg: cfg.__setitem__("AUTH_PASSWORD_HASH", hashed))
    except OSError as exc:
        raise config_write_error(exc, context="Cannot save password") from exc
    return Response(status_code=204)


@router.get("/tokens", response_model=TokenList, summary="List bearer tokens")
def list_tokens(_principal: Principal = Depends(_require_ui_principal)) -> TokenList:
    from sendspin_bridge.services.diagnostics.auth_tokens import list_tokens as _list

    return TokenList(tokens=[TokenRecord(**t.to_public_dict()) for t in _list()])


@router.post("/tokens", response_model=IssuedToken, status_code=201, summary="Issue a bearer token")
def issue_token(body: NewToken, _principal: Principal = Depends(_require_ui_principal)) -> IssuedToken:
    from sendspin_bridge.services.diagnostics.auth_tokens import issue_token as _issue

    label = body.label.strip()
    if not label:
        raise ApiError(400, "label_required", "label required")
    plain, record = _issue(label)
    return IssuedToken(token=plain, record=TokenRecord(**record.to_public_dict()))


@router.delete("/tokens/{token_id}", status_code=204, summary="Revoke a bearer token")
def revoke_token(token_id: str, _principal: Principal = Depends(_require_ui_principal)) -> Response:
    from sendspin_bridge.services.diagnostics.auth_tokens import revoke_token as _revoke

    if not _revoke(token_id):
        raise ApiError(404, "unknown_token", "Unknown token id")
    return Response(status_code=204)


@router.post("/ha-pair", response_model=IssuedToken, status_code=201, summary="Pair the HA integration")
def ha_pair(request: Request) -> IssuedToken:
    """Public, but only for the HA custom component reaching us through the
    Supervisor: add-on mode, a trusted peer, and ``X-Ingress-Path``."""
    from sendspin_bridge.services.diagnostics.auth_tokens import issue_token as _issue

    if not is_trusted_ingress(request, auth_settings(request)):
        raise ApiError(403, "forbidden", "Not allowed from this network")
    plain, record = _issue("ha-custom-component")
    return IssuedToken(token=plain, record=TokenRecord(**record.to_public_dict()))
