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
