"""The ASGI application: API v1, the HA compat routes, events, and the SPA."""

from __future__ import annotations

import contextlib
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, FastAPI
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from sendspin_bridge.api.auth import AuthSettings
from sendspin_bridge.api.errors import install_error_handlers
from sendspin_bridge.api.events import EventHub
from sendspin_bridge.api.events import router as events_router
from sendspin_bridge.api.middleware import IngressMiddleware, SecurityHeadersMiddleware
from sendspin_bridge.api.routers import (
    auth,
    bluetooth,
    bridge,
    compat_ha,
    devices,
    diagnostics,
    ha_integration,
    music_assistant,
)
from sendspin_bridge.api.routers import config as config_router
from sendspin_bridge.api.spa import mount_spa

API_PREFIX = "/api/v1"


def _session_max_age(config: dict) -> int:
    try:
        hours = int(config.get("SESSION_TIMEOUT_HOURS", 24))
    except (TypeError, ValueError):
        hours = 24
    return int(timedelta(hours=min(168, max(1, hours))).total_seconds())


def create_app(*, config: dict | None = None, spa_dir: Path | None = None, serve_spa: bool = True) -> FastAPI:
    from sendspin_bridge.config import ensure_secret_key, get_runtime_version, load_config

    cfg = config if config is not None else load_config()
    settings = AuthSettings.from_environment(cfg)
    app = FastAPI(
        title="Sendspin Bluetooth Bridge",
        version=get_runtime_version(),
        description="Bridges Music Assistant's Sendspin players to Bluetooth speakers. "
        "One API for the web UI, Home Assistant and automation.",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.auth_settings = settings
    app.state.event_hub = EventHub(auth_enabled=settings.enabled)

    install_error_handlers(app)

    v1 = APIRouter(prefix=API_PREFIX)
    v1.include_router(bridge.public)
    v1.include_router(auth.router)
    v1.include_router(bridge.router)
    v1.include_router(devices.router)
    v1.include_router(bluetooth.router)
    v1.include_router(music_assistant.router)
    v1.include_router(ha_integration.router)
    v1.include_router(config_router.router)
    v1.include_router(diagnostics.router)
    v1.include_router(events_router)
    app.include_router(v1)
    app.include_router(compat_ha.router)
    app.include_router(compat_ha.protected)
    if serve_spa:
        mount_spa(app, spa_dir)

    # Outermost last: ingress sets root_path before anything reads it.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware, ha_addon=settings.ha_addon)
    app.add_middleware(
        SessionMiddleware,
        secret_key=ensure_secret_key(cfg),
        session_cookie="sendspin_session",
        max_age=_session_max_age(cfg),
        same_site="lax",
        https_only=False,
    )
    app.add_middleware(IngressMiddleware)

    return app


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """The event hub lives as long as the server, on the server's loop."""
    app.state.event_hub.start()
    try:
        yield
    finally:
        await app.state.event_hub.stop()
