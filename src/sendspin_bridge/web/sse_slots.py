"""How many event streams the bridge will hold open at once.

Waitress serves each streaming response from a worker thread, so an
unbounded number of listeners would starve the rest of the web API.  The
budget is shared: the Home Assistant coordinator opens both the status
stream and the event stream, and together they must not eat the pool.

Getting the accounting right is the whole difficulty.  A slot claimed in the
view function and released in the generator's ``finally`` leaks on every
request whose body is never iterated — and Flask registers HEAD for every GET
rule, while Werkzeug discards a HEAD body without touching the generator.
Four HEAD requests from an uptime probe or a link preview were enough to
refuse every later listener for the life of the process.

So the slot is taken *inside* the generator, as a context manager whose exit
is tied to the same object that produced the body.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

__all__ = ["SseSlotPool"]


class SseSlotPool:
    """A bounded budget of concurrent event-stream listeners."""

    def __init__(self, max_slots: int):
        self.max_slots = max_slots
        self._lock = threading.Lock()
        self._in_use = 0

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._in_use

    def has_capacity(self) -> bool:
        """Whether a listener could be admitted right now.

        Only advisory: a view uses it to answer 503 before the body starts,
        because a status code cannot be changed once streaming has begun.
        The claim itself happens later, inside the generator.
        """
        with self._lock:
            return self._in_use < self.max_slots

    @contextlib.contextmanager
    def claim(self, *, label: str = "sse") -> Iterator[bool]:
        """Hold a slot for the duration of the block.

        Yields ``True`` when the slot was granted and ``False`` when the pool
        is full — the caller decides what to send in that case, since by then
        it is already writing a body.
        """
        with self._lock:
            granted = self._in_use < self.max_slots
            if granted:
                self._in_use += 1
        if not granted:
            logger.info("Refusing %s listener: all %d slots in use", label, self.max_slots)
            yield False
            return
        try:
            yield True
        finally:
            with self._lock:
                self._in_use = max(0, self._in_use - 1)

    def reset(self) -> None:
        """Drop all accounting (tests)."""
        with self._lock:
            self._in_use = 0
