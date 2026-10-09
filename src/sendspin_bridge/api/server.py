"""Serve the API with uvicorn on the bridge's own event loop.

The bridge owns the process's signal handling (graceful shutdown mutes sinks
first), so the embedded server must not install its own handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Generator
from pathlib import Path

import uvicorn
from fastapi import FastAPI

logger = logging.getLogger(__name__)

_PORT_FILE = Path("/tmp/sendspin-web-port")


class EmbeddedServer(uvicorn.Server):
    """uvicorn without signal capture — the bridge decides when to stop."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


def build_server(app: FastAPI, port: int, *, host: str = "0.0.0.0") -> EmbeddedServer:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        loop="none",
        lifespan="on",
        log_level="warning",
        access_log=False,
        proxy_headers=False,
        # SSE and WebSocket clients are long-lived; shutdown must not hang on them.
        timeout_graceful_shutdown=3,
        ws="websockets-sansio",
    )
    return EmbeddedServer(config)


async def serve_api(app: FastAPI, port: int | None = None) -> EmbeddedServer:
    """Start serving and return the running server (stop it with ``should_exit``)."""
    if port is None:
        from sendspin_bridge.config import resolve_web_port

        port = await asyncio.get_running_loop().run_in_executor(None, resolve_web_port)
    with contextlib.suppress(OSError):
        _PORT_FILE.write_text(str(port))
    server = build_server(app, port)
    task = asyncio.get_running_loop().create_task(server.serve(), name="api-server")
    task.add_done_callback(_log_server_exit)
    server.sendspin_task = task  # type: ignore[attr-defined]
    logger.info("API and web interface listening on port %s", port)
    return server


def _log_server_exit(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("API server stopped: %s", exc)
