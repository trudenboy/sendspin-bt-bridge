"""WebSocket endpoints for sendspin-bt-bridge (v2.63.0-rc.3).

Replaces the SSE ``/api/status/stream`` endpoint with
``/api/status/ws`` (and adds ``/api/logs/stream`` for the live log
panel).  HA Supervisor's ingress proxy applies deflate compression to
``text/event-stream`` responses, which corrupts SSE payloads on the
browser side; WebSocket frames are not transformed by ingress, so the
WS path is the long-term transport.

The route handlers are intentionally thin — all streaming logic lives
in pure generator functions (``status_ws_iter`` / ``log_stream_iter``)
that are unit-tested without spinning up a real WS connection.  The
handlers loop the generator, JSON-encode each item, call ``ws.send``,
and stop when the generator returns.

flask-sock is wired into the app via ``register_ws_routes(sock)`` so
the Sock instance can be created in ``web_interface.py`` next to the
Flask app initialisation (no module-level singletons).
"""

from __future__ import annotations

import json
import logging
import queue as _queue
import time
from typing import TYPE_CHECKING, Any

import state as _state

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flask_sock import Sock

    from sendspin_client import _RingLogHandler

logger = logging.getLogger(__name__)

# Match the SSE caps so the WS path doesn't silently grow more
# permissive than the path it replaces.
_WS_MAX_LIFETIME_SECONDS = 1800  # 30 minutes
_WS_DEFAULT_CHANGE_TIMEOUT = 15.0


def status_ws_iter(
    *,
    max_iterations: int | None = None,
    change_timeout: float = _WS_DEFAULT_CHANGE_TIMEOUT,
    max_lifetime: float = _WS_MAX_LIFETIME_SECONDS,
) -> Iterator[dict[str, Any]]:
    """Yield status snapshots for a single WS client session.

    First item is always the initial snapshot so clients render right
    away.  Each subsequent item is either:
      * a fresh status snapshot (when ``state.notify_status_changed``
        fired since the last yield), or
      * ``{"event": "heartbeat"}`` (when ``change_timeout`` elapsed
        with no change — keeps proxy + browser connection alive).

    The generator returns when ``max_lifetime`` is exceeded (yielding
    a final ``{"event": "session_expired"}`` so the client can
    reconnect cleanly) or when ``max_iterations`` is reached
    (test-only safety knob).
    """
    # Lazy-import the snapshot builder — pulling routes/api_status at
    # module load time would re-enter ``routes.api_status`` while it is
    # still being initialised by Flask blueprint registration.
    from routes.api_status import _build_status_payload

    yield _build_status_payload()

    last_version = _state.get_status_version()
    started = time.monotonic()
    iterations = 0
    while True:
        if max_iterations is not None and iterations >= max_iterations:
            return
        if time.monotonic() - started >= max_lifetime:
            yield {"event": "session_expired"}
            return
        changed, last_version = _state.wait_for_status_change(last_version, timeout=change_timeout)
        if changed:
            yield _build_status_payload()
        else:
            yield {"event": "heartbeat"}
        iterations += 1


_LOG_STREAM_DEFAULT_IDLE_TIMEOUT = 15.0
_LOG_STREAM_MAX_LIFETIME_SECONDS = 1800  # 30 minutes — match status WS


def log_stream_iter(
    handler: _RingLogHandler,
    *,
    max_iterations: int | None = None,
    idle_timeout: float = _LOG_STREAM_DEFAULT_IDLE_TIMEOUT,
    max_lifetime: float = _LOG_STREAM_MAX_LIFETIME_SECONDS,
) -> Iterator[dict[str, Any]]:
    """Yield live-log frames for a single WS client session.

    Frame protocol:
      * First frame: ``{"type": "snapshot", "lines": [...]}`` — current
        ring contents so the panel renders historical context on
        connect.
      * Subsequent frames: ``{"type": "append", "line": "..."}`` — one
        per ``handler.emit`` call after subscription.
      * On idle timeout: ``{"type": "heartbeat"}`` keeps proxy +
        browser connection warm.

    The generator subscribes a ``queue.Queue`` to *handler*, drains it
    with timeouts, and unsubscribes via a ``finally`` block — so the
    handler's subscriber list never leaks closed clients (verified by
    ``test_log_stream_iter_unsubscribes_on_completion``).
    """
    yield {"type": "snapshot", "lines": handler.snapshot()}

    q: _queue.Queue[str] = _queue.Queue()
    handler.subscribe(q)
    started = time.monotonic()
    iterations = 0
    try:
        while True:
            if max_iterations is not None and iterations >= max_iterations:
                return
            if time.monotonic() - started >= max_lifetime:
                yield {"type": "session_expired"}
                return
            try:
                line = q.get(timeout=idle_timeout)
            except _queue.Empty:
                yield {"type": "heartbeat"}
            else:
                yield {"type": "append", "line": line}
            iterations += 1
    finally:
        handler.unsubscribe(q)


def register_ws_routes(sock: Sock) -> None:
    """Register all WebSocket endpoints on the given flask-sock instance.

    Called once from ``web_interface.py`` after ``Sock(app)`` so the
    routes share Flask's request context but live behind the WS
    upgrade handshake.
    """

    @sock.route("/api/status/ws")
    def _api_status_ws(ws):  # type: ignore[no-untyped-def]
        try:
            for payload in status_ws_iter():
                ws.send(json.dumps(payload))
                if payload.get("event") == "session_expired":
                    return
        except Exception as exc:
            # Common: client closed the tab → simple_websocket raises
            # ConnectionClosed.  Log at debug; an info log per close
            # spams the log panel itself once it goes live.
            logger.debug("/api/status/ws session ended: %s", exc)

    @sock.route("/api/logs/stream")
    def _api_logs_stream(ws):  # type: ignore[no-untyped-def]
        # Reach into the global ring handler installed by
        # ``sendspin_client``.  Imported lazily to keep ``routes/api_ws``
        # importable on hosts where sendspin_client isn't yet on
        # ``sys.path`` (test rigs, lint tooling).
        try:
            from sendspin_client import _ring_log_handler
        except Exception as exc:
            logger.warning("/api/logs/stream unavailable — ring handler missing: %s", exc)
            return
        try:
            for frame in log_stream_iter(_ring_log_handler):
                ws.send(json.dumps(frame))
                if frame.get("type") == "session_expired":
                    return
        except Exception as exc:
            logger.debug("/api/logs/stream session ended: %s", exc)
