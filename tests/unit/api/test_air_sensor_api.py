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
from tests.support.air_service import bridge_started_with

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
    bridge_started_with(monkeypatch, config)
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


def test_switch_on_loop_mode_gets_no_off_status_from_the_parent(running_device_factory):
    dev = running_device_factory(EXPERIMENTAL_AIR_SENSOR=True, PLAYOUT_SYNC="loop")
    assert dev.status["air_sensor"] is None


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


# -- calibration at spawn (A15) ----------------------------------------------------------------

AIR_ENTRY = {
    "codec": "sbc",
    "rate": 44100,
    "adapter": "C0:FB:F9:62:D7:D6",
    "latency_ms": 203.4,
    "measured_at": "2026-10-28T09:00:00+00:00",
}


async def _spawn_params(monkeypatch, config: dict, *, preset: dict | None = None) -> tuple[SendspinClient, dict]:
    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
    monkeypatch.setattr("sendspin_bridge.bridge.client.load_config", lambda: json.loads(json.dumps(config)))
    bridge_started_with(monkeypatch, config)
    client = SendspinClient("Kitchen", "localhost", 9000)
    client.config_id = PLAYER_ID
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = "bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink"
    if preset is not None:
        client._update_status({"air_sensor": preset})
    seen: dict = {}

    async def _fake_exec(*args, **_kw):
        seen.update(json.loads(args[3]))
        raise RuntimeError("intentional short-circuit")

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=_fake_exec),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client.start_sendspin()
    return client, seen


def _calibrated_config(**extra) -> dict:
    cfg = _player_config(EXPERIMENTAL_AIR_SENSOR=True, PLAYOUT_SYNC="measure", **extra)
    cfg["players"][0]["air_to_sound_latency"] = [AIR_ENTRY]
    return cfg


@pytest.mark.asyncio
async def test_spawn_carries_the_calibration_for_the_last_seen_codec_rate_and_adapter(monkeypatch):
    previous = {"state": "locked", "accepts": [1], "codec": "sbc", "rate": 44100, "adapter": "C0:FB:F9:62:D7:D6"}
    client, params = await _spawn_params(monkeypatch, _calibrated_config(), preset=previous)
    assert params["air_sensor"] == {"enabled": True, "latency_ms": 203.4}
    assert client.air_to_sound_latency == [AIR_ENTRY]


@pytest.mark.asyncio
async def test_spawn_without_a_known_rate_is_uncalibrated(monkeypatch):
    _client, params = await _spawn_params(monkeypatch, _calibrated_config())
    assert params["air_sensor"] == {"enabled": True, "latency_ms": None}


@pytest.mark.asyncio
async def test_switch_off_spawn_has_no_air_param_even_with_a_calibration(monkeypatch):
    cfg = _calibrated_config()
    cfg["EXPERIMENTAL_AIR_SENSOR"] = False
    previous = {"state": "locked", "accepts": [1], "codec": "sbc", "rate": 44100, "adapter": "C0:FB:F9:62:D7:D6"}
    _client, params = await _spawn_params(monkeypatch, cfg, preset=previous)
    assert "air_sensor" not in params


# -- calibration storage and the Forget endpoints (A15) ------------------------------------------
#
# A real SendspinClient with a fake daemon process (its stdin records each line), the real
# DELETE use case, config diff and reconfiguration orchestrator, and the bridge loop on a thread.

AIR_ADAPTER = "C0:FB:F9:62:D7:D6"


class _RecordingStdin:
    class _Transport:
        def get_write_buffer_size(self):
            return 0

        def is_closing(self):
            return False

    def __init__(self, sink):
        self.transport = self._Transport()
        self._sink = sink

    def write(self, data):
        self._sink.append(data)

    async def drain(self):
        return None


class _FakeDaemon:
    def __init__(self, sink):
        self.returncode = None
        self.pid = 4242
        self.stdin = _RecordingStdin(sink)


