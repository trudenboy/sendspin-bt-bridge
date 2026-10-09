"""One event stream for every client, over SSE and WebSocket.

Each event is an envelope ``{"v": 1, "type", "at", "data"}``:

* ``status`` — the full ``BridgeStatus`` (bridge, devices, groups), built once
  per change for all listeners and sent to new listeners on connect;
* ``job`` — a ``Job`` whenever one starts, progresses or ends;
* ``log`` — one log line (only to listeners that ask for ``types=log``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from sendspin_bridge.api.auth import auth_settings, is_cross_site, is_trusted_ingress, require_principal
from sendspin_bridge.api.errors import ApiError

logger = logging.getLogger(__name__)

EVENT_VERSION = 1
DEFAULT_TYPES = frozenset({"status", "job"})
ALL_TYPES = frozenset({"status", "job", "log"})
_QUEUE_SIZE = 64
_HEARTBEAT_S = 15.0
_MAX_LIFETIME_S = 1800.0
_MAX_LISTENERS = 16


def envelope(event_type: str, data: Any) -> dict[str, Any]:
    return {"v": EVENT_VERSION, "type": event_type, "at": datetime.now(tz=UTC).isoformat(), "data": data}


class _Listener:
    def __init__(self, types: frozenset[str]) -> None:
        self.types = types
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_SIZE)

    def offer(self, event: dict[str, Any]) -> None:
        if event["type"] not in self.types:
            return
        if self.queue.full():
            # A slow listener loses its oldest event, never blocks the others.
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
        self.queue.put_nowait(event)


class EventHub:
    """Fan-out on the bridge loop. ``publish_threadsafe`` may be called from any thread."""

    def __init__(self, *, auth_enabled: bool) -> None:
        self._auth_enabled = auth_enabled
        self._listeners: set[_Listener] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._status_dirty = asyncio.Event()
        self._last_status: dict[str, Any] | None = None
        self._tasks: list[asyncio.Task] = []
        self._log_unsubscribe = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        from sendspin_bridge.application.jobs import jobs
        from sendspin_bridge.services.lifecycle.bridge_runtime_state import add_status_listener

        self._loop = asyncio.get_running_loop()
        add_status_listener(self._on_status_changed)
        jobs.add_listener(self._on_job)
        self._status_dirty.set()
        self._tasks.append(self._loop.create_task(self._status_producer(), name="events-status"))

    async def stop(self) -> None:
        from sendspin_bridge.application.jobs import jobs
        from sendspin_bridge.services.lifecycle.bridge_runtime_state import remove_status_listener

        remove_status_listener(self._on_status_changed)
        jobs.remove_listener(self._on_job)
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    # -- producers ------------------------------------------------------

    def _on_status_changed(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._status_dirty.set)

    def _on_job(self, job) -> None:
        self.publish_threadsafe(envelope("job", job.model_dump(mode="json")))

    async def _status_producer(self) -> None:
        from sendspin_bridge.application.status import build_status

        loop = asyncio.get_running_loop()
        while True:
            await self._status_dirty.wait()
            self._status_dirty.clear()
            try:
                status = await loop.run_in_executor(None, lambda: build_status(auth_enabled=self._auth_enabled))
            except Exception:
                logger.exception("Status build for the event stream failed")
                await asyncio.sleep(1.0)
                continue
            event = envelope("status", status.model_dump(mode="json"))
            self._last_status = event
            self._broadcast(event)

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._broadcast, event)

    def _broadcast(self, event: dict[str, Any]) -> None:
        for listener in list(self._listeners):
            listener.offer(event)

    # -- consumers ------------------------------------------------------

    @contextlib.asynccontextmanager
    async def listen(self, types: frozenset[str]) -> AsyncIterator[_Listener]:
        if len(self._listeners) >= _MAX_LISTENERS:
            raise ApiError(503, "too_many_listeners", "Too many event listeners")
        listener = _Listener(types)
        if "status" in types and self._last_status is not None:
            listener.offer(self._last_status)
        unsubscribe_log = self._subscribe_logs(listener) if "log" in types else None
        self._listeners.add(listener)
        try:
            yield listener
        finally:
            self._listeners.discard(listener)
            if unsubscribe_log is not None:
                unsubscribe_log()

    def _subscribe_logs(self, listener: _Listener):
        """Live log lines from the in-memory ring handler."""
        from sendspin_bridge.bridge.client import _ring_log_handler

        loop = asyncio.get_running_loop()

        class _Sink:
            def put_nowait(self, line: str) -> None:
                loop.call_soon_threadsafe(listener.offer, envelope("log", {"line": line}))

        sink = _Sink()
        _ring_log_handler.subscribe(sink)
        return lambda: _ring_log_handler.unsubscribe(sink)


def _parse_types(raw: str | None) -> frozenset[str]:
    if not raw:
        return DEFAULT_TYPES
    types = frozenset(t.strip() for t in raw.split(",") if t.strip())
    unknown = types - ALL_TYPES
    if unknown:
        raise ApiError(400, "unknown_event_type", f"Unknown event types: {', '.join(sorted(unknown))}")
    return types


def _check_ws_origin(websocket: WebSocket) -> None:
    """A browser handshake must come from this bridge's own origin (or HA ingress)."""
    if is_trusted_ingress(websocket, auth_settings(websocket)):
        return  # the Supervisor proxied it: Origin is HA's address, Host is ours
    if is_cross_site(websocket):
        raise ApiError(403, "bad_origin", "Cross-origin WebSocket refused")


