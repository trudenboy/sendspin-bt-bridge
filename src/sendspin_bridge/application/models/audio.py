"""Local audio outputs (``pulse_sink`` candidates)."""

from __future__ import annotations

from pydantic import Field

from sendspin_bridge.application.models.base import ResponseModel


class AudioSinkOut(ResponseModel):
    name: str = Field(description="PulseAudio/PipeWire sink name.")
    description: str = ""
    state: str = Field(description="running | idle | suspended | unknown")
    sample_spec: str = ""
    muted: bool = False
    volume: int = Field(description="Loudest channel, percent.")
    active_port_available: str | None = Field(
        default=None, description="available | not available | availability unknown, when the sink has ports."
    )
    identifiers: dict[str, str] = Field(
        default_factory=dict, description="Stable hardware identifiers kept as the player's match."
    )
    in_use_by: str | None = Field(default=None, description="Config id of the pulse_sink player using this sink.")
