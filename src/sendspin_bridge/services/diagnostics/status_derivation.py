"""The host probe, on its own clock.

A status payload has two halves that change at different rates and cost
different amounts.  Runtime state — who is connected, what is playing, where
the volume sits — lives in memory and changes many times a second.  The other
half describes the *host*: the Bluetooth controllers, the audio server, D-Bus,
memory.  It changes on the order of seconds and costs subprocesses to find
out.

They were fused: every SSE tick rebuilt both, so the rate of the first drove
the cost of the second.  Measured on a live bridge, ``/api/preflight`` — the
host probe alone — takes ~56 ms of the ~62 ms a full status build takes; the
guidance derived on top of it costs a few milliseconds.  An idle bridge with
one speaker ticks about every six seconds, per connected client, and every
browser tab watching the dashboard multiplied those probes.

This gives the probe its own cadence while everything derived from it stays
as fresh as the tick.  Readers never wait on a probe already in flight: they
are served what was last true, because making them wait would put the cost
straight back on the tick.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["StatusDerivation"]

#: Long enough to collapse a burst of status changes, short enough that an
#: operator watching the dashboard cannot tell.
DEFAULT_MIN_INTERVAL_S = 2.0


class StatusDerivation[T]:
    """Builds *build()* at most once per interval, and never twice at once."""

    def __init__(
        self,
        build: Callable[[], T],
        *,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        label: str = "status derivation",
        wait_timeout_s: float = 10.0,
    ):
        self._build = build
        self._min_interval_s = float(min_interval_s)
        self._clock = clock
        self._label = label
        self._wait_timeout_s = float(wait_timeout_s)
        self._state = threading.Condition()
        self._building = False
        self._value: T | None = None
        self._has_value = False
        self._built_at = 0.0

    def invalidate(self) -> None:
        """Make the next read rebuild, whatever the interval says.

        For the changes an operator makes and expects to see at once — a saved
        config, a speaker added or released.  Costs nothing on its own: the
        rebuild happens when somebody actually reads.
        """
        with self._state:
            self._has_value = False

    def current(self) -> T:
        """The derived half, rebuilt only when it is due.

        Raises whatever *build* raises, but only when there is nothing cached
        to serve instead: once a value exists, a failed rebuild leaves it
        standing rather than blanking the operator's screen.
        """
        with self._state:
            while True:
                if self._building:
                    if self._has_value:
                        # Somebody is already probing the host.  Waiting for
                        # them would put the probe's cost back on this tick.
                        return self._value  # type: ignore[return-value]
                    # Nothing to serve yet, so there is no shortcut: wait for
                    # the build in flight rather than starting a second one.
                    self._state.wait(timeout=self._wait_timeout_s)
                    continue
                if not self._is_due():
                    return self._value  # type: ignore[return-value]
                self._building = True
                break

        try:
            value = self._build()
        except Exception:
            with self._state:
                self._building = False
                self._state.notify_all()
                if self._has_value:
                    logger.warning("%s failed; serving the last known state", self._label, exc_info=True)
                    return self._value  # type: ignore[return-value]
            raise

        with self._state:
            self._value = value
            self._has_value = True
            self._built_at = self._clock()
            self._building = False
            self._state.notify_all()
        return value

    def _is_due(self) -> bool:
        """Caller holds the condition."""
        if not self._has_value:
            return True
        return (self._clock() - self._built_at) >= self._min_interval_s
