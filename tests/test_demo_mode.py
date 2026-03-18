"""Integration-style tests for demo-mode runtime helpers."""

from __future__ import annotations

import asyncio

import pytest

from demo.bt_manager import DemoBluetoothManager
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

    client = FakeClient()
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("demo.simulator.asyncio.sleep", fast_sleep)

    task = asyncio.create_task(run_simulator([client]))
    await real_sleep(0.01)
    task.cancel()
    await task

    assert client.status["track_progress_ms"] > 0
