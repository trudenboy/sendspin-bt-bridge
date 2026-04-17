"""Host-side TCP bind probe — find a free port before spawning a daemon.

Complements ``services/sendspin_port_probe.py`` (which does *outbound*
connect-probes against a remote Music Assistant Sendspin server).
Here we need *inbound* bind availability on the local host so the
aiosendspin ``ClientListener`` (aiohttp ``TCPSite``) can bind without
``OSError: [Errno 98] address already in use``.

``SO_REUSEADDR`` is set to match ``aiohttp.web.TCPSite`` defaults so the
probe does not flag a port as free that aiohttp would still fail on.
We deliberately do NOT set ``SO_REUSEPORT``: aiohttp does not set it,
and doing so here would produce false-positive "available" results.
"""

from __future__ import annotations

import logging
import socket

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_MAX_ATTEMPTS", "find_available_bind_port", "is_port_available"]

DEFAULT_MAX_ATTEMPTS = 10


def is_port_available(port: int, *, host: str = "0.0.0.0") -> bool:
    """Return ``True`` if ``(host, port)`` accepts a TCP bind right now.

    Uses a SO_REUSEADDR socket to match aiohttp's bind defaults. A ``True``
    result means the port was free at probe time (TOCTOU still applies to
    any caller that later binds via aiohttp).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def find_available_bind_port(
    start_port: int,
    *,
    host: str = "0.0.0.0",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> int | None:
    """Return the first port in ``[start_port, start_port + max_attempts)`` that
    accepts a bind on *host*, or ``None`` if every candidate is occupied.
    """
    if max_attempts <= 0:
        return None
    for offset in range(max_attempts):
        candidate = start_port + offset
        if is_port_available(candidate, host=host):
            return candidate
    return None
