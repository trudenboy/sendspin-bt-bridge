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
