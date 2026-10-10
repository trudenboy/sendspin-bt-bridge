"""The HA custom component's endpoints, kept verbatim until it ships on API v1.

Paths, bodies and status codes match the legacy Flask routes exactly; nothing
here is part of API v1 and nothing new should be added (ADR-0001).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from sendspin_bridge.api.auth import require_principal
from sendspin_bridge.api.routers.auth import ha_pair
from sendspin_bridge.application import ha_integration as ha
from sendspin_bridge.application.errors import UseCaseError

router = APIRouter(prefix="/api", include_in_schema=False)
protected = APIRouter(prefix="/api", include_in_schema=False, dependencies=[Depends(require_principal)])


@router.get("/health")
def health() -> dict[str, bool]:
    """Liveness at the pre-v1 address: the image's Docker HEALTHCHECK probes it."""
    return {"ok": True}


_EVENT_SSE_MAX_LIFETIME = 6 * 60 * 60
_EVENT_QUEUE_MAXSIZE = 256
_HEARTBEAT_S = 15.0


@router.post("/auth/ha-pair")
def ha_pair_compat(request: Request) -> JSONResponse:
    """Public: the bootstrap call made before any token exists (gate inside)."""
    try:
        issued = ha_pair(request)
    except UseCaseError:
        return JSONResponse({"success": False, "error": "Not allowed from this network"}, status_code=403)
    return JSONResponse({"success": True, "token": issued.token, "record": issued.record.model_dump()})


@protected.get("/ha/state")
def ha_state() -> JSONResponse:
    try:
        return JSONResponse(ha.state_projection())
    except UseCaseError:
        return JSONResponse({"error": "Failed to build projection"}, status_code=500)


def _command_response(result) -> JSONResponse:
    return JSONResponse(result.to_dict(), status_code=200 if result.success else result.code)


@protected.post("/ha/command")
async def ha_command(request: Request) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool

    data = await _json_object(request)
    player_id = str(data.get("player_id") or "").strip()
    command = str(data.get("command") or "").strip()
    if not player_id:
        return JSONResponse({"success": False, "error": "player_id required"}, status_code=400)
    if not command:
        return JSONResponse({"success": False, "error": "command required"}, status_code=400)
    result = await run_in_threadpool(ha.dispatch_device_command, player_id, command, data.get("value"))
    return _command_response(result)


@protected.post("/ha/command/bridge")
async def ha_command_bridge(request: Request) -> JSONResponse:
    from starlette.concurrency import run_in_threadpool

    data = await _json_object(request)
    command = str(data.get("command") or "").strip()
    if not command:
        return JSONResponse({"success": False, "error": "command required"}, status_code=400)
    result = await run_in_threadpool(ha.dispatch_bridge_command, command, data.get("value"))
    return _command_response(result)


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@protected.get("/status/events")
async def status_events(request: Request) -> StreamingResponse:
    """Typed ``InternalEvent`` records as SSE (``event:<type>`` / ``data:<json>``)."""
    from sendspin_bridge.bridge.state import get_internal_event_publisher

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_EVENT_QUEUE_MAXSIZE)

    def _offer(event: Any) -> None:
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        queue.put_nowait(event)

    def _on_event(event: Any) -> None:
        loop.call_soon_threadsafe(_offer, event)

    unsubscribe = get_internal_event_publisher().subscribe(_on_event)

    async def _stream():
        try:
            yield ": " + " " * 2048 + "\n\n"
            yield 'event: ready\ndata: {"ready": true}\n\n'
            deadline = time.monotonic() + _EVENT_SSE_MAX_LIFETIME
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield 'event: expired\ndata: {"expired": true}\n\n'
                    break
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=min(_HEARTBEAT_S, remaining))
                except TimeoutError:
                    if deadline - time.monotonic() > 0:
                        yield ": heartbeat\n\n"
                    continue
                payload = {
                    "event_type": getattr(event, "event_type", ""),
                    "category": getattr(event, "category", ""),
                    "subject_id": getattr(event, "subject_id", ""),
                    "payload": dict(getattr(event, "payload", {}) or {}),
                    "at": getattr(event, "at", ""),
                }
                yield f"event: {payload['event_type']}\ndata: {json.dumps(payload)}\n\n"
        finally:
            with contextlib.suppress(Exception):
                unsubscribe()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "Content-Encoding": "identity", "X-Accel-Buffering": "no"},
    )
