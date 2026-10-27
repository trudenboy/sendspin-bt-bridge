"""Latency PulseAudio reports for this process' own playback stream."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_CLIENT_NAME = "sendspin-latency"
_logged_failure = False


def _default_factory() -> Any:
    import pulsectl_asyncio  # type: ignore[import-untyped]

    return pulsectl_asyncio.PulseAsync(_CLIENT_NAME)


async def read_own_stream_latency_ms(pid: int, *, pulse_factory: Callable[[], Any] | None = None) -> float | None:
    """buffer_usec + sink_usec of this process' sink-input(s) (max if several), ms; None if none."""
    global _logged_failure
    try:
        factory = pulse_factory or _default_factory
        async with factory() as pulse:
            inputs = await pulse.sink_input_list()
        best: float | None = None
        for si in inputs:
            if str(si.proplist.get("application.process.id")) != str(pid):
                continue
            usec = float(si.buffer_usec or 0) + float(si.sink_usec or 0)
            if best is None or usec > best:
                best = usec
        return None if best is None else best / 1000.0
    except Exception as exc:
        if not _logged_failure:
            _logged_failure = True
            logger.debug("PulseAudio stream latency unavailable: %s", exc)
        return None
