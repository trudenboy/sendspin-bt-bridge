"""PulseAudio volume controller implementing the sendspin VolumeController protocol.

Routes volume/mute commands to a specific PA/PipeWire sink via pulsectl-asyncio,
enabling atomic volume control for Bluetooth speakers.  Used as the
``volume_controller`` argument to ``DaemonArgs`` on sendspin ≥ 5.5.0.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from services.pulse import aget_sink_mute, aget_sink_volume, aset_sink_mute, aset_sink_volume

if TYPE_CHECKING:
    from collections.abc import Callable

    # Callback signature: (volume: int, muted: bool) -> None
    VolumeChangeCallback = Callable[[int, bool], None]

logger = logging.getLogger(__name__)


class PulseVolumeController:
    """VolumeController backed by a PulseAudio/PipeWire sink.

    Implements the four-method protocol expected by sendspin ≥ 5.5.0::

        async def set_state(volume, *, muted) -> None
        async def get_state() -> tuple[int, bool]
        async def start_monitoring(callback) -> None
        async def stop_monitoring() -> None

    ``start_monitoring`` subscribes to PulseAudio sink change events via
    ``pulsectl_asyncio.PulseAsync.subscribe_events('sink')`` so any
    externally-driven volume / mute change (a separate ``pactl`` call,
    a physical knob on the BT speaker, the bridge's own
    ``/api/volume`` direct-pactl path, ...) is pushed to sendspin (and
    thereby to Music Assistant) without waiting for the next periodic
    ``get_state()`` poll.  Echo events from our own ``set_state`` calls
    are suppressed by comparing the new sink state against the values
    we just applied.
    """

    def __init__(self, sink_name: str) -> None:
        self._sink_name = sink_name
        self._volume = 100
        self._muted = False
        self._callback: VolumeChangeCallback | None = None
        self._monitor_task: asyncio.Task | None = None

    async def set_state(self, volume: int, *, muted: bool) -> None:
        """Apply volume and mute state to the PA sink."""
        vol = max(0, min(100, volume))
        ok_vol = await aset_sink_volume(self._sink_name, vol)
        ok_mute = await aset_sink_mute(self._sink_name, muted)
        if ok_vol or ok_mute:
            self._volume = vol
            self._muted = muted
            logger.debug("PA sink %s → vol=%d%% muted=%s", self._sink_name, vol, muted)

    async def get_state(self) -> tuple[int, bool]:
        """Read current volume and mute state from the PA sink."""
        vol = await aget_sink_volume(self._sink_name)
        muted = await aget_sink_mute(self._sink_name)
        if vol is not None:
            self._volume = vol
        if muted is not None:
            self._muted = muted
        return (self._volume, self._muted)

    async def start_monitoring(self, callback: VolumeChangeCallback) -> None:
        """Begin reporting external sink-state changes to *callback*.

        Idempotent: a second call replaces the callback but reuses the
        running subscribe task.  Sendspin invokes this once during
        daemon initialisation.
        """
        self._callback = callback
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(
                self._subscribe_loop(),
                name=f"pa-volume-monitor:{self._sink_name}",
            )

    async def stop_monitoring(self) -> None:
        """Stop the PA event subscription and clear the callback."""
        task = self._monitor_task
        self._monitor_task = None
        self._callback = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _subscribe_loop(self) -> None:
        """Run the pulsectl_asyncio sink-event subscription.

        Each ``change`` event for any sink triggers a fresh ``get_state``
        read — we look up our sink by name on every event so the loop
        is robust to PA renumbering on BT reconnect.  ``new`` / ``remove``
        events are tolerated quietly: ``aget_sink_*`` returns ``None``
        when our sink is gone, which collapses to no-op.

        Echo suppression: ``set_state`` updates ``self._volume`` /
        ``self._muted`` *after* writing to PA.  When PA echoes the
        change back to us via this loop, the read state matches the
        cached values and we skip the callback — preventing a
        sendspin → controller → MA → controller infinite loop.
        """
        # Lazy import: keep ``services.pa_volume_controller`` importable
        # on dev hosts that don't ship ``pulsectl_asyncio``.
        try:
            from services.pulse import _CLIENT_NAME, _PULSECTL_AVAILABLE

            if not _PULSECTL_AVAILABLE:
                logger.debug("PA volume monitor: pulsectl_asyncio unavailable, skipping subscription")
                return
            import pulsectl_asyncio  # type: ignore[import-untyped]
        except Exception as exc:
            logger.debug("PA volume monitor: import failed (%s) — skipping", exc)
            return

        client_name = f"{_CLIENT_NAME}.volctl-{self._sink_name[:32]}"
        while True:
            try:
                async with pulsectl_asyncio.PulseAsync(client_name) as pulse:
                    logger.info(
                        "PA volume monitor: subscribed to sink events for %s",
                        self._sink_name,
                    )
                    async for event in pulse.subscribe_events("sink"):
                        if getattr(event, "facility", None) != "sink":
                            continue
                        # Event index addresses A sink — we read state by NAME
                        # to stay robust to PA index renumbering on BT reconnect.
                        await self._handle_sink_event()
            except asyncio.CancelledError:
                logger.debug("PA volume monitor: cancelled for %s", self._sink_name)
                raise
            except Exception as exc:
                # Subscribe loop drops on PA disconnect or transient errors.
                # Wait briefly and reconnect — never give up so the bridge can
                # heal after pulseaudio restart / BT-stack hiccup.
                logger.warning(
                    "PA volume monitor: subscription dropped for %s (%s) — reconnecting in 2s",
                    self._sink_name,
                    exc,
                )
                await asyncio.sleep(2.0)

    async def _handle_sink_event(self) -> None:
        """Read sink state and fire the callback when it diverges from cache."""
        try:
            new_vol = await aget_sink_volume(self._sink_name)
            new_muted = await aget_sink_mute(self._sink_name)
        except Exception as exc:
            logger.debug("PA volume monitor: get_state failed (%s)", exc)
            return
        if new_vol is None or new_muted is None:
            return  # sink gone or unreachable — wait for next event
        if (new_vol, new_muted) == (self._volume, self._muted):
            return  # echo from our own set_state — suppress
        prev_vol, prev_muted = self._volume, self._muted
        self._volume, self._muted = new_vol, new_muted
        callback = self._callback
        if callback is None:
            return
        logger.info(
            "PA volume monitor: external change on %s → vol=%d%% muted=%s (was vol=%d%% muted=%s)",
            self._sink_name,
            new_vol,
            new_muted,
            prev_vol,
            prev_muted,
        )
        try:
            callback(new_vol, new_muted)
        except Exception as exc:
            logger.warning("VolumeChangeCallback raised: %s", exc)
