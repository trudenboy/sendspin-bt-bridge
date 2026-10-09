"""Reaching the bridge loop from a use case running in a worker thread."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sendspin_bridge.application.errors import UseCaseError

logger = logging.getLogger(__name__)


def bridge_loop() -> asyncio.AbstractEventLoop:
    from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

    loop = get_main_loop()
    if loop is None:
        raise UseCaseError(503, "not_ready", "The bridge runtime is not ready")
    return loop


def run_on_loop(coro, *, timeout: float, description: str = "operation") -> Any:
    """Run *coro* on the bridge loop and wait for its result (worker threads only)."""
    loop = bridge_loop()
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        if asyncio.iscoroutine(coro):
            coro.close()
        raise UseCaseError(503, "not_ready", f"Could not schedule {description}") from exc
    try:
        return future.result(timeout=timeout)
    except TimeoutError as exc:
        future.cancel()
        raise UseCaseError(504, "timeout", f"{description} timed out") from exc


def submit_on_loop(coro, *, description: str) -> bool:
    """Schedule *coro* on the bridge loop without waiting; log a failure when it lands."""
    from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

    loop = get_main_loop()
    if loop is None:
        if asyncio.iscoroutine(coro):
            coro.close()
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        if asyncio.iscoroutine(coro):
            coro.close()
        logger.debug("Could not schedule %s: %s", description, exc)
        return False

    def _log(done) -> None:
        try:
            done.result()
        except Exception as exc:
            logger.debug("%s failed asynchronously: %s", description, exc)

    future.add_done_callback(_log)
    return True
