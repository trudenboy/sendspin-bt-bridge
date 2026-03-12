"""Unit tests for state.py module."""

import threading
import time
import uuid

import state


def test_get_status_version_initial():
    version = state.get_status_version()
    assert isinstance(version, int)


def test_wait_times_out():
    version = state.get_status_version()
    changed, current = state.wait_for_status_change(version, timeout=0.1)
    assert changed is False
    assert current == version


def test_notify_increments_version():
    before = state.get_status_version()
    state.notify_status_changed()
    time.sleep(0.2)
    after = state.get_status_version()
    assert after > before


def test_wait_detects_change():
    before = state.get_status_version()

    def _notify():
        time.sleep(0.05)
        state.notify_status_changed()

    t = threading.Thread(target=_notify, daemon=True)
    t.start()
    changed, current = state.wait_for_status_change(before, timeout=1.0)
    assert changed is True
    assert current > before
    t.join(timeout=1)


def test_create_scan_job():
    job_id = str(uuid.uuid4())
    state.create_scan_job(job_id)
    job = state.get_scan_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert "created" in job


def test_finish_scan_job():
    job_id = str(uuid.uuid4())
    state.create_scan_job(job_id)
    result = {"devices": ["AA:BB:CC:DD:EE:FF"]}
    state.finish_scan_job(job_id, result)
    job = state.get_scan_job(job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["devices"] == ["AA:BB:CC:DD:EE:FF"]


def test_get_nonexistent_job():
    job_id = str(uuid.uuid4())
    assert state.get_scan_job(job_id) is None


# ---------------------------------------------------------------------------
# Disabled devices
# ---------------------------------------------------------------------------


def test_set_disabled_devices_empty():
    state.set_disabled_devices([])
    assert state.get_disabled_devices() == []


def test_set_disabled_devices():
    devices = [
        {"player_name": "Speaker 1", "mac": "AA:BB:CC:DD:EE:FF", "enabled": False},
        {"player_name": "Speaker 2", "mac": "11:22:33:44:55:66", "enabled": False},
    ]
    state.set_disabled_devices(devices)
    result = state.get_disabled_devices()
    assert len(result) == 2
    assert result[0]["player_name"] == "Speaker 1"
    assert result[1]["mac"] == "11:22:33:44:55:66"


def test_get_disabled_devices_returns_copy():
    """Returned list is a copy — mutations don't affect internal state."""
    state.set_disabled_devices([{"player_name": "X", "mac": "AA:BB:CC:DD:EE:FF"}])
    copy = state.get_disabled_devices()
    copy.clear()
    assert len(state.get_disabled_devices()) == 1


def test_set_disabled_devices_replaces():
    """Calling set_disabled_devices replaces the previous list."""
    state.set_disabled_devices([{"player_name": "A"}])
    state.set_disabled_devices([{"player_name": "B"}])
    result = state.get_disabled_devices()
    assert len(result) == 1
    assert result[0]["player_name"] == "B"
