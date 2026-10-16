"""The playout settings (web UI / config.json) reach every daemon at spawn."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from sendspin_bridge.bridge.client import SendspinClient


async def _spawn_params(monkeypatch, config: dict) -> dict:
    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
    monkeypatch.setattr("sendspin_bridge.bridge.client.load_config", lambda: dict(config))
    client = SendspinClient("ENEBY Portable", "localhost", 9000)
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
    return spawned[0]


@pytest.mark.asyncio
async def test_defaults_compensate_and_leave_the_audio_clock_alone(monkeypatch):
    params = await _spawn_params(monkeypatch, {})

    assert params["compensate_output_latency"] is True
    assert params["playout_diag"] is False


@pytest.mark.asyncio
async def test_configured_values_reach_the_daemon(monkeypatch):
    params = await _spawn_params(monkeypatch, {"COMPENSATE_OUTPUT_LATENCY": False, "PLAYOUT_DIAG": True})

    assert params["compensate_output_latency"] is False
    assert params["playout_diag"] is True
