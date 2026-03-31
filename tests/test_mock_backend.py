"""Tests for MockAudioBackend — hardware-free audio backend for testing."""

from __future__ import annotations

import time

from services.audio_backend import (
    AudioBackend,
    BackendCapability,
    BackendStatus,
    BackendType,
)
from services.backends.mock_backend import MockAudioBackend

# ---------------------------------------------------------------------------
# Subclass / identity
# ---------------------------------------------------------------------------


class TestMockBackendIdentity:
    def test_is_audio_backend_subclass(self):
        backend = MockAudioBackend()
        assert isinstance(backend, AudioBackend)

    def test_default_backend_type_is_local_sink(self):
        backend = MockAudioBackend()
        assert backend.backend_type is BackendType.LOCAL_SINK

    def test_custom_backend_type(self):
        backend = MockAudioBackend(backend_type=BackendType.BLUETOOTH_A2DP)
        assert backend.backend_type is BackendType.BLUETOOTH_A2DP

    def test_backend_id_contains_mock(self):
        backend = MockAudioBackend()
        assert "mock" in backend.backend_id

    def test_custom_backend_id(self):
        backend = MockAudioBackend(backend_id="my-test-sink")
        assert backend.backend_id == "my-test-sink"


# ---------------------------------------------------------------------------
# connect / disconnect lifecycle
# ---------------------------------------------------------------------------


class TestMockBackendConnect:
    def test_connect_returns_true_by_default(self):
        backend = MockAudioBackend()
        assert backend.connect() is True

    def test_connect_returns_false_when_fail_connect(self):
        backend = MockAudioBackend(fail_connect=True)
        assert backend.connect() is False

    def test_connect_sets_error_on_failure(self):
        backend = MockAudioBackend(fail_connect=True)
        backend.connect()
        status = backend.get_status()
        assert status.error is not None

    def test_connect_latency_is_respected(self):
        latency = 0.05
        backend = MockAudioBackend(connect_latency=latency)
        t0 = time.monotonic()
        backend.connect()
        elapsed = time.monotonic() - t0
        assert elapsed >= latency * 0.9  # small tolerance

    def test_disconnect_returns_true(self):
        backend = MockAudioBackend()
        backend.connect()
        assert backend.disconnect() is True

    def test_disconnect_resets_connected_state(self):
        backend = MockAudioBackend()
        backend.connect()
        assert backend.is_ready() is True
        backend.disconnect()
        assert backend.is_ready() is False

    def test_disconnect_clears_error(self):
        backend = MockAudioBackend(fail_connect=True)
        backend.connect()
        assert backend.get_status().error is not None
        backend.disconnect()
        assert backend.get_status().error is None


# ---------------------------------------------------------------------------
# is_ready
# ---------------------------------------------------------------------------


class TestMockBackendReady:
    def test_not_ready_before_connect(self):
        backend = MockAudioBackend()
        assert backend.is_ready() is False

    def test_ready_after_connect(self):
        backend = MockAudioBackend()
        backend.connect()
        assert backend.is_ready() is True

    def test_not_ready_after_failed_connect(self):
        backend = MockAudioBackend(fail_connect=True)
        backend.connect()
        assert backend.is_ready() is False


# ---------------------------------------------------------------------------
# audio destination
# ---------------------------------------------------------------------------


class TestMockBackendDestination:
    def test_none_before_connect(self):
        backend = MockAudioBackend()
        assert backend.get_audio_destination() is None

    def test_returns_sink_name_after_connect(self):
        backend = MockAudioBackend(backend_id="spk1")
        backend.connect()
        dest = backend.get_audio_destination()
        assert dest == "mock_sink_spk1"

    def test_none_after_disconnect(self):
        backend = MockAudioBackend()
        backend.connect()
        backend.disconnect()
        assert backend.get_audio_destination() is None


# ---------------------------------------------------------------------------
# volume
# ---------------------------------------------------------------------------


class TestMockBackendVolume:
    def test_default_volume_is_50(self):
        backend = MockAudioBackend()
        assert backend.get_volume() == 50

    def test_set_and_get_volume(self):
        backend = MockAudioBackend()
        backend.set_volume(75)
        assert backend.get_volume() == 75

    def test_volume_clamped_at_100(self):
        backend = MockAudioBackend()
        backend.set_volume(150)
        assert backend.get_volume() == 100

    def test_volume_clamped_at_0(self):
        backend = MockAudioBackend()
        backend.set_volume(-10)
        assert backend.get_volume() == 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


