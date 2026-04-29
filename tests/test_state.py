"""Unit tests for state.py module."""

import threading
import uuid

import state
from sendspin_bridge.services.bluetooth.device_registry import set_active_clients


def test_get_status_version_initial():
    version = state.get_status_version()
    assert isinstance(version, int)


def test_wait_times_out():
    # Let any pending debounce timers from previous tests settle
    import time

    time.sleep(0.2)
    version = state.get_status_version()
    changed, current = state.wait_for_status_change(version, timeout=0.15)
    assert changed is False
    assert current == version


def test_notify_increments_version():
    before = state.get_status_version()
    state.notify_status_changed()
    # Use the blocking wait instead of a blind sleep
    changed, after = state.wait_for_status_change(before, timeout=2.0)
    assert changed is True
    assert after > before


def test_wait_detects_change():
    before = state.get_status_version()
    ready = threading.Event()

    def _notify():
        ready.wait(timeout=5)
        state.notify_status_changed()

    t = threading.Thread(target=_notify, daemon=True)
    t.start()
    ready.set()
    changed, current = state.wait_for_status_change(before, timeout=2.0)
    assert changed is True
    assert current > before
    t.join(timeout=2)


def test_create_scan_job():
    job_id = str(uuid.uuid4())
    state.create_scan_job(job_id)
    job = state.get_scan_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert "created" in job


def test_create_scan_job_with_metadata():
    job_id = str(uuid.uuid4())
    state.create_scan_job(job_id, {"scan_options": {"adapter": "", "audio_only": True}})
    job = state.get_scan_job(job_id)
    assert job is not None
    assert job["scan_options"]["audio_only"] is True


def test_create_async_job():
    job_id = str(uuid.uuid4())
    state.create_async_job(job_id, "update-check")
    job = state.get_async_job(job_id)
    assert job is not None
    assert job["status"] == "running"
    assert job["job_type"] == "update-check"


def test_finish_async_job():
    job_id = str(uuid.uuid4())
    state.create_async_job(job_id, "ma-discover")
    result = {"success": True, "servers": [{"url": "http://localhost:8095"}]}
    state.finish_async_job(job_id, result)
    job = state.get_async_job(job_id)
    assert job is not None
    assert job["status"] == "done"
    assert job["success"] is True
    assert job["servers"][0]["url"] == "http://localhost:8095"


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


def test_get_nonexistent_async_job():
    job_id = str(uuid.uuid4())
    assert state.get_async_job(job_id) is None


def test_state_client_aliases_follow_registry_updates():
    client = object()

    set_active_clients([client])

    assert state.get_clients_snapshot() == [client]
    with state.clients_lock:
        assert state.clients == [client]

    set_active_clients([])
    assert state.get_clients_snapshot() == []


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


def test_startup_progress_lifecycle():
    progress = state.reset_startup_progress(4, message="Booting")
    assert progress["status"] == "running"
    assert progress["total_steps"] == 4
    assert progress["percent"] == 0

    progress = state.update_startup_progress(
        "devices",
        "Preparing devices",
        current_step=2,
        details={"active_clients": 3},
    )
    assert progress["phase"] == "devices"
    assert progress["current_step"] == 2
    assert progress["percent"] == 50
    assert progress["details"]["active_clients"] == 3

    progress = state.complete_startup_progress("Ready", details={"active_clients": 3})
    assert progress["status"] == "ready"
    assert progress["phase"] == "ready"
    assert progress["current_step"] == 4
    assert progress["percent"] == 100
    assert progress["completed_at"] is not None


def test_fail_startup_progress_marks_error():
    state.reset_startup_progress(3)
    state.update_startup_progress("web", "Starting web", current_step=2)

    progress = state.fail_startup_progress("Web startup failed", details={"port": 8080})

    assert progress["status"] == "error"
    assert progress["phase"] == "web"
    assert progress["message"] == "Web startup failed"
    assert progress["details"]["port"] == 8080
    assert progress["completed_at"] is not None


