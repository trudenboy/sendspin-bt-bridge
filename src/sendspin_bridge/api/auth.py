"""Who is calling, decided the same way for every entry point.

A request is authenticated by exactly one of:

* **ingress** — the Home Assistant Supervisor proxy, only in add-on mode, only
  from a trusted peer, only with ``X-Ingress-Path``;
* **session** — the signed session cookie the SPA gets from ``POST /auth/session``;
* **bearer** — a long-lived token from ``/auth/tokens`` (HA integration, MCP, scripts);
* **anonymous** — when authentication is switched off (standalone, ``AUTH_ENABLED``
  unset), every caller is let in, as the legacy UI did.

Browser callers (everyone but bearer) must echo the session's CSRF token in
``X-CSRF-Token`` on state-changing requests. A cross-site page cannot set that
header without a CORS preflight the bridge never grants.
"""

from __future__ import annotations

import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends
from starlette.requests import HTTPConnection

from sendspin_bridge.api.errors import ApiError
from sendspin_bridge.config import load_config
from sendspin_bridge.security.request_identity import TrustPolicy

PrincipalKind = Literal["ingress", "session", "bearer", "anonymous"]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "X-CSRF-Token"
_CSRF_SESSION_KEY = "csrf_token"


@dataclass(frozen=True)
class AuthSettings:
    """Fixed at start-up, like the legacy gate: a change needs a restart."""

    enabled: bool
    ha_addon: bool

    @classmethod
    def from_environment(cls, config: dict | None = None) -> AuthSettings:
        ha_addon = bool(os.environ.get("SUPERVISOR_TOKEN"))
        cfg = config if config is not None else load_config()
        return cls(enabled=ha_addon or bool(cfg.get("AUTH_ENABLED", False)), ha_addon=ha_addon)


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    user: str | None = None

    @property
    def needs_csrf(self) -> bool:
        """Cookie-borne identities need a token; bearer and auth-off callers do not.

        With authentication off there is no session to bind a token to (and the
        HA integration and scripts post without one, as before); browser
        requests from another site are refused by origin instead.
        """
        return self.kind in ("session", "ingress")


def auth_settings(request: HTTPConnection) -> AuthSettings:
    return request.app.state.auth_settings


def trust_policy(request: HTTPConnection) -> TrustPolicy:
    """The request's trust policy, built once from the live config."""
    cached = request.scope.get("sendspin.trust_policy")
    if cached is None:
        cached = TrustPolicy.from_config(load_config())
        request.scope["sendspin.trust_policy"] = cached
    return cached


def peer_address(request: HTTPConnection) -> str:
    client = request.scope.get("client")
    return (client[0] if client else "") or ""


def client_id(request: HTTPConnection) -> str:
    """The address this request is attributed to (rate limiting, logs)."""
    policy = trust_policy(request)
    return policy.client_ip(
        peer_address(request),
        request.headers.get("X-Forwarded-For", ""),
        request.headers.get("X-Real-IP", ""),
    )


def is_trusted_ingress(request: HTTPConnection, settings: AuthSettings) -> bool:
    """A request the HA Supervisor proxied to us from the add-on panel."""
    if not settings.ha_addon or not request.headers.get("X-Ingress-Path"):
        return False
    return trust_policy(request).is_trusted(peer_address(request))


def csrf_token(request: HTTPConnection) -> str:
    """The session's CSRF token, minted on first use."""
    token = request.session.get(_CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_hex(32)
        request.session[_CSRF_SESSION_KEY] = token
    return token


def _ingress_user(request: HTTPConnection) -> str:
    display = request.headers.get("X-Remote-User-Display-Name") or request.headers.get("X-Remote-User-Name") or ""
    if display:
        return display
    cached = request.session.get("ha_user")
    if isinstance(cached, str) and cached:
        return cached
    username = load_config().get("MA_USERNAME", "")
    return username or "HA User"


def resolve_principal(request: HTTPConnection) -> Principal | None:
    """Who the caller is, or ``None`` when nobody we know."""
    settings = auth_settings(request)
    if not settings.enabled:
        return Principal("anonymous")
    if is_trusted_ingress(request, settings):
        user = _ingress_user(request)
        request.session["authenticated"] = True
        request.session["ha_user"] = user
        return Principal("ingress", user)
    if request.session.get("authenticated"):
        session_user = request.session.get("ha_user")
        return Principal("session", session_user if isinstance(session_user, str) and session_user else None)
    from sendspin_bridge.services.diagnostics.auth_tokens import extract_bearer, find_matching_token

    presented = extract_bearer(request.headers)
    if presented:
        record = find_matching_token(presented)
        if record is not None:
            return Principal("bearer", f"bearer:{record.label}")
    return None


def is_cross_site(request: HTTPConnection) -> bool:
    """A browser request started by another site (non-browsers send neither header)."""
    fetch_site = request.headers.get("Sec-Fetch-Site", "")
    if fetch_site in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("Origin")
    if not origin or origin == "null":
        return bool(origin)
    host = request.headers.get("Host", "")
    if trust_policy(request).is_trusted(peer_address(request)):
        host = request.headers.get("X-Forwarded-Host") or host
    return urlparse(origin).netloc.lower() != host.lower()


def require_principal(request: HTTPConnection) -> Principal:
    """Router dependency: an authenticated caller with a valid CSRF token."""
    principal = resolve_principal(request)
    if principal is None:
        raise ApiError(401, "unauthorized", "Sign in first.", headers={"WWW-Authenticate": "Bearer"})
    method = request.scope.get("method", "GET")
    if principal.kind == "anonymous" and method not in SAFE_METHODS and is_cross_site(request):
        raise ApiError(403, "cross_site", "Cross-site request refused.")
    if principal.needs_csrf and method not in SAFE_METHODS:
        presented = request.headers.get(CSRF_HEADER, "")
        expected = request.session.get(_CSRF_SESSION_KEY, "")
        if not presented or not expected or not hmac.compare_digest(presented, expected):
            raise ApiError(
                403, "csrf_failed", f"Missing or stale {CSRF_HEADER}; read it from GET /api/v1/auth/session."
            )
    return principal


Authenticated = Depends(require_principal)
