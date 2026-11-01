"""Runtime owner of the Sendspin source client: start and stop it without a bridge restart (#563)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_source: Any = None


def running() -> bool:
    """Whether the source client is alive."""
    return _source is not None and bool(getattr(_source, "running", False))


def client() -> Any:
    """The running source client (for pairing commands), or ``None``."""
    return _source


async def start(settings: Any, capture: Any) -> dict[str, Any]:
    """Start the source client; a second call is a no-op."""
    global _source

    if _source is not None:
        return {"started": True, "already": True}
    from sendspin_bridge.services.source.client import SourceClient

    source = SourceClient(settings, capture)
    await source.start()
    _source = source
    return {"started": True, "server": settings.server_url}


async def stop() -> dict[str, Any]:
    """Stop the source client and everything it owns; a second call is a no-op."""
    global _source

    source, _source = _source, None
    if source is None:
        return {"started": False, "already": True}
    return await source.stop()


def reset() -> None:
    """Forget the running client (tests and a cold start that never had one)."""
    global _source
    _source = None