def test_set_runtime_mode_info_replaces_metadata():
    info = state.set_runtime_mode_info(
        {
            "mode": "demo",
            "is_mocked": True,
            "simulator_active": True,
            "fixture_devices": 4,
            "mocked_layers": [{"layer": "PulseAudio", "summary": "Mocked"}],
        }
    )

    assert info["mode"] == "demo"
    assert info["is_mocked"] is True
    assert info["fixture_devices"] == 4
    assert info["mocked_layers"][0]["layer"] == "PulseAudio"
    assert info["updated_at"] is not None

    state.set_runtime_mode_info(None)
    reset = state.get_runtime_mode_info()
    assert reset["mode"] == "production"
    assert reset["is_mocked"] is False


# ---------------------------------------------------------------------------
# Music Assistant now-playing state
# ---------------------------------------------------------------------------


def test_set_ma_now_playing_for_group_adds_sync_metadata():
    state.clear_ma_now_playing()

    state.set_ma_now_playing_for_group("syncgroup_1", {"track": "Song", "connected": True})

    result = state.get_ma_now_playing_for_group("syncgroup_1")
    meta = result["_sync_meta"]

    assert result["track"] == "Song"
    assert result["connected"] is True
    assert meta["pending"] is False
    assert meta["pending_ops"] == []
    assert meta["stale"] is False
    assert meta["last_confirmed_at"] is not None
    assert meta["source"] == "direct"


def test_apply_ma_now_playing_prediction_marks_entry_pending_until_confirmed():
    state.clear_ma_now_playing()
    state.set_ma_now_playing_for_group(
        "syncgroup_1",
        {"syncgroup_id": "syncgroup_1", "shuffle_enabled": False, "connected": True},
    )

    predicted = state.apply_ma_now_playing_prediction(
        "syncgroup_1",
        {"shuffle_enabled": True},
        op_id="op-1",
        action="shuffle",
        value=True,
    )

    predicted_meta = predicted["_sync_meta"]
    assert predicted["shuffle_enabled"] is True
    assert predicted_meta["pending"] is True
    assert predicted_meta["pending_ops"][0]["op_id"] == "op-1"
    assert predicted_meta["last_accepted_at"] is not None
    assert predicted_meta["source"] == "predicted"

    state.replace_ma_now_playing(
        {
            "syncgroup_1": {
                "syncgroup_id": "syncgroup_1",
                "shuffle_enabled": True,
                "connected": True,
            }
        }
    )
    confirmed = state.get_ma_now_playing_for_group("syncgroup_1")
    confirmed_meta = confirmed["_sync_meta"]

    assert confirmed["shuffle_enabled"] is True
    assert confirmed_meta["pending"] is False
    assert confirmed_meta["pending_ops"] == []
    assert confirmed_meta["stale"] is False
    assert confirmed_meta["last_accepted_at"] is None
    assert confirmed_meta["source"] == "monitor"
    assert confirmed_meta["last_command_at"] is not None


def test_fail_ma_pending_op_clears_pending_and_sets_error():
    state.clear_ma_now_playing()
    state.set_ma_now_playing_for_group("syncgroup_1", {"syncgroup_id": "syncgroup_1", "repeat_mode": "off"})
    state.apply_ma_now_playing_prediction(
        "syncgroup_1",
        {"repeat_mode": "all"},
        op_id="op-repeat",
        action="repeat",
        value="all",
    )

    failed = state.fail_ma_pending_op("syncgroup_1", "op-repeat", "timeout")
    meta = failed["_sync_meta"]

    assert failed["repeat_mode"] == "all"
    assert meta["pending"] is False
    assert meta["pending_ops"] == []
    assert meta["last_error"] == "timeout"


def test_mark_ma_now_playing_stale_preserves_last_snapshot():
    state.clear_ma_now_playing()
    state.set_ma_now_playing_for_group(
        "syncgroup_1", {"syncgroup_id": "syncgroup_1", "track": "Song", "connected": True}
    )

    state.mark_ma_now_playing_stale("monitor disconnected")

    result = state.get_ma_now_playing_for_group("syncgroup_1")
    meta = result["_sync_meta"]

    assert result["track"] == "Song"
    assert result["connected"] is False
    assert meta["stale"] is True
    assert meta["last_error"] == "monitor disconnected"
    assert meta["source"] == "disconnect"
