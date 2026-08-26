"""One connection, many conversations, one place that sorts them out.

The bridge holds a single WebSocket to Music Assistant and carries several
logical conversations over it at once: queue commands, queue polls, player
refreshes, and the stream of events the server pushes on its own.  Every
caller that wanted an answer used to read the socket itself, hunting for its
own ``message_id`` and stashing whatever else turned up on the way.  Three
such hunts existed, with three different bounds — ten messages, twenty,
thirty — and past the bound the answer was dropped: the caller returned an
empty payload and reported failure for a command the server had carried out.

Routing is not a search.  One reader takes each message and hands it to
whoever is waiting for that id, or to the event handler when nobody is.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = ["MessageDispatcher"]


class MessageDispatcher:
    """Routes messages off one socket to the callers waiting for them."""

    def __init__(
        self,
        recv: Callable[[], Awaitable[Any]],
        *,
        on_event: Callable[[str], None],
        on_unclaimed: Callable[[dict], None] | None = None,
    ):
        self._recv = recv
        self._on_event = on_event
        self._on_unclaimed = on_unclaimed
        self._waiting: dict[str, asyncio.Future] = {}

    # -- who is listening ----------------------------------------------

    @property
    def pending(self) -> int:
        """How many callers are waiting for an answer."""
        return len(self._waiting)

    def expect(self, message_id: object) -> asyncio.Future:
        """Register interest in *message_id* before the request goes out."""
        key = str(message_id)
        existing = self._waiting.get(key)
        if existing is not None and not existing.done():
            return existing
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[key] = future
        return future

    def forget(self, message_id: object) -> None:
        """Stop waiting for *message_id* — a caller that gave up leaves nothing."""
        self._waiting.pop(str(message_id), None)

    def fail_all(self, error: BaseException) -> None:
        """The socket is gone: nobody waiting on it can be answered."""
        waiting, self._waiting = self._waiting, {}
        for future in waiting.values():
            if not future.done():
                future.set_exception(error)

    # -- reading -------------------------------------------------------

    async def pump_once(self) -> dict | None:
        """Read one message and give it to whoever it belongs to."""
        raw = await self._recv()
        message = raw if isinstance(raw, dict) else json.loads(raw)
        self.dispatch(message)
        return message

    def dispatch(self, message: dict) -> None:
        """Route one already-decoded message."""
        message_id = message.get("message_id")
        if message_id is not None:
            future = self._waiting.pop(str(message_id), None)
            if future is not None:
                if not future.done():
                    future.set_result(message)
                return
        event = message.get("event")
        if event:
            self._on_event(str(event))
            return
        if self._on_unclaimed is not None:
            self._on_unclaimed(message)
        else:
            logger.debug("MA dispatch: nobody claimed message id=%s", message_id)

    # -- asking --------------------------------------------------------

    async def request(
        self,
        send: Callable[[str], Awaitable[None]],
        *,
        message_id: object,
        timeout: float,
        pump: bool = False,
    ) -> dict:
        """Send a request and wait for its answer.

        With *pump*, this reads the socket while it waits — for the caller
        that owns the connection.  Without it, some other task is pumping and
        this only waits.  Either way the wait ends on the answer or the
        timeout, never on a number of intervening messages.
        """
        key = str(message_id)
        future = self.expect(key)
        try:
            await send(key)
            if not pump:
                return await asyncio.wait_for(future, timeout=timeout)

            async def _pump_until_answered() -> dict:
                while not future.done():
                    await self.pump_once()
                return future.result()

            return await asyncio.wait_for(_pump_until_answered(), timeout=timeout)
        except BaseException:
            self.forget(key)
            raise
