from __future__ import annotations

import asyncio

import pytest

import state
from services.lifecycle_state import BridgeLifecycleState


@pytest.fixture(autouse=True)
def _reset_shared_state():
    state.reset_startup_progress()
    state.set_runtime_mode_info(None)
    state.set_clients([])
    state.set_disabled_devices([])
    state.set_main_loop(None)
    state.set_ma_api_credentials("", "")
    state.set_ma_groups({}, [])
    state.set_ma_connected(False)


def test_begin_startup_sets_runtime_mode_and_progress():
    lifecycle_state = BridgeLifecycleState(startup_steps=7)

    lifecycle_state.begin_startup(demo_mode=True)

    progress = state.get_startup_progress()
    runtime_info = state.get_runtime_mode_info()
    assert progress["status"] == "running"
    assert progress["phase"] == "config"
    assert progress["total_steps"] == 7
    assert progress["details"]["demo_mode"] is True
    assert runtime_info["mode"] == "demo"
    assert runtime_info["is_mocked"] is True
    assert runtime_info["simulator_active"] is True


def test_publish_ma_integration_sets_credentials_groups_and_progress():
    lifecycle_state = BridgeLifecycleState()

    lifecycle_state.publish_ma_integration(
        ma_api_url="http://ma.local:8095",
        ma_api_token="token",
        groups_loaded=True,
        name_map={"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen Group"}},
        all_groups=[{"id": "syncgroup_1", "name": "Kitchen Group", "members": []}],
        monitor_enabled=True,
    )

    progress = state.get_startup_progress()
    url, token = state.get_ma_api_credentials()
    groups = state.get_ma_groups()
    assert url == "http://ma.local:8095"
    assert token == "token"
    assert groups[0]["name"] == "Kitchen Group"
    assert state.get_ma_group_for_player_id("sendspin-kitchen")["id"] == "syncgroup_1"
    assert state.is_ma_connected() is True
    assert progress["phase"] == "integrations"
    assert progress["details"]["ma_configured"] is True
    assert progress["details"]["ma_monitor_enabled"] is True


def test_publish_main_loop_and_complete_startup_updates_shared_progress():
    lifecycle_state = BridgeLifecycleState()
    loop = asyncio.new_event_loop()
    try:
        lifecycle_state.publish_main_loop(loop, web_thread_name="WebServer")
        lifecycle_state.complete_startup(
            active_clients=[object(), object()],
            demo_mode=False,
            monitor_enabled=False,
        )

        progress = state.get_startup_progress()
        assert state.get_main_loop() is loop
        assert progress["status"] == "ready"
        assert progress["phase"] == "ready"
        assert progress["details"]["active_clients"] == 2
        assert progress["details"]["demo_mode"] is False
    finally:
        loop.close()


def test_publish_startup_failure_marks_phase_and_error_details():
    lifecycle_state = BridgeLifecycleState()

    lifecycle_state.publish_startup_failure("Failed to boot web", phase="web", details={"error_type": "RuntimeError"})

    progress = state.get_startup_progress()
    assert progress["status"] == "error"
    assert progress["phase"] == "idle"
    assert progress["message"] == "Failed to boot web"
    assert progress["details"]["startup_phase"] == "web"
    assert progress["details"]["error_type"] == "RuntimeError"


def test_publish_shutdown_updates_progress_and_clears_main_loop():
    lifecycle_state = BridgeLifecycleState(startup_steps=6)
    loop = asyncio.new_event_loop()
    try:
        state.set_main_loop(loop)
        lifecycle_state.publish_shutdown_started(active_clients=2)
        progress = state.get_startup_progress()
        assert progress["phase"] == "shutdown"
        assert progress["status"] == "stopping"
        assert progress["details"]["active_clients"] == 2

        lifecycle_state.publish_shutdown_complete(stopped_clients=2)
        progress = state.get_startup_progress()
        assert progress["phase"] == "shutdown"
        assert progress["status"] == "stopped"
        assert progress["details"]["stopped_clients"] == 2
        assert state.get_main_loop() is None
    finally:
        loop.close()


def test_lifecycle_state_publishes_bridge_events(monkeypatch):
    lifecycle_state = BridgeLifecycleState()
    events = []
    monkeypatch.setattr(
        state, "publish_bridge_event", lambda event_type, payload=None: events.append((event_type, payload))
    )

    lifecycle_state.begin_startup(demo_mode=False)
    lifecycle_state.complete_startup(active_clients=[object()], demo_mode=False, monitor_enabled=True)
    lifecycle_state.publish_shutdown_started(active_clients=1)
    lifecycle_state.publish_shutdown_complete(stopped_clients=1)

    assert [event_type for event_type, _payload in events] == [
        "bridge.startup.started",
        "bridge.startup.completed",
        "bridge.shutdown.started",
        "bridge.shutdown.completed",
    ]
