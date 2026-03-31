"""Tests for services.audio_backend — abstract audio backend contract."""

from __future__ import annotations

import pytest

from services.audio_backend import (
    AudioBackend,
    BackendCapability,
    BackendStatus,
    BackendType,
)

# ---------------------------------------------------------------------------
# Concrete test-only implementation
# ---------------------------------------------------------------------------


class _StubBackend(AudioBackend):
    """Minimal concrete backend used by all contract tests."""

    def __init__(
        self,
        backend_type: BackendType = BackendType.BLUETOOTH_A2DP,
        backend_id: str = "stub-001",
    ):
        self._type = backend_type
        self._id = backend_id
        self._connected = False
        self._volume = 50
        self._sink: str | None = None

    @property
    def backend_type(self) -> BackendType:
        return self._type

    @property
    def backend_id(self) -> str:
        return self._id

    def connect(self) -> bool:
        self._connected = True
        self._sink = "alsa_output.stub"
        return True

    def disconnect(self) -> bool:
        self._connected = False
        self._sink = None
        return True

    def is_ready(self) -> bool:
        return self._connected

    def get_audio_destination(self) -> str | None:
        return self._sink

    def set_volume(self, level: int) -> None:
        self._volume = level

    def get_volume(self) -> int | None:
        return self._volume

    def get_status(self) -> BackendStatus:
        return BackendStatus(
            connected=self._connected,
            available=True,
        )

    def get_capabilities(self) -> set[BackendCapability]:
        return {BackendCapability.VOLUME_CONTROL}


# ---------------------------------------------------------------------------
# ABC instantiation
# ---------------------------------------------------------------------------


def test_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        AudioBackend()  # type: ignore[abstract]


def test_concrete_subclass_can_be_instantiated():
    backend = _StubBackend()
    assert isinstance(backend, AudioBackend)


# ---------------------------------------------------------------------------
# BackendCapability enum
# ---------------------------------------------------------------------------


def test_capability_has_volume_control():
    assert BackendCapability.VOLUME_CONTROL == "volume_control"


def test_capability_has_device_discovery():
    assert BackendCapability.DEVICE_DISCOVERY == "device_discovery"


def test_capability_has_battery_reporting():
    assert BackendCapability.BATTERY_REPORTING == "battery_reporting"


def test_capability_has_handoff_support():
    assert BackendCapability.HANDOFF_SUPPORT == "handoff_support"


def test_capability_has_codec_selection():
    assert BackendCapability.CODEC_SELECTION == "codec_selection"


# ---------------------------------------------------------------------------
# BackendStatus dataclass
# ---------------------------------------------------------------------------


def test_status_defaults():
    s = BackendStatus()
    assert s.connected is False
    assert s.available is False
    assert s.error is None
    assert s.battery_level is None
    assert s.extras == {}


def test_status_custom_fields():
    s = BackendStatus(
        connected=True,
        available=True,
        error="timeout",
        battery_level=72,
        extras={"codec": "aac"},
    )
    assert s.connected is True
    assert s.available is True
    assert s.error == "timeout"
    assert s.battery_level == 72
    assert s.extras == {"codec": "aac"}


def test_status_to_dict():
    s = BackendStatus(connected=True, available=True, battery_level=90)
    d = s.to_dict()
    assert d["connected"] is True
    assert d["available"] is True
    assert d["error"] is None
    assert d["battery_level"] == 90
    assert d["extras"] == {}


# ---------------------------------------------------------------------------
# BackendType enum
# ---------------------------------------------------------------------------


def test_backend_type_bluetooth():
    assert BackendType.BLUETOOTH_A2DP == "bluetooth_a2dp"


def test_backend_type_local_sink():
    assert BackendType.LOCAL_SINK == "local_sink"


def test_backend_type_snapcast():
    assert BackendType.SNAPCAST == "snapcast"


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_backend_type_property():
    b = _StubBackend(backend_type=BackendType.LOCAL_SINK)
    assert b.backend_type is BackendType.LOCAL_SINK


def test_backend_id_property():
    b = _StubBackend(backend_id="my-speaker")
    assert b.backend_id == "my-speaker"


# ---------------------------------------------------------------------------
# Abstract method signatures via the stub
# ---------------------------------------------------------------------------


def test_connect_returns_bool():
    b = _StubBackend()
    assert b.connect() is True


def test_disconnect_returns_bool():
    b = _StubBackend()
    b.connect()
    assert b.disconnect() is True


def test_is_ready_reflects_connection():
    b = _StubBackend()
    assert b.is_ready() is False
    b.connect()
    assert b.is_ready() is True


def test_get_audio_destination_none_before_connect():
    b = _StubBackend()
    assert b.get_audio_destination() is None


def test_get_audio_destination_after_connect():
    b = _StubBackend()
    b.connect()
    assert isinstance(b.get_audio_destination(), str)


def test_set_and_get_volume():
    b = _StubBackend()
    b.set_volume(75)
    assert b.get_volume() == 75


def test_get_status_returns_backend_status():
    b = _StubBackend()
    s = b.get_status()
    assert isinstance(s, BackendStatus)


def test_get_capabilities_returns_set():
    b = _StubBackend()
    caps = b.get_capabilities()
    assert isinstance(caps, set)
    assert BackendCapability.VOLUME_CONTROL in caps


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


def test_to_dict_structure():
    b = _StubBackend(
        backend_type=BackendType.BLUETOOTH_A2DP,
        backend_id="bt-abc",
    )
    b.connect()
    d = b.to_dict()

    assert d["backend_type"] == "bluetooth_a2dp"
    assert d["backend_id"] == "bt-abc"
    assert d["connected"] is True
    assert d["available"] is True
    assert d["audio_destination"] == "alsa_output.stub"
    assert "volume_control" in d["capabilities"]
    assert d["error"] is None
    assert d["battery_level"] is None


def test_to_dict_disconnected():
    b = _StubBackend()
    d = b.to_dict()
    assert d["connected"] is False
    assert d["audio_destination"] is None


# ---------------------------------------------------------------------------
# End-to-end lifecycle
# ---------------------------------------------------------------------------


def test_full_lifecycle():
    b = _StubBackend(
        backend_type=BackendType.SNAPCAST,
        backend_id="snap-living-room",
    )
    assert not b.is_ready()
    assert b.get_audio_destination() is None

    assert b.connect() is True
    assert b.is_ready()
    assert b.get_audio_destination() is not None

    b.set_volume(30)
    assert b.get_volume() == 30

    status = b.get_status()
    assert status.connected is True

    assert b.disconnect() is True
    assert not b.is_ready()
    assert b.get_audio_destination() is None
