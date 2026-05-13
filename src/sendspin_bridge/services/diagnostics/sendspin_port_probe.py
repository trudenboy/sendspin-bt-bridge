"""Probe for the correct Sendspin WebSocket port on a given host.

When the configured Sendspin port doesn't accept connections, this module
attempts to find an alternative port by testing common candidates.

The default port was 9000 historically (legacy bridge default); upstream
Music Assistant now serves the Sendspin WebSocket on 8927.  The probe walks
both so existing configs keep working without manual migration.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8927
# 8927 first (current MA default), then 9000 (legacy bridge default for older
# installs), then 8095 (MA WebSocket API — almost never the right answer but
# kept as a last-resort smell test in case the user mixed the two ports up).
_PROBE_CANDIDATES = (8927, 9000, 8095)
_PROBE_TIMEOUT = 3.0


async def probe_sendspin_port(
    host: str,
    default_port: int = DEFAULT_PORT,
    candidates: tuple[int, ...] | None = None,
    timeout: float = _PROBE_TIMEOUT,
) -> int | None:
    """Try to connect to candidate ports and return the first that accepts TCP.

    Returns the port number on success, or ``None`` if no port responds.
    The *default_port* is always tried first (deduped from candidates).
    """
    if not host:
        return None

    ordered: list[int] = [default_port]
    for port in candidates or _PROBE_CANDIDATES:
        if port not in ordered:
            ordered.append(port)

    for port in ordered:
        if await _tcp_probe(host, port, timeout):
            return port
    return None


async def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    """Return True if *host:port* accepts a TCP connection within *timeout*."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (TimeoutError, OSError):
        return False
