"""Mock audio backend for hardware-free testing and development.

Simulates a fully-functional audio backend without requiring any
real audio hardware.  Useful for:
- CI/CD testing without Bluetooth adapters
- Development on machines without audio output
- Integration testing of the bridge orchestration layer
- Demo mode (MOCK_RUNTIME=true)
"""

from __future__ import annotations

import random
import time

from services.audio_backend import (
    AudioBackend,
    BackendCapability,
    BackendStatus,
    BackendType,
)


class MockAudioBackend(AudioBackend):
    """Simulated audio backend for testing and demo purposes."""

    def __init__(
        self,
        backend_id: str = "mock-default",
        *,
        fail_connect: bool = False,
        connect_latency: float = 0.0,
        failure_rate: float = 0.0,
        backend_type: BackendType = BackendType.LOCAL_SINK,
    ) -> None:
        self._backend_id = backend_id
        self._backend_type = backend_type
        self._fail_connect = fail_connect
        self._connect_latency = connect_latency
        self._failure_rate = failure_rate
        self._connected = False
        self._volume: int = 50
        self._error: str | None = None
        self.call_log: list[str] = []

    @property
    def backend_type(self) -> BackendType:
        return self._backend_type

    @property
    def backend_id(self) -> str:
        return self._backend_id

    def connect(self) -> bool:
        self.call_log.append("connect")
        if self._connect_latency > 0:
            time.sleep(self._connect_latency)
        if self._fail_connect:
            self._error = "Mock connect failure (configured)"
            return False
        if self._failure_rate > 0 and random.random() < self._failure_rate:
            self._error = "Mock random connect failure"
            return False
        self._connected = True
        self._error = None
        return True

    def disconnect(self) -> bool:
        self.call_log.append("disconnect")
        self._connected = False
        self._error = None
        return True

    def is_ready(self) -> bool:
        self.call_log.append("is_ready")
        return self._connected

    def get_audio_destination(self) -> str | None:
        if not self._connected:
            return None
        return f"mock_sink_{self._backend_id}"

    def set_volume(self, level: int) -> None:
        self.call_log.append(f"set_volume:{level}")
        self._volume = max(0, min(100, level))

    def get_volume(self) -> int | None:
        return self._volume

    def get_status(self) -> BackendStatus:
        return BackendStatus(
            connected=self._connected,
            available=self._connected,
            error=self._error,
            battery_level=None,
        )

    def get_capabilities(self) -> set[BackendCapability]:
        return set(BackendCapability)
