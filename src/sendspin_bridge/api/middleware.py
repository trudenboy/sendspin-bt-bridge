"""ASGI middleware: HA ingress prefix and security headers."""

from __future__ import annotations

import os

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sendspin_bridge.config import load_config
from sendspin_bridge.security.request_identity import TrustPolicy

# The SPA is compiled ahead of time: no inline scripts, so script-src is strict.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "frame-ancestors 'self'"
)


class IngressMiddleware:
    """Serve under the HA ingress prefix when the Supervisor proxies us.

    ``X-Ingress-Path`` is honoured only from a trusted peer and only as a
    single-leading-slash absolute path — the same rules as the legacy WSGI
    middleware. It becomes the ASGI ``root_path`` so generated URLs carry it.

    The Supervisor strips the prefix before forwarding, while ASGI's ``path``
    must include ``root_path`` — so the prefix is put back in front of the
    path. Routing then strips it again, and a request whose own path happens
    to start with the header's value (the HA integration sends
    ``X-Ingress-Path: /api/auth/ha-pair`` to that very path) still routes.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            raw = dict(scope.get("headers") or ()).get(b"x-ingress-path", b"").decode("latin-1").rstrip("/")
            if raw and raw.startswith("/") and not raw.startswith("//"):
                client = scope.get("client")
                peer = (client[0] if client else "") or ""
                policy = TrustPolicy.from_config(load_config())
                scope["sendspin.trust_policy"] = policy
                if policy.is_trusted(peer):
                    scope = dict(scope)
                    scope["root_path"] = raw
                    scope["path"] = raw + scope["path"]
                    if scope.get("raw_path") is not None:
                        scope["raw_path"] = raw.encode("latin-1") + scope["raw_path"]
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """``nosniff`` everywhere; CSP and no-store on HTML; framing rules by mode."""

    def __init__(self, app: ASGIApp, *, ha_addon: bool | None = None) -> None:
        self.app = app
        self.ha_addon = bool(os.environ.get("SUPERVISOR_TOKEN")) if ha_addon is None else ha_addon

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                # In add-on mode HA ingress frames the panel; CSP frame-ancestors
                # already limits who may.
                if not self.ha_addon:
                    headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                if headers.get("content-type", "").startswith("text/html"):
                    # A page with its own inline script sets its own nonce'd policy.
                    headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
                    headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            await send(message)

        await self.app(scope, receive, send_with_headers)
