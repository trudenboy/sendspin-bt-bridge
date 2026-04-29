"""Helpers for stopping daemon subprocesses and cancelling reader tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class SubprocessStopService:
    """Own reader-task cancellation and graceful daemon stop/kill flow."""

    def __init__(self, *, logger_: logging.Logger | None = None):
        self._logger = logger_ or logging.getLogger(__name__)

    async def cancel_reader_tasks(self, tasks: dict[str, asyncio.Task[Any] | None]) -> dict[str, None]:
        """Cancel and await stdout/stderr reader tasks, returning cleared slots."""
        for task in tasks.values():
            if task and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
        return {name: None for name in tasks}

    async def stop_process(
        self,
        proc,
        *,
        send_stop: Callable[[dict[str, Any]], Awaitable[None]],
        player_name: str,
        reader_tasks: dict[str, asyncio.Task[Any] | None] | None = None,
    ) -> dict[str, None] | None:
        """Request graceful subprocess stop, falling back to kill on timeout.

        If *reader_tasks* is provided, they are cancelled AFTER the stop
        command has been sent and the process has had a brief moment to
        drain its output.  Returns the cleared reader-task dict (or None
        if no reader_tasks were supplied).
        """
        if proc and proc.returncode is None:
            try:
                await send_stop({"cmd": "stop"})
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except TimeoutError:
                self._logger.warning("[%s] Daemon subprocess did not exit, killing", player_name)
                try:
                    proc.kill()
                except ProcessLookupError:
                    self._logger.debug("[%s] Process already exited before kill", player_name)
                await proc.wait()
            except Exception as exc:
                self._logger.debug("stop_sendspin: %s", exc)

        cleared: dict[str, None] | None = None
        if reader_tasks is not None:
            cleared = await self.cancel_reader_tasks(reader_tasks)
        return cleared