@pytest.fixture
def calibrated_bridge(monkeypatch, tmp_config):
    """Build a running calibrated speaker; ``switch`` False leaves the air sensor off."""
    from sendspin_bridge.application import devices as devices_uc
    from sendspin_bridge.config.players import player_id_for_mac
    from sendspin_bridge.services.bluetooth import air_sensor_service as air_mod
    from sendspin_bridge.services.bluetooth import device_registry
    from sendspin_bridge.services.lifecycle import bridge_runtime_state

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def build(*, switch: bool = True, entries: list | None = None, learned: list | None = None):
        entries = [AIR_ENTRY, {**AIR_ENTRY, "rate": 48000, "latency_ms": 150.0}] if entries is None else entries
        pid = player_id_for_mac(MAC)
        tmp_config.write_text(
            json.dumps(
                {
                    "CONFIG_SCHEMA_VERSION": 6,
                    "EXPERIMENTAL_AIR_SENSOR": switch,
                    "PLAYOUT_SYNC": "measure",
                    "players": [
                        {
                            "id": pid,
                            "name": "Kitchen",
                            "backend": {"type": "bluetooth_a2dp", "mac": MAC},
                            "air_to_sound_latency": entries,
                            **({"air_learned_latency": learned} if learned else {}),
                        }
                    ],
                    "PLAYER_STATE": {},
                }
            )
        )
        writes: list[bytes] = []
        bt = SimpleNamespace(
            mac_address=MAC,
            connected=False,
            check_bluetooth_available=lambda: True,
            effective_adapter_mac=AIR_ADAPTER,
        )
        client = SendspinClient("Kitchen", "localhost", 9000, bt_manager=bt)
        client.config_id = pid
        client.air_to_sound_latency = list(entries)
        client.air_learned_latency = list(learned or [])
        snapshot = DeviceRegistrySnapshot(active_clients=[client])
        monkeypatch.setattr(status_uc, "get_device_registry_snapshot", lambda: snapshot)
        monkeypatch.setattr(device_registry, "get_device_registry_snapshot", lambda: snapshot)
        monkeypatch.setattr(bridge_runtime_state, "get_main_loop", lambda: loop)
        monkeypatch.setattr(devices_uc, "_sync_ha_options_later", lambda: None)
        if switch:
            service = SimpleNamespace(loop=loop, subscribe=lambda mac: None, unsubscribe=lambda mac: None)
            air_mod.set_air_sensor_service(service)

        async def _start():
            client._daemon_proc = _FakeDaemon(writes)
            client._update_status(
                {
                    "air_sensor": {
                        "state": "locked",
                        "accepts": [1],
                        "codec": "sbc",
                        "rate": 44100,
                        "adapter": AIR_ADAPTER,
                    }
                }
            )
            await asyncio.sleep(0)

        asyncio.run_coroutine_threadsafe(_start(), loop).result(timeout=5)
        dev = SimpleNamespace(
            id=client.player_id,
            client=client,
            sent=lambda: [json.loads(line) for line in writes],
            stored=lambda: json.loads(tmp_config.read_text())["players"][0].get("air_to_sound_latency"),
            stored_learned=lambda: json.loads(tmp_config.read_text())["players"][0].get("air_learned_latency"),
        )
        return dev

    yield build
    air_mod.set_air_sensor_service(None)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def _cal(latency):
    return {"cmd": "set_air_calibration", "v": 1, "latency_ms": latency, "protocol_version": 2}


def test_device_audio_lists_the_stored_calibrations(api_client, calibrated_bridge):
    dev = calibrated_bridge()
    audio = api_client.get(f"/api/v1/devices/{dev.id}").json()["audio"]
    assert audio["air_to_sound_latency"] == [
        {**AIR_ENTRY, "spread_ms": None, "reference": "", "source": "sync_wizard", "d_ref_ms": 0.0},
        {
            **AIR_ENTRY,
            "rate": 48000,
            "latency_ms": 150.0,
            "spread_ms": None,
            "reference": "",
            "source": "sync_wizard",
            "d_ref_ms": 0.0,
        },
    ]


def test_forget_one_calibration(api_client, calibrated_bridge):
    dev = calibrated_bridge()
    assert dev.sent()[-1] == _cal(203.4)
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/44100/{AIR_ADAPTER}")
    assert r.status_code == 204, r.text
    entries = api_client.get(f"/api/v1/devices/{dev.id}").json()["audio"]["air_to_sound_latency"]
    assert [(e["rate"], e["latency_ms"]) for e in entries] == [(48000, 150.0)]
    assert [(e["rate"], e["latency_ms"]) for e in dev.stored()] == [(48000, 150.0)]
    # The running daemon plays 44.1 kHz: no entry matches any more, applied live.
    assert dev.sent()[-1] == _cal(None)


def _learned_entry(rate=44100, samples=(228.0, 230.0), delays=(184.0, 190.0)):
    from sendspin_bridge.services.audio.playout.air_learned import learned_reference

    latency, d_ref = learned_reference(list(samples), list(delays))
    return {
        "codec": "sbc",
        "rate": rate,
        "adapter": AIR_ADAPTER,
        "source": "learned",
        "samples_ms": list(samples),
        "d_out_ms": list(delays),
        "latency_ms": latency,
        "d_ref_ms": d_ref,
        "starts": len(samples),
        "measured_at": "2026-10-29T10:00:00+00:00",
    }


