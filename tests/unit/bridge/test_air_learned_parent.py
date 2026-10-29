"""The parent learns each speaker's air latency from its starts (B8b-1).

A real SendspinClient; the daemon is a recording fake.  Spawn params carry the learned target,
daemon-reported samples are validated and persisted once per (daemon process, sequence number),
and the resent calibration always carries the learned three together with the calibration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.bluetooth import air_sensor_service as air_mod

MAC = "6C:5C:3D:35:17:99"
ADAPTER = "C0:FB:F9:62:D7:D6"
KEY = ("sbc", 44100, ADAPTER)


def _learned(samples: list[float], delays: list[float], starts: int | None = None) -> dict:
    from sendspin_bridge.services.audio.playout.air_learned import learned_reference

    latency, d_ref = learned_reference(samples, delays)
    return {
        "codec": "sbc",
        "rate": 44100,
        "adapter": ADAPTER,
        "source": "learned",
        "samples_ms": samples,
        "d_out_ms": delays,
        "latency_ms": latency,
        "d_ref_ms": d_ref,
        "starts": starts or len(samples),
        "measured_at": "2026-10-29T10:00:00+00:00",
    }


def _config(client_entries: dict) -> dict:
    return {
        "EXPERIMENTAL_AIR_SENSOR": True,
        "PLAYOUT_SYNC": "loop",
        "players": [
            {"id": "eneby", "name": "ENEBY", "backend": {"type": "bluetooth_a2dp", "mac": MAC}, **client_entries}
        ],
    }


@pytest.fixture
def service():
    def install():
        fake = SimpleNamespace(
            loop=asyncio.get_running_loop(), subscribe=lambda _mac: None, unsubscribe=lambda _mac: None
        )
        air_mod.set_air_sensor_service(fake)

    yield install
    air_mod.set_air_sensor_service(None)


async def _spawn(monkeypatch, config: dict) -> tuple[dict, SendspinClient]:
    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
    monkeypatch.setattr("sendspin_bridge.bridge.client.load_config", lambda: json.loads(json.dumps(config)))
    client = SendspinClient("ENEBY", "localhost", 9000)
    client.config_id = "eneby"
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = "bluez_sink.6C_5C_3D_35_17_99.a2dp_sink"
    # The key the spawn resolves against: what the daemon last reported.
    client._air_last_seen = {"codec": "sbc", "rate": 44100, "adapter": ADAPTER}
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


@pytest.mark.asyncio
async def test_one_learned_sample_leaves_the_spawn_param_unchanged(monkeypatch, service):
    service()
    cfg = _config({"air_learned_latency": [_learned([228.0], [184.0])]})
    params, _ = await _spawn(monkeypatch, cfg)
    assert params["air_sensor"] == {"enabled": True, "latency_ms": None}


@pytest.mark.asyncio
async def test_two_learned_samples_ride_in_the_spawn_param(monkeypatch, service):
    service()
    entry = _learned([228.0, 230.0], [184.0, 190.0])
    params, _ = await _spawn(monkeypatch, _config({"air_learned_latency": [entry]}))
    assert params["air_sensor"] == {
        "enabled": True,
        "latency_ms": None,
        "learned_ms": entry["latency_ms"],
        "learned_d_ref_ms": entry["d_ref_ms"],
        "learned_starts": 2,
    }


@pytest.mark.asyncio
async def test_a_calibration_carries_its_reference_delay(monkeypatch, service):
    service()
    cal = {
        "codec": "sbc",
        "rate": 44100,
        "adapter": ADAPTER,
        "latency_ms": 203.4,
        "d_ref_ms": 184.0,
        "measured_at": "2026-10-29T10:00:00+00:00",
    }
    params, _ = await _spawn(monkeypatch, _config({"air_to_sound_latency": [cal]}))
    assert params["air_sensor"] == {"enabled": True, "latency_ms": 203.4, "d_ref_ms": 184.0}


# -- sample persistence ----------------------------------------------------------------------


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


SAMPLE = {"seq": 1, "x_ms": 228.1, "d_out_ms": 184.0, "codec": "sbc", "rate": 44100, "adapter": ADAPTER}


@pytest.fixture
def running(monkeypatch, service):
    """A running client with a daemon that accepts the air payload; persistence is recorded."""

    async def build(entries: list | None = None):
        service()
        bt = SimpleNamespace(
            mac_address=MAC,
            connected=False,
            check_bluetooth_available=lambda: True,
            effective_adapter_mac=ADAPTER,
        )
        client = SendspinClient("ENEBY", "localhost", 9000, bt_manager=bt)
        client.config_id = "eneby"
        client.air_to_sound_latency = []
        client.air_learned_latency = list(entries or [])
        writes: list[bytes] = []
        persisted: list[tuple] = []

        def _fake_schedule(key, fn, pid, value):
            persisted.append((key, pid, value))
            fn(pid, value)

        saved: list[tuple] = []
        monkeypatch.setattr(client, "_schedule_persist", _fake_schedule)
        monkeypatch.setattr(
            "sendspin_bridge.bridge.client.save_air_learned_sample",
            lambda pid, key, x, d: saved.append((pid, key, x, d)),
        )
        client._daemon_proc = _FakeDaemon(writes)
        client._update_status(
            {"air_sensor": {"state": "locked", "accepts": [1], "codec": "sbc", "rate": 44100, "adapter": ADAPTER}}
        )
        await asyncio.sleep(0)
        return SimpleNamespace(
            client=client, saved=saved, persisted=persisted, sent=lambda: [json.loads(w) for w in writes]
        )

    return build


@pytest.mark.asyncio
async def test_a_reported_sample_is_persisted_once_per_process_and_seq(running):
    env = await running()
    env.client._update_status({"playout": {"air_learned": dict(SAMPLE)}})
    assert env.saved == [("eneby", KEY, 228.1, 184.0)]
    env.client._update_status({"playout": {"air_learned": dict(SAMPLE)}})
    assert len(env.saved) == 1
    env.client._daemon_proc = _FakeDaemon([])  # a new daemon process starts with seq 1 again
    env.client._update_status({"playout": {"air_learned": dict(SAMPLE)}})
    assert len(env.saved) == 2


@pytest.mark.asyncio
async def test_a_new_sample_resends_the_calibration_with_the_learned_target(running):
    env = await running([_learned([228.0], [184.0])])
    await asyncio.sleep(0)
    before = len(env.sent())
    env.client._update_status({"playout": {"air_learned": {**SAMPLE, "x_ms": 230.0, "d_out_ms": 190.0}}})
    await asyncio.sleep(0)
    sent = env.sent()
    assert len(sent) == before + 1
    last = sent[-1]
    assert last["cmd"] == "set_air_calibration"
    assert last["latency_ms"] is None
    assert last["learned_starts"] == 2
    assert isinstance(last["learned_ms"], float) and isinstance(last["learned_d_ref_ms"], float)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {k: v for k, v in SAMPLE.items() if k != "rate"},
        {**SAMPLE, "x_ms": 1500.0, "d_out_ms": 100.0},
        {k: v for k, v in SAMPLE.items() if k != "d_out_ms"},
    ],
    ids=["no-rate", "offset-1500", "no-delay"],
)
async def test_an_invalid_sample_is_ignored_with_one_debug_line(running, caplog, bad):
    env = await running()
    with caplog.at_level(logging.DEBUG, logger="sendspin_bridge.bridge.client"):
        env.client._update_status({"playout": {"air_learned": bad}})
        env.client._update_status({"playout": {"air_learned": bad}})
    assert env.saved == []
    assert env.client.air_learned_latency == []
    lines = [r for r in caplog.records if r.levelno == logging.DEBUG and "learned" in r.getMessage().lower()]
    assert len(lines) == 1
