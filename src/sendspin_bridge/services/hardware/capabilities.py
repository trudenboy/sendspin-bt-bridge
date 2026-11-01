"""What this host can show, and why it cannot show the rest.

A hardware page that renders an empty list where the truth is "this host has no
D-Bus" teaches the operator nothing.  Each capability therefore carries a
stable reason code when it is unavailable, so the UI branches on the code and
never on the wording.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "AUDIO_SERVERS",
    "PULSE_TOOLS_MISSING",
    "Capabilities",
    "Capability",
    "build_capabilities",
    "capture_reason",
    "classify_audio_server",
    "probe_audio_server",
    "probe_capture",
]

AudioServer = Literal["pipewire", "pulseaudio", "none"]

AUDIO_SERVERS: tuple[AudioServer, ...] = ("pipewire", "pulseaudio", "none")

#: Stable reason codes (the API contract; clients branch on these).
BLUETOOTH_UNAVAILABLE = "bluetooth_unavailable"
DBUS_UNAVAILABLE = "dbus_unavailable"
PULSE_UNAVAILABLE = "pulse_unavailable"
PIPEWIRE_REQUIRED = "pipewire_required"
PULSE_TOOLS_MISSING = "pulse_tools_missing"


@dataclass(frozen=True, slots=True)
class Capability:
    """One yes/no capability and the reason it is no."""

    available: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Capabilities:
    """The host's hardware surface, as far as this build can see it."""

    bluetooth: Capability
    dbus: Capability
    audio_server: AudioServer
    capture: Capability
    default_sink: Capability

    @property
    def reasons(self) -> dict[str, str]:
        """Reason code per unavailable capability, keyed by name."""
        out: dict[str, str] = {}
        for name in ("bluetooth", "dbus", "capture", "default_sink"):
            capability: Capability = getattr(self, name)
            if not capability.available and capability.reason:
                out[name] = capability.reason
        if self.audio_server == "none":
            out["audio_server"] = PULSE_UNAVAILABLE
        return out


def classify_audio_server(server_name: str) -> AudioServer:
    """``pipewire``, ``pulseaudio`` or ``none`` from the server's own name.

    PipeWire answers the PulseAudio protocol and names itself, so the
    substring is the discriminator.  Any other answering server is a
    PulseAudio-compatible one; only silence is ``none``.
    """
    text = str(server_name or "").strip()
    lowered = text.lower()
    if "pipewire" in lowered:
        return "pipewire"
    if text and lowered != "not available":
        return "pulseaudio"
    return "none"


def build_capabilities(
    *,
    bluetooth: bool,
    dbus: bool,
    server_name: str,
    capture: bool,
    default_sink: bool,
) -> Capabilities:
    """The capability set from already-probed facts (pure)."""
    audio_server = classify_audio_server(server_name)
    return Capabilities(
        bluetooth=Capability(bluetooth, None if bluetooth else BLUETOOTH_UNAVAILABLE),
        dbus=Capability(dbus, None if dbus else DBUS_UNAVAILABLE),
        audio_server=audio_server,
        capture=Capability(capture, None if capture else capture_reason(audio_server)),
        default_sink=Capability(default_sink, None if default_sink else PULSE_UNAVAILABLE),
    )


def probe_audio_server() -> str:
    """Ask the audio server for its own name (empty when nothing answers)."""
    from sendspin_bridge.services.audio import pulse

    return pulse.get_server_name()


def capture_reason(audio_server: AudioServer) -> str:
    """The stable code for a host that cannot capture the phone, given its audio server."""
    if audio_server == "pipewire":
        return PIPEWIRE_REQUIRED
    if audio_server == "pulseaudio":
        return PULSE_TOOLS_MISSING
    return PULSE_UNAVAILABLE


def probe_capture(server_name: str) -> bool:
    """Whether the host's audio server can expose the phone's capture at all.

    Presence, not activity: a paired phone that is not streaming is still a
    capture-capable host.  PipeWire needs ``wpctl``; PulseAudio needs ``pactl``
    and ``parecord`` (HAOS ships both).
    """
    audio_server = classify_audio_server(server_name)
    if audio_server == "pipewire":
        return shutil.which("wpctl") is not None
    if audio_server == "pulseaudio":
        return shutil.which("pactl") is not None and shutil.which("parecord") is not None
    return False
