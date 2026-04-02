"""PulseAudio sink state monitor for idle disconnect.

Subscribes to PA sink events via ``pulsectl_asyncio`` and tracks
``running ↔ idle/suspended`` transitions for registered Bluetooth sinks.
Runs in the parent process on the main asyncio event loop — one subscription
covers all devices.

When pulsectl is unavailable the monitor degrades gracefully: ``start()``
becomes a no-op and ``available`` stays ``False`` so callers can fall back
to daemon-flag-based idle detection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

from services.pulse import _PULSECTL_AVAILABLE

if TYPE_CHECKING:
    from collections.abc import Callable

if _PULSECTL_AVAILABLE:
    import pulsectl_asyncio  # type: ignore[import-untyped]


logger = logging.getLogger(__name__)

__all__ = ["SinkMonitor", "extract_mac_from_sink"]

# PA sink state constants (from <pulse/def.h>).
_PA_SINK_RUNNING = 0
_PA_SINK_IDLE = 1
_PA_SINK_SUSPENDED = 2

# Regex: extract the 17-char underscore-delimited MAC from a bluez sink name.
_BLUEZ_MAC_RE = re.compile(r"^bluez_(?:sink|output)\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})")

_RECONNECT_DELAY = 5.0  # seconds between PA reconnect attempts


def extract_mac_from_sink(sink_name: str) -> str | None:
    """Extract a colon-delimited MAC address from a bluez sink name.

    Supported patterns::

        bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink
        bluez_sink.FC_58_FA_EB_08_6C
        bluez_output.FC_58_FA_EB_08_6C.a2dp-sink
        bluez_output.FC_58_FA_EB_08_6C.1

    Returns ``None`` for non-bluez or malformed names.
    """
    m = _BLUEZ_MAC_RE.match(sink_name)
    if not m:
        return None
    return m.group(1).replace("_", ":").upper()


class SinkMonitor:
    """Watches PA sink state transitions and dispatches per-device callbacks.

    Usage::

        monitor = SinkMonitor()
        monitor.register(mac, sink_name, on_active_cb, on_idle_cb)
        await monitor.start()
        ...
        await monitor.stop()
    """

    def __init__(self) -> None:
        self._callbacks: dict[str, tuple[Callable[[], None], Callable[[], None]]] = {}
        self._sink_names: dict[str, str] = {}  # MAC → sink_name
        self._sink_states: dict[str, str] = {}  # sink_name → "running"|"idle"|"suspended"
        self._sink_index_to_name: dict[int, str] = {}  # PA index → sink_name
        self._task: asyncio.Task[None] | None = None

    # ── Public API ────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True when the monitor loop is actively running."""
        return self._task is not None and not self._task.done()

    def register(
        self,
        mac: str,
        sink_name: str,
        on_active: Callable[[], None],
        on_idle: Callable[[], None],
    ) -> None:
        """Register callbacks for a device's sink state transitions.

        *on_active* is called when the sink enters ``running``.
        *on_idle* is called when the sink leaves ``running`` (→ idle/suspended/removed).
        """
        # Clean up old sink name mapping if MAC was already registered
        old_sink = self._sink_names.get(mac)
        if old_sink and old_sink != sink_name:
            self._sink_states.pop(old_sink, None)
        self._callbacks[mac] = (on_active, on_idle)
        self._sink_names[mac] = sink_name

    def unregister(self, mac: str) -> None:
        """Remove all state tracking for *mac*."""
        self._callbacks.pop(mac, None)
        sink = self._sink_names.pop(mac, None)
        if sink:
            self._sink_states.pop(sink, None)

    async def start(self) -> None:
        """Start the PA event subscription loop as a background task."""
        if not _PULSECTL_AVAILABLE:
            logger.warning("SinkMonitor: pulsectl unavailable — sink state monitoring disabled")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Cancel the monitor task and clean up."""
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ── Event loop ────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """Persistent subscribe loop with automatic reconnect."""
        while True:
            try:
                async with pulsectl_asyncio.PulseAsync("sendspin-sink-monitor") as pulse:
                    logger.info("SinkMonitor: connected to PulseAudio — subscribing to sink events")
                    async for event in pulse.subscribe_events("sink"):
                        ev_type = str(event.t)
                        if ev_type == "change" or ev_type == "new":
                            await self._handle_sink_change(pulse, event.index)
                        elif ev_type == "remove":
                            self._handle_sink_remove(event.index)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "SinkMonitor: PA connection lost (%s) — reconnecting in %.0fs",
                    exc,
                    _RECONNECT_DELAY,
                )
                self._sink_index_to_name.clear()
                await asyncio.sleep(_RECONNECT_DELAY)

    # ── Event handlers ────────────────────────────────────────────────

    async def _handle_sink_change(self, pulse: Any, sink_index: int) -> None:
        """Query the sink and fire callbacks on state transitions."""
        try:
            sink_info = await pulse.sink_info(sink_index)  # type: ignore[union-attr]
        except Exception:
            return

        sink_name: str = sink_info.name
        self._sink_index_to_name[sink_index] = sink_name

        # Find registered MAC for this sink name
        mac = self._find_mac_for_sink(sink_name)
        if mac is None:
            return

        new_state = self._classify_state(sink_info.state)
        old_state = self._sink_states.get(sink_name)

        if new_state == old_state:
            return

        self._sink_states[sink_name] = new_state
        on_active, on_idle = self._callbacks.get(mac, (None, None))
        if on_active is None:
            return

        if new_state == "running" and old_state != "running":
            logger.debug("SinkMonitor: %s [%s] → running", sink_name, mac)
            on_active()
        elif new_state in ("idle", "suspended") and old_state in ("running", None):
            logger.debug("SinkMonitor: %s [%s] → %s", sink_name, mac, new_state)
            on_idle()  # type: ignore[misc]

    def _handle_sink_remove(self, sink_index: int) -> None:
        """Handle sink removal — treat as idle if it was running."""
        sink_name = self._sink_index_to_name.pop(sink_index, None)
        if sink_name is None:
            return

        old_state = self._sink_states.pop(sink_name, None)
        mac = self._find_mac_for_sink(sink_name)
        if mac is None or old_state != "running":
            return

        on_active, on_idle = self._callbacks.get(mac, (None, None))
        if on_idle is not None:
            logger.debug("SinkMonitor: %s [%s] removed while running — treating as idle", sink_name, mac)
            on_idle()

    # ── Helpers ───────────────────────────────────────────────────────

    def _find_mac_for_sink(self, sink_name: str) -> str | None:
        """Reverse lookup: sink_name → registered MAC."""
        for mac, name in self._sink_names.items():
            if name == sink_name:
                return mac
        return None

    @staticmethod
    def _classify_state(state: int) -> str:
        """Map PA sink state integer to a string label."""
        if state == _PA_SINK_RUNNING:
            return "running"
        if state == _PA_SINK_IDLE:
            return "idle"
        if state == _PA_SINK_SUSPENDED:
            return "suspended"
        return "unknown"
