"""Route-facing bridge runtime helpers backed by the shared runtime state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import state

UTC = timezone.utc

if TYPE_CHECKING:
    import asyncio


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    """Return the main asyncio event loop used for bridge-side async work."""
    return state.get_main_loop()


def get_status_version() -> int:
    """Return the current SSE/status version."""
    return state.get_status_version()


def wait_for_status_change(last_version: int, timeout: float = 15) -> tuple[bool, int]:
    """Block until the shared status version changes or timeout expires."""
    return state.wait_for_status_change(last_version, timeout=timeout)


def get_bridge_start_time() -> datetime:
    """Return the bridge process start time."""
    return state.bridge_start_time


def get_bridge_uptime(now: datetime | None = None) -> timedelta:
    """Return bridge uptime as a timedelta."""
    current = now if now is not None else datetime.now(tz=UTC)
    return current - get_bridge_start_time()


def get_bridge_uptime_seconds(now: datetime | None = None) -> float:
    """Return bridge uptime in seconds."""
    return round(get_bridge_uptime(now).total_seconds(), 1)


def get_bridge_uptime_text(now: datetime | None = None) -> str:
    """Return bridge uptime formatted without fractional seconds."""
    return str(get_bridge_uptime(now)).split(".")[0]
