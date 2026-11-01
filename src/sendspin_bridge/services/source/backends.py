"""Pick the capture backend for the host's audio server."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sendspin_bridge.services.source.capture import CaptureBackend

__all__ = ["backend_for"]


def backend_for(server_name: str) -> CaptureBackend | None:
    """The capture backend for ``server_name``, or ``None`` when the host cannot capture.

    ``SENDSPIN_SOURCE_NODE`` is a debugging override for a fixed PipeWire node; it is read
    only when the host runs PipeWire.
    """
    from sendspin_bridge.services.hardware.capabilities import classify_audio_server

    server = classify_audio_server(server_name)
    if server == "pipewire":
        from sendspin_bridge.services.source.capture.pipewire import PipeWireBackend

        node = os.environ.get("SENDSPIN_SOURCE_NODE") or None
        return PipeWireBackend(fixed_node=node)
    if server == "pulseaudio":
        from sendspin_bridge.services.source.capture.pulse import PulseAudioBackend

        return PulseAudioBackend()
    return None
