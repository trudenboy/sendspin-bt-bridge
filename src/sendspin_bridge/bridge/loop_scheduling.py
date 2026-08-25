"""Getting work onto the bridge's event loop from whichever thread is asking.

The bridge runs one asyncio loop, but the code that wants to schedule onto it
runs on three kinds of thread: the loop itself, Waitress workers serving the
web API, and dbus-python callbacks.  A bare ``asyncio.ensure_future`` only
works on the first of those — elsewhere it raises ``RuntimeError`` because
there is no running loop on the calling thread.

``client.py`` spelled out the correct dance three times (fetch the registered
loop, ``run_coroutine_threadsafe``, fall back to ``ensure_future``), and the
one place that skipped it aborted status writes coming from the web API.
This is that dance, once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = logging.getLogger(__name__)

__all__ = ["schedule_on_bridge_loop"]


def schedule_on_bridge_loop(
    coro: Coroutine[Any, Any, Any],
    *,
    description: str = "",
) -> Any | None:
    """Run *coro* on the bridge loop, whatever thread is asking.

    Returns a handle — a ``concurrent.futures.Future`` when scheduled from
    another thread, an ``asyncio.Task`` when scheduled from the loop — or
    ``None`` when there is no loop to schedule onto.

    A missing loop is reported, never raised: startup and shutdown both have
    windows where the bridge has no running loop, and a status update arriving
    in one of them is not a reason to fail the caller.
    """
    from sendspin_bridge.bridge import state as _state

    label = description or getattr(coro, "__qualname__", "coroutine")

    loop = _state.get_main_loop()
    if loop is not None and loop.is_running():
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError as exc:
            # Shutdown can close the loop between the check above and the
            # submission below.  That is the same "no loop to schedule onto"
            # this helper promises to absorb, not an error for the caller.
            logger.debug("Could not schedule %s on the bridge loop: %s", label, exc)
            coro.close()
            return None

    # No registered loop: we may still be *on* one (tests, early startup).
    try:
        return asyncio.ensure_future(coro)
    except RuntimeError as exc:
        logger.debug("Could not schedule %s on the bridge loop: %s", label, exc)
        coro.close()
        return None