router = APIRouter(tags=["events"])

_SSE_HEADERS = {
    # no-transform: HA ingress must not deflate the stream (it corrupted frames before).
    "Cache-Control": "no-cache, no-transform",
    "Content-Encoding": "identity",
    "X-Accel-Buffering": "no",
}


def hub_of(app) -> EventHub:
    return app.state.event_hub


@router.get(
    "/events",
    summary="Event stream (SSE)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events"}},
)
async def events_sse(request: Request, types: str | None = Query(default=None, description="status,job,log")):
    require_principal(request)
    wanted = _parse_types(types)
    hub = hub_of(request.app)
    context = hub.listen(wanted)
    listener = await context.__aenter__()

    async def _stream() -> AsyncIterator[str]:
        try:
            # Padding flushes proxy buffers (nginx, HA ingress, Cloudflare).
            yield ": " + " " * 2048 + "\n\n"
            # The stream ends after a while so a client behind a proxy that
            # silently dropped it reconnects; EventSource does so on its own.
            deadline = time.monotonic() + _MAX_LIFETIME_S
            while (remaining := deadline - time.monotonic()) > 0:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(listener.queue.get(), timeout=min(_HEARTBEAT_S, remaining))
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        finally:
            await context.__aexit__(None, None, None)

    return StreamingResponse(_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.websocket("/events/ws")
async def events_ws(websocket: WebSocket, types: str | None = None, csrf: str | None = None) -> None:
    """Browsers pass the session's CSRF token as ``csrf`` (a cross-site page can open
    a WebSocket with the user's cookie, but cannot read the token)."""
    try:
        _check_ws_origin(websocket)
        principal = require_principal(websocket)
        if principal.needs_csrf:
            expected = websocket.session.get("csrf_token", "")
            if not csrf or not expected or not hmac.compare_digest(csrf, expected):
                raise ApiError(403, "csrf_failed", "Missing or stale csrf parameter")
        wanted = _parse_types(types)
    except ApiError as exc:
        await websocket.close(code=4000 + exc.status, reason=exc.code)
        return
    hub = hub_of(websocket.app)
    try:
        async with hub.listen(wanted) as listener:
            await websocket.accept()

            async def _send() -> None:
                while True:
                    await websocket.send_json(await listener.queue.get())

            async def _until_closed() -> None:
                # Clients send nothing; reading is how a close is noticed
                # without waiting for the next event.
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return

            tasks = [asyncio.ensure_future(_send()), asyncio.ensure_future(_until_closed())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                        await task
    except ApiError as exc:
        await websocket.close(code=4000 + exc.status, reason=exc.code)


__all__ = ["EventHub", "auth_settings", "envelope", "router"]
