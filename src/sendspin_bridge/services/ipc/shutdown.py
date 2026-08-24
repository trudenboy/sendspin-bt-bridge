"""Bringing a daemon's tasks down in the right order.

The old sequence cancelled the daemon task and then "waited for a clean
shutdown" with ``wait_for(shield(daemon_task), 3.0)``.  ``shield`` protects an
*outer* await from cancellation; it cannot un-cancel a task cancelled a line
earlier, so the wait returned immediately with ``CancelledError`` and the
step never once did what its comment said.  Every stop was an abrupt
teardown — no goodbye to the server, no PulseAudio drain.

The order that matters: the primary task gets its grace period to finish on
its own, and only an overstay is cancelled.  Observability tasks have nothing
to flush, so they go immediately.  Failures are reported rather than left
unretrieved on a task nobody awaits.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

__all__ = ["shut_down_tasks"]


def _default_on_error(label: str, exc: BaseException) -> None:
    logger.warning("%s task failed during shutdown: %s", label, exc, exc_info=exc)


async def shut_down_tasks(
    *,
    primary: asyncio.Task | asyncio.Future,
    auxiliary: Iterable[asyncio.Task | asyncio.Future],
    grace_s: float,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> None:
    """Stop *primary* gracefully, then *auxiliary* at once.

    *primary* is given *grace_s* seconds to finish by itself; only an
    overstay is cancelled.  Failures from either group are handed to
    *on_error* instead of being left on a task nobody retrieves.
    """
    report = on_error or _default_on_error

    aux = [task for task in auxiliary if task is not None]
    for task in aux:
        task.cancel()

    try:
        await asyncio.wait_for(asyncio.shield(primary), timeout=grace_s)
    except TimeoutError:
        logger.info("Daemon did not finish within %.1fs — cancelling", grace_s)
        primary.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await primary
    except asyncio.CancelledError:
        # Somebody else cancelled it; nothing left to wait for.
        pass
    except Exception as exc:
        report("primary", exc)

    for task in aux:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            report("auxiliary", exc)
