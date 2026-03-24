"""PulseAudio volume controller implementing the sendspin VolumeController protocol.

Routes volume/mute commands to a specific PA/PipeWire sink via pulsectl-asyncio,
enabling atomic volume control for Bluetooth speakers.  Used as the
``volume_controller`` argument to ``DaemonArgs`` on sendspin ≥ 5.5.0.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from services.pulse import aget_sink_mute, aget_sink_volume, aset_sink_mute, aset_sink_volume

if TYPE_CHECKING:
    import asyncio
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

    Monitoring is a no-op for now; external volume changes (e.g. physical
    knob on the BT speaker) are not tracked.  This can be extended later
    with a PA subscribe loop if needed.
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
        """Start reporting externally observed state changes (no-op for now)."""
        self._callback = callback

    async def stop_monitoring(self) -> None:
        """Stop monitoring external state changes."""
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        self._callback = None
