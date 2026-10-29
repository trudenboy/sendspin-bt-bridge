"""Air correction state and learned latencies in the device model (stage B, B9)."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.status as status_uc
from sendspin_bridge.application.models.devices import PlayoutTiming
from sendspin_bridge.config.players import record_air_learned_sample
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

MAC = "AA:BB:CC:DD:EE:01"
PLAYER_ID = "kitchen-1"
ADAPTER = "C0:FB:F9:62:D7:D6"
KEY = ("sbc", 44100, ADAPTER)

AIR_KEYS = {
    "air_bias_state": "active",
    "air_bias_reason": None,
    "air_bias_ms": 12.3,
    "air_target": "calibrated",
    "air_target_ms": 228.7,
    "air_d_ref_ms": 184.0,
    "air_trim_ms": 30.0,
    "air_learned_starts": None,
    "air_corrections": 2,
    "air_last_correction_ms": -41.5,
    "air_bias_gated": 3,
    "air_bias_skipped": 1,
}


def _learned_entries():
    entries = None
    for x, d in ((219.0, 184.0), (220.0, 184.0), (219.8, 184.0)):
        entries = record_air_learned_sample(entries, KEY, x, d, "2026-10-28T09:00:00+00:00")
    return entries


@pytest.fixture
def running_device(monkeypatch, tmp_config):
    status: dict = {"backend_type": "bluetooth_a2dp", "volume": 100, "bluetooth_mac": MAC}
    client = SimpleNamespace(
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
        bt_manager=SimpleNamespace(mac_address=MAC, connected=False),
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )
    monkeypatch.setattr(
        status_uc, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[client])
    )
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": PLAYER_ID,
                        "name": "Kitchen",
                        "backend": {"type": "bluetooth_a2dp", "mac": MAC},
                        "air_to_sound_latency": [
                            {
                                "codec": "sbc",
                                "rate": 44100,
                                "adapter": ADAPTER,
                                "latency_ms": 228.7,
                                "d_ref_ms": 184.0,
                                "measured_at": "2026-10-28T09:00:00+00:00",
                            }
                        ],
                        "air_learned_latency": _learned_entries(),
                    },
                ],
                "PLAYER_STATE": {},
            }
        )
    )
    return client


def test_playout_timing_keeps_the_air_keys_and_ignores_air_learned():
    model = PlayoutTiming.model_validate({"mode": "loop", **AIR_KEYS, "air_learned": {"x": 1}})
    for key, value in AIR_KEYS.items():
        assert getattr(model, key) == value
    assert not hasattr(model, "air_learned")


def test_playout_timing_defaults_without_air_keys():
    model = PlayoutTiming.model_validate({"mode": "measure"})
    assert model.air_bias_state is None and model.air_corrections == 0
    assert model.air_bias_gated == 0 and model.air_bias_skipped == 0


def test_device_json_carries_the_air_correction(api_client, running_device):
    running_device.status.update({"playout": {"mode": "loop", **AIR_KEYS}})
    body = api_client.get(f"/api/v1/devices/{PLAYER_ID}").json()
    playout = body["timing"]["playout"]
    assert playout["air_bias_state"] == "active"
    assert playout["air_bias_skipped"] == 1
    assert playout["air_trim_ms"] == 30.0
    assert "air_learned" not in playout
    assert body["audio"]["air_to_sound_latency"][0]["d_ref_ms"] == 184.0


def test_device_json_lists_the_learned_entries(api_client, running_device):
    entries = api_client.get(f"/api/v1/devices/{PLAYER_ID}").json()["audio"]["air_learned_latency"]
    assert len(entries) == 1
    entry = entries[0]
    assert (entry["codec"], entry["rate"], entry["adapter"]) == KEY
    assert entry["starts"] == 3 and entry["samples_ms"] == [219.0, 220.0, 219.8]
    assert entry["d_out_ms"] == [184.0, 184.0, 184.0]
    assert entry["source"] == "learned"
    assert isinstance(entry["latency_ms"], float) and isinstance(entry["d_ref_ms"], float)


def test_openapi_lists_the_air_fields_as_required(api_client):
    schemas = api_client.get("/api/v1/openapi.json").json()["components"]["schemas"]
    for name in ("PlayoutTiming-Output", "PlayoutTiming"):
        if name in schemas:
            playout = schemas[name]
            break
    else:  # pragma: no cover
        pytest.fail("PlayoutTiming missing from the schema")
    for key in AIR_KEYS:
        assert key in playout["required"], key
    assert "AirLearnedEntryOut" in schemas
    assert "d_ref_ms" in schemas["AirLatencyEntryOut"]["required"]
    assert "air_learned_latency" in schemas["DeviceAudio"]["required"]
