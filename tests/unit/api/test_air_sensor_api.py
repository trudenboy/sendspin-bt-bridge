"""The daemon's air sensor snapshot in DeviceStatus, the parent snapshot and API v1 (``timing.air_sensor``)."""

from __future__ import annotations

import asyncio
import json
import math
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import sendspin_bridge.application.status as status_uc
from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

MAC = "AA:BB:CC:DD:EE:01"
PLAYER_ID = "kitchen-1"


def _bt_client() -> SimpleNamespace:
    status: dict = {"backend_type": "bluetooth_a2dp", "volume": 100}
    return SimpleNamespace(
        id=PLAYER_ID,
        status=status,
        _status_lock=threading.Lock(),
        _update_status=status.update,
        player_name="Kitchen",
        player_id=PLAYER_ID,
        config_id=PLAYER_ID,
        backend_type="bluetooth_a2dp",
        listen_port=8930,
        server_host="ma.local",
        server_port=9000,
        static_delay_ms=0.0,
        connected_server_url="",
        bt_manager=None,
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )


def _write_config(tmp_config, **extra) -> None:
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {"id": PLAYER_ID, "name": "Kitchen", "backend": {"type": "bluetooth_a2dp", "mac": MAC}},
                ],
                "PLAYER_STATE": {},
                **extra,
            }
        )
    )


@pytest.fixture
def running_device(monkeypatch, tmp_config):
    client = _bt_client()
    monkeypatch.setattr(
        status_uc, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[client])
    )
    _write_config(tmp_config)
    return client


@pytest.fixture
def config_with(tmp_config):
    return lambda **kw: _write_config(tmp_config, **kw)


def _air(api_client):
    resp = api_client.get(f"/api/v1/devices/{PLAYER_ID}")
    assert resp.status_code == 200, resp.text
    return resp.json()["timing"]["air_sensor"]


# -- API ------------------------------------------------------------------------------------


def test_device_timing_carries_air_sensor(api_client, running_device):
    running_device.status.update(
        {
            "air_sensor": {
                "state": "locked",
                "host_placement_ms": 12.4,
                "confidence": 0.8,
                "accepts": [1],
                "unknown_key": 1,
            }
        }
    )
    air = _air(api_client)
    assert air["state"] == "locked" and air["host_placement_ms"] == 12.4 and "unknown_key" not in air
    # The IPC gate is not an API field.
    assert "accepts" not in air


def test_a_full_daemon_snapshot_is_typed_field_by_field(api_client, running_device):
    snap = {
        "state": "locked",
        "reason": None,
        "codec": "sbc",
        "adapter": "C0:FB:F9:62:D6:9D",
        "host_placement_ms": 12.4,
        "air_error_ms": -3.25,
        "air_to_sound_ms": 150.0,
        "calibrated": True,
        "confidence": 0.8,
        "jitter_ms": 0.6,
        "psr": 1.4,
        "lag_cells": 3.5,
        "windows_confirmed": 7,
        "windows_rejected": 2,
        "windows_skipped": 1,
        "last_confirmed_at": "2026-10-28T10:00:00+00:00",
        "epochs": 1,
        "frames": 1200,
        "missing_frames": 3,
        "anchor": "send",
        "complete_lag_ms": 1.5,
        "timestamp": "kernel",
        "monitor_drops": 0,
        "ipc_dropped": 0,
        "dropped_messages": 0,
        "accepts": [1],
        "fold": {"id": "f1", "phase_ms": 2.5, "ok": True, "reason": None},
    }
    running_device.status.update({"air_sensor": snap})
    air = _air(api_client)
    expected = {k: v for k, v in snap.items() if k != "accepts"}
    assert {k: air[k] for k in expected} == expected
    # Every model field is present (defaults included): rate is not in the daemon snapshot.
    assert air["rate"] is None


def test_off_device_reports_the_reason(api_client, running_device):
    running_device.status.update({"air_sensor": {"state": "off", "reason": "needs_measure"}})
    air = _air(api_client)
    assert air == {**air, "state": "off", "reason": "needs_measure"}


def test_malformed_air_sensor_is_dropped(api_client, running_device):
    running_device.status.update({"air_sensor": {"state": 5, "confidence": "high"}})
    assert _air(api_client) is None


def test_switch_off_has_no_air_key_anywhere(api_client, running_device, config_with):
    config_with(EXPERIMENTAL_AIR_SENSOR=False)
    body = api_client.get(f"/api/v1/devices/{PLAYER_ID}").json()
    assert "air_sensor" in body["timing"]
    assert body["timing"]["air_sensor"] is None and running_device.status.get("air_sensor") is None