class TestMockBackendStatus:
    def test_status_before_connect(self):
        status = MockAudioBackend().get_status()
        assert isinstance(status, BackendStatus)
        assert status.connected is False
        assert status.available is False
        assert status.error is None

    def test_status_after_connect(self):
        backend = MockAudioBackend()
        backend.connect()
        status = backend.get_status()
        assert status.connected is True
        assert status.available is True
        assert status.error is None

    def test_status_after_failed_connect(self):
        backend = MockAudioBackend(fail_connect=True)
        backend.connect()
        status = backend.get_status()
        assert status.connected is False
        assert status.error is not None


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


class TestMockBackendCapabilities:
    def test_returns_all_capabilities(self):
        backend = MockAudioBackend()
        caps = backend.get_capabilities()
        assert caps == set(BackendCapability)

    def test_capabilities_is_a_set(self):
        backend = MockAudioBackend()
        assert isinstance(backend.get_capabilities(), set)


# ---------------------------------------------------------------------------
# to_dict (concrete method from ABC)
# ---------------------------------------------------------------------------


class TestMockBackendToDict:
    def test_to_dict_returns_dict(self):
        backend = MockAudioBackend(backend_id="dicttest")
        d = backend.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_keys(self):
        backend = MockAudioBackend(backend_id="dicttest")
        backend.connect()
        d = backend.to_dict()
        assert d["backend_type"] == BackendType.LOCAL_SINK.value
        assert d["backend_id"] == "dicttest"
        assert d["connected"] is True
        assert d["audio_destination"] == "mock_sink_dicttest"
        assert "capabilities" in d


# ---------------------------------------------------------------------------
# instance independence
# ---------------------------------------------------------------------------


class TestMockBackendIndependence:
    def test_two_instances_independent_volume(self):
        a = MockAudioBackend(backend_id="a")
        b = MockAudioBackend(backend_id="b")
        a.set_volume(10)
        b.set_volume(90)
        assert a.get_volume() == 10
        assert b.get_volume() == 90

    def test_two_instances_independent_connect(self):
        a = MockAudioBackend(backend_id="a")
        b = MockAudioBackend(backend_id="b")
        a.connect()
        assert a.is_ready() is True
        assert b.is_ready() is False

    def test_two_instances_independent_call_log(self):
        a = MockAudioBackend(backend_id="a")
        b = MockAudioBackend(backend_id="b")
        a.connect()
        b.set_volume(50)
        assert "connect" in a.call_log
        assert "connect" not in b.call_log


# ---------------------------------------------------------------------------
# call_log tracking
# ---------------------------------------------------------------------------


class TestMockBackendCallLog:
    def test_call_log_starts_empty(self):
        backend = MockAudioBackend()
        assert backend.call_log == []

    def test_connect_logged(self):
        backend = MockAudioBackend()
        backend.connect()
        assert "connect" in backend.call_log

    def test_disconnect_logged(self):
        backend = MockAudioBackend()
        backend.disconnect()
        assert "disconnect" in backend.call_log

    def test_is_ready_logged(self):
        backend = MockAudioBackend()
        backend.is_ready()
        assert "is_ready" in backend.call_log

    def test_set_volume_logged_with_level(self):
        backend = MockAudioBackend()
        backend.set_volume(42)
        assert "set_volume:42" in backend.call_log

    def test_multiple_calls_ordered(self):
        backend = MockAudioBackend()
        backend.connect()
        backend.set_volume(80)
        backend.disconnect()
        assert backend.call_log == ["connect", "set_volume:80", "disconnect"]


# ---------------------------------------------------------------------------
# failure_rate (chaos testing)
# ---------------------------------------------------------------------------


class TestMockBackendFailureRate:
    def test_failure_rate_zero_always_succeeds(self):
        backend = MockAudioBackend(failure_rate=0.0)
        results = [backend.connect() for _ in range(50)]
        assert all(results)

    def test_failure_rate_one_always_fails(self):
        backend = MockAudioBackend(failure_rate=1.0)
        results = []
        for _ in range(20):
            results.append(backend.connect())
            backend.disconnect()  # reset state for next attempt
        assert not any(results)

    def test_failure_rate_partial_produces_mix(self):
        """With 50% failure rate over many attempts, expect both successes and failures."""
        backend = MockAudioBackend(failure_rate=0.5)
        results = []
        for _ in range(100):
            results.append(backend.connect())
            backend.disconnect()
        assert any(results), "Expected at least one success"
        assert not all(results), "Expected at least one failure"