def test_forget_clears_the_calibration_and_the_learned_entry(api_client, calibrated_bridge):
    dev = calibrated_bridge(learned=[_learned_entry()])
    assert dev.sent()[-1]["learned_starts"] == 2  # the daemon has the learned target alongside 203.4
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/44100/{AIR_ADAPTER}")
    assert r.status_code == 204, r.text
    assert not dev.stored_learned()
    assert [(e["rate"], e["latency_ms"]) for e in dev.stored()] == [(48000, 150.0)]
    last = dev.sent()[-1]
    assert last["latency_ms"] is None
    assert not any(k in last for k in ("learned_ms", "learned_d_ref_ms", "learned_starts"))


def test_forget_of_a_learned_only_key_is_204(api_client, calibrated_bridge):
    dev = calibrated_bridge(entries=[], learned=[_learned_entry()])
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/44100/{AIR_ADAPTER}")
    assert r.status_code == 204, r.text
    assert not dev.stored_learned()
    assert not any(k in dev.sent()[-1] for k in ("learned_ms", "learned_starts"))


def test_forget_all_clears_the_learned_entries_too(api_client, calibrated_bridge):
    dev = calibrated_bridge(learned=[_learned_entry(), _learned_entry(rate=48000)])
    assert api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations").status_code == 204
    assert not dev.stored_learned()


def test_forget_with_neither_kind_is_404(api_client, calibrated_bridge):
    dev = calibrated_bridge(learned=[_learned_entry(rate=48000)])
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/32000/{AIR_ADAPTER}")
    assert r.status_code == 404 and r.json()["code"] == "unknown_calibration"
    assert len(dev.stored_learned()) == 1


def test_forgetting_another_rate_keeps_the_live_value(api_client, calibrated_bridge):
    # Entries carry their reference delay (as a loaded config does), so the reload behind the
    # Forget changes nothing the daemon has.
    dev = calibrated_bridge(
        entries=[{**AIR_ENTRY, "d_ref_ms": 184.0}, {**AIR_ENTRY, "rate": 48000, "latency_ms": 150.0, "d_ref_ms": 184.0}]
    )
    count = len(dev.sent())
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/48000/{AIR_ADAPTER.lower()}")
    assert r.status_code == 204, r.text
    assert [e["rate"] for e in dev.stored()] == [44100]
    assert len(dev.sent()) == count  # the daemon already has 203.4


def test_forget_all_calibrations(api_client, calibrated_bridge):
    dev = calibrated_bridge()
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations")
    assert r.status_code == 204, r.text
    assert api_client.get(f"/api/v1/devices/{dev.id}").json()["audio"]["air_to_sound_latency"] == []
    assert not dev.stored()
    assert dev.sent()[-1] == _cal(None)


def test_forget_unknown_is_404(api_client, calibrated_bridge):
    dev = calibrated_bridge()
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/44100/00:00:00:00:00:00")
    assert r.status_code == 404
    assert r.json()["code"] == "unknown_calibration"
    assert api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/32000/{AIR_ADAPTER}").status_code == 404
    assert api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/aac/44100/{AIR_ADAPTER}").status_code == 404
    assert len(dev.stored()) == 2


def test_forget_on_an_unknown_device_is_404(api_client, calibrated_bridge):
    calibrated_bridge()
    r = api_client.delete(f"/api/v1/devices/nope/air-calibrations/sbc/44100/{AIR_ADAPTER}")
    assert r.status_code == 404 and r.json()["code"] == "unknown_device"
    assert api_client.delete("/api/v1/devices/nope/air-calibrations").status_code == 404


def test_the_rate_in_the_path_is_a_number(api_client, calibrated_bridge):
    dev = calibrated_bridge()
    assert api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/fast/{AIR_ADAPTER}").status_code == 422


def test_switch_off_forgets_in_the_config_and_sends_nothing(api_client, calibrated_bridge):
    dev = calibrated_bridge(switch=False)
    assert dev.sent() == []
    r = api_client.delete(f"/api/v1/devices/{dev.id}/air-calibrations/sbc/44100/{AIR_ADAPTER}")
    assert r.status_code == 204, r.text
    assert [e["rate"] for e in dev.stored()] == [48000]
    assert dev.client.air_to_sound_latency == [{**AIR_ENTRY, "rate": 48000, "latency_ms": 150.0, "d_ref_ms": 0.0}]
    assert dev.sent() == []