def test_openapi_lists_the_model(api_client):
    schemas = api_client.get("/api/v1/openapi.json").json()["components"]["schemas"]
    assert "AirSensorTiming" in schemas
    assert "AirSensorFold" in schemas


# -- validation of a semi-trusted daemon value ---------------------------------------------


def _model(raw):
    from sendspin_bridge.application.models.devices import _air_or_none

    return _air_or_none(raw)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "locked",
        {},  # no state
        {"state": None},
        {"state": "locked", "host_placement_ms": "12.4"},  # numbers are numbers, not strings
        {"state": "locked", "calibrated": "yes"},
        {"state": "locked", "windows_confirmed": 1.5},
        {"state": "locked", "frames": True},
        {"state": "locked", "host_placement_ms": math.nan},
        {"state": "locked", "air_error_ms": math.inf},
        {"state": "locked", "fold": "f1"},
        {"state": "locked", "fold": {"ok": "yes"}},
    ],
)
def test_values_of_the_wrong_type_drop_the_whole_value(raw):
    assert _model(raw) is None


def test_values_are_clamped_to_their_ranges():
    air = _model(
        {
            "state": "x" * 500,
            "reason": "r" * 500,
            "confidence": 3.0,
            "windows_confirmed": -4,
            "frames": -1,
            "monitor_drops": -2,
        }
    )
    assert air is not None
    assert len(air.state) <= 64 and len(air.reason or "") <= 64
    assert air.confidence == 1.0
    assert air.windows_confirmed == 0 and air.frames == 0 and air.monitor_drops == 0
    assert _model({"state": "locked", "confidence": -0.5}).confidence == 0.0


def test_status_key_is_accepted_from_the_daemon():
    from sendspin_bridge.bridge.client import _IPC_ALLOWED_KEYS, DeviceStatus

    assert "air_sensor" in _IPC_ALLOWED_KEYS
    assert DeviceStatus().air_sensor is None


# -- the parent side (switch on, the daemon does not run the sensor) -------------------------


async def _spawn(monkeypatch, config: dict, *, preset: dict | None = None) -> SendspinClient:
    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
    monkeypatch.setattr("sendspin_bridge.bridge.client.load_config", lambda: json.loads(json.dumps(config)))
    client = SendspinClient("Kitchen", "localhost", 9000)
    client.config_id = PLAYER_ID
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = "bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink"
    if preset is not None:
        client._update_status({"air_sensor": preset})

    async def _fake_exec(*_args, **_kw):
        raise RuntimeError("intentional short-circuit")

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=_fake_exec),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client.start_sendspin()
    return client


def _player_config(**extra) -> dict:
    return {
        "players": [{"id": PLAYER_ID, "name": "Kitchen", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}],
        **extra,
    }


@pytest.fixture
def running_device_factory(monkeypatch):
    def _make(**cfg):
        return asyncio.run(_spawn(monkeypatch, _player_config(**cfg)))

    return _make


def test_switch_on_but_not_effective_reports_off_with_reason(running_device_factory):
    dev = running_device_factory(EXPERIMENTAL_AIR_SENSOR=True, PLAYOUT_SYNC="off")
    assert dev.status["air_sensor"] == {"state": "off", "reason": "needs_measure"}


def test_switch_on_loop_mode_reports_off_with_its_reason(running_device_factory):
    dev = running_device_factory(EXPERIMENTAL_AIR_SENSOR=True, PLAYOUT_SYNC="loop")
    assert dev.status["air_sensor"] == {"state": "off", "reason": "loop_not_supported_yet"}


def test_switch_off_never_sets_the_status_at_spawn(running_device_factory):
    dev = running_device_factory(EXPERIMENTAL_AIR_SENSOR=False, PLAYOUT_SYNC="measure")
    assert dev.status["air_sensor"] is None


def test_an_effective_speaker_leaves_the_status_to_its_daemon(running_device_factory):
    dev = running_device_factory(EXPERIMENTAL_AIR_SENSOR=True, PLAYOUT_SYNC="measure")
    # No parent-side value: the daemon that builds the sensor publishes the snapshot itself.
    assert dev.status["air_sensor"] is None


@pytest.mark.asyncio
async def test_a_spawn_drops_a_stale_daemon_snapshot(monkeypatch):
    stale = {"state": "locked", "accepts": [1]}
    dev = await _spawn(monkeypatch, _player_config(EXPERIMENTAL_AIR_SENSOR=True, PLAYOUT_SYNC="off"), preset=stale)
    assert dev.status["air_sensor"] == {"state": "off", "reason": "needs_measure"}
    assert dev.air_accepts == ()
