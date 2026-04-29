"""Thread-safety tests for concurrent status, config, and notification operations."""

import json
import threading
from unittest.mock import MagicMock, patch

import sendspin_bridge.bridge.state as state
from sendspin_bridge.config import update_config

# ---------------------------------------------------------------------------
# Concurrent _update_status() calls
# ---------------------------------------------------------------------------


def _make_client_stub():
    """Return a lightweight SendspinClient-like object with _update_status."""
    from sendspin_client import SendspinClient

    bt = MagicMock()
    bt.check_bluetooth_available.return_value = False
    bt.mac_address = "AA:BB:CC:DD:EE:FF"
    with patch("sendspin_client.socket.gethostname", return_value="test-host"):
        client = SendspinClient(
            player_name="ThreadTest",
            server_host="localhost",
            server_port=9000,
            bt_manager=bt,
        )
    return client


@patch("sendspin_client._state")
def test_concurrent_update_status(mock_state):
    """Multiple threads calling _update_status must not corrupt DeviceStatus."""
    mock_state.publish_device_event = MagicMock()
    mock_state.notify_status_changed = MagicMock()
    client = _make_client_stub()
    barrier = threading.Barrier(10)
    errors: list[Exception] = []

    def writer(volume: int):
        try:
            barrier.wait(timeout=5)
            for _ in range(50):
                client._update_status({"volume": volume, "playing": True})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(v,)) for v in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Threads raised: {errors}"
    assert client.status["playing"] is True
    assert isinstance(client.status["volume"], int)
    assert 0 <= client.status["volume"] <= 9


@patch("sendspin_client._state")
def test_update_status_preserves_other_fields(mock_state):
    """_update_status must only change the keys in the updates dict."""
    mock_state.publish_device_event = MagicMock()
    mock_state.notify_status_changed = MagicMock()
    client = _make_client_stub()
    client.status["muted"] = True
    client._update_status({"volume": 42})
    assert client.status["muted"] is True
    assert client.status["volume"] == 42


# ---------------------------------------------------------------------------
# Concurrent update_config() calls
# ---------------------------------------------------------------------------


def test_concurrent_update_config(tmp_config):
    """Concurrent update_config calls must not crash or corrupt the config file.

    update_config serializes reads and writes under separate lock scopes, so
    concurrent mutators may overwrite each other. This test verifies:
    - No exceptions are raised during concurrent access
    - The file always contains valid JSON after all writers finish
    """
    tmp_config.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 1}))
    barrier = threading.Barrier(5)
    errors: list[Exception] = []

    def writer(key: str, value: str):
        try:
            barrier.wait(timeout=5)
            for i in range(10):
                update_config(lambda cfg, k=key, v=f"{value}-{i}": cfg.__setitem__(k, v))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"KEY_{i}", f"val-{i}")) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"Threads raised: {errors}"
    # File must be valid JSON (not corrupted by concurrent writes)
    saved = json.loads(tmp_config.read_text())
    assert isinstance(saved, dict)
    assert "CONFIG_SCHEMA_VERSION" in saved


# ---------------------------------------------------------------------------
# Concurrent notify_status_changed() calls
# ---------------------------------------------------------------------------


def test_concurrent_notify_status_changed():
    """Rapid concurrent notifications must not raise or deadlock."""
    barrier = threading.Barrier(10)
    errors: list[Exception] = []

    def notifier():
        try:
            barrier.wait(timeout=5)
            for _ in range(50):
                state.notify_status_changed()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=notifier) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Threads raised: {errors}"


def test_notify_eventually_increments_version():
    """After concurrent notifications, the version must have advanced."""
    before = state.get_status_version()
    threads = []
    for _ in range(5):
        t = threading.Thread(target=state.notify_status_changed)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)

    changed, after = state.wait_for_status_change(before, timeout=1.0)
    assert changed is True
    assert after > before
