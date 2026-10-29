"""The ``air_sensor`` spawn param follows the running air sensor service, not the saved switch.

EXPERIMENTAL_AIR_SENSOR needs a restart: toggled at runtime, a respawned daemon must not build a
sensor no service feeds (switch on, no service yet), nor lose one the service still feeds
(switch off, service still running until the restart).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.bluetooth import air_sensor_service as air_mod

MAC = "6C:5C:3D:35:17:99"


def _config(switch: bool) -> dict:
    return {
        "EXPERIMENTAL_AIR_SENSOR": switch,
        "PLAYOUT_SYNC": "measure",
        "players": [{"id": "eneby", "name": "ENEBY", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}],
    }


async def _spawn(monkeypatch, config: dict) -> tuple[dict, SendspinClient]:
    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
    monkeypatch.setattr("sendspin_bridge.bridge.client.load_config", lambda: json.loads(json.dumps(config)))
    client = SendspinClient("ENEBY", "localhost", 9000)
    client.config_id = "eneby"
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = "bluez_sink.6C_5C_3D_35_17_99.a2dp_sink"
    spawned: list[dict] = []

    async def _fake_exec(*args, **_kw):
        spawned.append(json.loads(args[3]))
        raise RuntimeError("intentional short-circuit")

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=_fake_exec),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client.start_sendspin()
    return spawned[0], client


@pytest.fixture
def service():
    def install(running: bool):  # called inside the test, on its loop
        fake = SimpleNamespace(
            loop=asyncio.get_running_loop(), subscribe=lambda _mac: None, unsubscribe=lambda _mac: None
        )
        air_mod.set_air_sensor_service(fake if running else None)

    yield install
    air_mod.set_air_sensor_service(None)


@pytest.mark.asyncio
async def test_switch_and_service_on_spawns_the_sensor(monkeypatch, service):
    service(True)
    params, _client = await _spawn(monkeypatch, _config(True))
    assert params["air_sensor"]["enabled"] is True


@pytest.mark.asyncio
async def test_switch_turned_on_before_the_restart_spawns_no_sensor(monkeypatch, service):
    service(False)  # saved on, but no service runs until the restart
    params, client = await _spawn(monkeypatch, _config(True))
    assert "air_sensor" not in params
    assert client.status.get("air_sensor") is None  # nothing reported before the restart


@pytest.mark.asyncio
async def test_switch_turned_off_before_the_restart_keeps_the_sensor(monkeypatch, service):
    service(True)  # saved off, the service still runs until the restart
    params, _client = await _spawn(monkeypatch, _config(False))
    assert params["air_sensor"]["enabled"] is True


@pytest.mark.asyncio
async def test_with_the_service_running_a_speaker_not_in_measure_says_why(monkeypatch, service):
    service(True)
    cfg = {**_config(True), "PLAYOUT_SYNC": "off"}
    params, client = await _spawn(monkeypatch, cfg)
    assert "air_sensor" not in params
    assert client.status.get("air_sensor") == {"state": "off", "reason": "needs_measure"}
