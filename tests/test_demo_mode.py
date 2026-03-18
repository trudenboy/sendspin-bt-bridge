"""Integration-style tests for demo-mode runtime helpers."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest
from flask import Flask

from config import VERSION
from demo.bt_manager import DemoBluetoothManager
from demo.fixtures import (
    DEMO_ADAPTERS,
    DEMO_MA_SERVER_INFO,
    DEMO_MA_TOKEN,
    DEMO_MA_URL,
    DEMO_UPDATE_INFO,
    demo_player_id_for_name,
)
from demo.simulator import run_simulator


def test_demo_bluetooth_manager_connect_device_restores_sink_and_volume():
    seen: list[tuple[str, int | None]] = []
    manager = DemoBluetoothManager(
        "AA:BB:CC:DD:EE:02",
        device_name="Kitchen",
        on_sink_found=lambda sink, volume: seen.append((sink, volume)),
    )

    assert manager.connect_device() is True

    assert manager.connected is True
    assert manager.battery_level == 45
    assert seen == [("bluez_output.AA_BB_CC_DD_EE_02.1", 50)]


def test_demo_bluetooth_manager_disconnect_clears_connection_state():
    manager = DemoBluetoothManager("AA:BB:CC:DD:EE:01", device_name="Living Room")

    assert manager.disconnect_device() is True

    assert manager.connected is False
    assert manager.battery_level is None


@pytest.mark.asyncio
async def test_demo_bluetooth_manager_monitor_loop_stops_after_shutdown(monkeypatch):
    manager = DemoBluetoothManager("AA:BB:CC:DD:EE:01", device_name="Living Room")
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("demo.bt_manager.asyncio.sleep", fast_sleep)

    task = asyncio.create_task(manager.monitor_and_reconnect())
    await real_sleep(0.01)
    manager.shutdown()
    await asyncio.wait_for(task, timeout=0.1)


@pytest.mark.asyncio
async def test_demo_simulator_advances_track_progress(monkeypatch):
    progress_updated = asyncio.Event()

    class FakeClient:
        def __init__(self):
            self.player_name = "Living Room"
            self.status = {
                "group_id": None,
                "server_connected": True,
                "bluetooth_connected": True,
                "playing": True,
                "track_progress_ms": 0,
                "track_duration_ms": 10000,
                "battery_level": 80,
                "current_track": "Demo Track",
                "current_artist": "Demo Artist",
            }

        def _update_status(self, updates: dict) -> None:
            self.status.update(updates)
            if self.status.get("track_progress_ms", 0) > 0:
                progress_updated.set()

    client = FakeClient()
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("demo.simulator.asyncio.sleep", fast_sleep)

    task = asyncio.create_task(run_simulator([client]))
    await asyncio.wait_for(progress_updated.wait(), timeout=0.2)
    task.cancel()
    await task

    assert client.status["track_progress_ms"] > 0


@pytest.mark.asyncio
async def test_demo_install_seeds_connected_ma_state_and_named_adapters(monkeypatch):
    import config as config_module
    import demo
    import state

    class StubSendspinClient:
        def __init__(self):
            self.player_name = "Office @ DEMO"
            self.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:04")
            self.status = {}
            self.bluetooth_sink_name = None
            self._daemon_proc = None
            self._daemon_task = None
            self._stderr_task = None

        def _update_status(self, updates: dict) -> None:
            self.status.update(updates)

    monkeypatch.setattr(sys.modules["__main__"], "SendspinClient", StubSendspinClient, raising=False)
    monkeypatch.setattr(sys.modules["__main__"], "BluetoothManager", object, raising=False)

    demo.install()

    config = config_module.load_config()
    assert config["MA_API_URL"] == DEMO_MA_URL
    assert config["MA_API_TOKEN"] == DEMO_MA_TOKEN
    assert [adapter["name"] for adapter in config["BLUETOOTH_ADAPTERS"]] == [
        adapter["name"] for adapter in DEMO_ADAPTERS
    ]

    assert state.get_ma_api_credentials() == (DEMO_MA_URL, DEMO_MA_TOKEN)
    assert state.is_ma_connected() is True
    assert DEMO_MA_SERVER_INFO["version"] == VERSION
    assert state.get_ma_server_version() == DEMO_MA_SERVER_INFO["version"]
    assert state.get_adapter_name(DEMO_ADAPTERS[1]["mac"]) == DEMO_ADAPTERS[1]["name"]
    main_floor = state.get_ma_now_playing_for_group("syncgroup_main_floor")
    assert main_floor["connected"] is True
    assert main_floor["image_url"].startswith("data:image/svg+xml;utf8,")
    assert main_floor["prev_track"]
    assert main_floor["next_track"]
    update_info = state.get_update_available()
    assert update_info is not None
    assert update_info["current_version"] == VERSION
    assert update_info["version"] == DEMO_UPDATE_INFO["version"]
    assert "What's Changed" in update_info["body"]
    assert "album artwork" in update_info["body"]

    client = StubSendspinClient()
    await client._start_sendspin_inner()
    assert client.player_id == demo_player_id_for_name("Office")

    from routes.api_bt import bt_bp
    from routes.api_config import config_bp

    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    app.register_blueprint(config_bp)
    response = app.test_client().get("/api/bt/adapters")
    assert response.status_code == 200
    adapters = response.get_json()["adapters"]
    assert [adapter["name"] for adapter in adapters] == [adapter["name"] for adapter in DEMO_ADAPTERS]
    assert [adapter["mac"] for adapter in adapters] == [adapter["mac"] for adapter in DEMO_ADAPTERS]

    update_response = app.test_client().get("/api/update/info")
    assert update_response.status_code == 200
    update_payload = update_response.get_json()
    assert update_payload["update_available"] is True
    assert update_payload["version"] == DEMO_UPDATE_INFO["version"]
    assert update_payload["body"] == DEMO_UPDATE_INFO["body"]
    assert update_payload["channel"] == "stable"
