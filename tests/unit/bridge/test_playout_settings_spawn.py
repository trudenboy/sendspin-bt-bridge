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


def test_the_lead_actually_declared_reaches_the_device_api():
    from sendspin_bridge.application.models.devices import DeviceTiming
    from sendspin_bridge.bridge.client import _IPC_ALLOWED_KEYS, DeviceStatus

    assert "declared_lead_time_ms" in _IPC_ALLOWED_KEYS
    assert DeviceStatus().declared_lead_time_ms is None
    assert "declared_lead_time_ms" in DeviceTiming.model_fields


def test_device_timing_carries_the_declared_lead_from_the_status():
    from sendspin_bridge.application.models.devices import Device

    device = Device.from_status({"player_name": "ENEBY Portable", "declared_lead_time_ms": 450})

    assert device.timing.declared_lead_time_ms == 450


@pytest.mark.asyncio
@pytest.mark.parametrize(("config", "mode"), [({}, "off"), ({"PLAYOUT_SYNC": "measure"}, "measure")])
async def test_the_playout_sync_mode_reaches_the_daemon(monkeypatch, config, mode):
    params = await _spawn_params(monkeypatch, config)

    assert params["playout"] == {"mode": mode}


def test_the_daemon_playout_status_passes_the_ipc_allowlist():
    from sendspin_bridge.bridge.client import _IPC_ALLOWED_KEYS

    assert "playout" in _IPC_ALLOWED_KEYS
    assert "effective_required_lead_time_ms" in _IPC_ALLOWED_KEYS
