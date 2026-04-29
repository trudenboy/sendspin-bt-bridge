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

import sendspin_bridge.bridge.state as _state

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flask_sock import Sock

    from sendspin_bridge.bridge.client import _RingLogHandler

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
    from sendspin_bridge.web.routes.api_status import _build_status_payload

    # Capture the status version BEFORE building the initial snapshot.
    # If a status change lands between snapshot build and version
    # capture, capturing AFTER would silently swallow the change
    # (next ``wait_for_status_change`` would never trigger on a version
    # already past the captured baseline).  Capturing BEFORE means any
    # concurrent change forces an immediate follow-up snapshot — the
    # client may briefly see the old then new state, which is correct.
    last_version = _state.get_status_version()
    yield _build_status_payload()

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

# Per-client queue cap.  A stalled browser tab (slow network, paused
# JS) would otherwise grow the queue without bound under busy logging
# and blow the bridge's memory budget.  Cap matches the ring buffer's
# default size so even under sustained back-pressure the queue holds
# at most one buffer's worth of lines.  When the queue is full, the
# *producer* drops the new line: ``_RingLogHandler.emit`` swallows the
# ``queue.Full`` raised by ``put_nowait``.  The ring buffer covers the
# resulting gap on the client's next reconnect (``snapshot`` frame).
LOG_STREAM_QUEUE_MAXSIZE = 2000


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

    The generator subscribes via :meth:`_RingLogHandler.subscribe_with_snapshot`
    to take an atomic snapshot + register pair (so log lines emitted
    between snapshot and subscribe land in the queue rather than being
    dropped), uses a bounded queue (newest line is dropped at the
    ``put_nowait`` step in the handler when the queue is full — the
    ring buffer covers the gap on the client's next reconnect), and
    unsubscribes via a ``finally`` block — so the handler's subscriber
    list never leaks closed clients (verified by
    ``test_log_stream_iter_unsubscribes_on_completion``).
    """
    bounded_q: _queue.Queue[str] = _queue.Queue(maxsize=LOG_STREAM_QUEUE_MAXSIZE)
    q, snapshot = handler.subscribe_with_snapshot(bounded_q)
    yield {"type": "snapshot", "lines": snapshot}

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
    # ``simple_websocket.ConnectionClosed`` is the expected exception
    # when the browser tab closes — log at debug to avoid log spam.
    # Anything else is a real bug (JSON encoding error, payload build
    # error, etc.) and surfaces via ``logger.exception`` so it shows
    # up in the bug report instead of being swallowed.
    try:
        from simple_websocket import ConnectionClosed as _WsClosed
    except ImportError:  # pragma: no cover — fallback for older deps
        _WsClosed = ()  # type: ignore[assignment]

    @sock.route("/api/status/ws")
    def _api_status_ws(ws):  # type: ignore[no-untyped-def]
        try:
            for payload in status_ws_iter():
                ws.send(json.dumps(payload))
                if payload.get("event") == "session_expired":
                    return
        except _WsClosed as exc:
            logger.debug("/api/status/ws client disconnected: %s", exc)
        except Exception:
            logger.exception("/api/status/ws session ended unexpectedly")

    @sock.route("/api/logs/stream")
    def _api_logs_stream(ws):  # type: ignore[no-untyped-def]
        # Reach into the global ring handler installed by
        # ``sendspin_client``.  Imported lazily to keep ``routes/api_ws``
        # importable on hosts where sendspin_client isn't yet on
        # ``sys.path`` (test rigs, lint tooling).
        try:
            from sendspin_bridge.bridge.client import _ring_log_handler
        except ImportError as exc:
            logger.warning("/api/logs/stream unavailable — ring handler missing: %s", exc)
            return
        # Hold an explicit handle on the generator so we can ``.close()``
        # it in ``finally``: ``ws.send`` raises on client disconnect, and
        # without an explicit close the generator's ``finally`` (which
        # calls ``handler.unsubscribe``) might be deferred until GC,
        # leaking subscribers that emit() then has to fan out to.
        stream = log_stream_iter(_ring_log_handler)
        try:
            for frame in stream:
                ws.send(json.dumps(frame))
                if frame.get("type") == "session_expired":
                    return
        except _WsClosed as exc:
            logger.debug("/api/logs/stream client disconnected: %s", exc)
        except Exception:
            logger.exception("/api/logs/stream session ended unexpectedly")
        finally:
            stream.close()
