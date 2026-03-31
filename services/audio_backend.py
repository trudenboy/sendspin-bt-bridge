"""Abstract audio backend contract for the bridge runtime.

Every audio destination (Bluetooth A2DP, local ALSA/PipeWire sink,
Snapcast, etc.) implements this ABC.  The bridge orchestrator
interacts with backends exclusively through this interface.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class BackendType(str, Enum):
    """Identifier for each concrete backend implementation."""

    BLUETOOTH_A2DP = "bluetooth_a2dp"
    LOCAL_SINK = "local_sink"
    SNAPCAST = "snapcast"


class BackendCapability(str, Enum):
    """Optional features a backend may advertise."""

    VOLUME_CONTROL = "volume_control"
    DEVICE_DISCOVERY = "device_discovery"
    BATTERY_REPORTING = "battery_reporting"
    HANDOFF_SUPPORT = "handoff_support"
    CODEC_SELECTION = "codec_selection"


@dataclass
class BackendStatus:
    """Current operational status of a backend instance."""

    connected: bool = False
    available: bool = False
    error: str | None = None
    battery_level: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AudioBackend(abc.ABC):
    """Abstract base class that every audio backend must implement."""

    @property
    @abc.abstractmethod
    def backend_type(self) -> BackendType:
        """Return the type discriminator for this backend."""

    @property
    @abc.abstractmethod
    def backend_id(self) -> str:
        """Return a unique, stable identifier for this backend instance."""

    @abc.abstractmethod
    def connect(self) -> bool:
        """Establish connection to the audio destination. Returns True on success."""

    @abc.abstractmethod
    def disconnect(self) -> bool:
        """Tear down the connection. Returns True on success."""

    @abc.abstractmethod
    def is_ready(self) -> bool:
        """Return True when the backend is connected and ready to receive audio."""

    @abc.abstractmethod
    def get_audio_destination(self) -> str | None:
        """Return the PulseAudio/PipeWire sink name, or None if not yet available."""

    @abc.abstractmethod
    def set_volume(self, level: int) -> None:
        """Set output volume (0-100)."""

    @abc.abstractmethod
    def get_volume(self) -> int | None:
        """Return current volume (0-100) or None if unknown."""

    @abc.abstractmethod
    def get_status(self) -> BackendStatus:
        """Return the current operational status."""

    @abc.abstractmethod
    def get_capabilities(self) -> set[BackendCapability]:
        """Return the set of capabilities this backend supports."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise backend state for API/status responses."""
        status = self.get_status()
        return {
            "backend_type": self.backend_type.value,
            "backend_id": self.backend_id,
            "connected": status.connected,
            "available": status.available,
            "audio_destination": self.get_audio_destination(),
            "capabilities": sorted(cap.value for cap in self.get_capabilities()),
            "error": status.error,
            "battery_level": status.battery_level,
        }
