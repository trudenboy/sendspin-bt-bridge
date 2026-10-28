"""Air sensor subscriptions follow daemons, Bluetooth connections and live reconfiguration.

A real ``SendspinClient`` (fake Bluetooth manager, fake daemon process whose stdin records every
line) and a real ``AirSensorService`` on a fake monitor hub. The client subscribes its speaker
while the daemon advertises ``air_sensor.accepts`` with v1, the speaker is connected, management
is on and the speaker is not in standby; every other edge unsubscribes. After a respawn the
current calibration goes out again. A hub reopen after an error starts new epochs and sends a
fresh ``air_source`` at once.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sendspin_bridge.services.bluetooth import air_sensor_service as air_mod
from sendspin_bridge.services.bluetooth.a2dp_air import read_btsnoop
from sendspin_bridge.services.bluetooth.air_sensor_service import AirSensorService
from sendspin_bridge.services.bluetooth.hci_monitor_hub import HubRecord
from sendspin_bridge.services.ipc.commands import AirSource

pytestmark = pytest.mark.asyncio

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "a2dp_air" / "eneby_clicks.btsnoop.gz"
ENEBY, OTHER = "FC:58:FA:EB:08:6C", "2C:D2:6B:B8:EC:5B"
OFFSET_US = 1_793_130_000_000_000.0
TICK_S = 0.25


class FakeHub:
    def __init__(self, peers):
        self._peers = dict(peers)
        self.unavailable_reason = None
        self.consumers: list = []

    def add_consumer(self, c):
        if c not in self.consumers:
            self.consumers.append(c)
            c.on_hub_state("open", None)

    def remove_consumer(self, c):
        self.consumers = [x for x in self.consumers if x is not c]

    def peer(self, index, handle):
        return self._peers.get((index, handle))

    def peers(self):
        return dict(self._peers)

    def adapters(self):
        return {1: "C0:FB:F9:62:D7:D6"}

    def counters(self):
        return {"drops": 0, "cpu_fraction": None, "streaming_links": 0}

    def deliver(self, rec):
        for c in list(self.consumers):
            c.on_record(rec)

    def state(self, state, reason=None):
        for c in list(self.consumers):
            c.on_hub_state(state, reason)


class _Transport:
    def get_write_buffer_size(self):
        return 0

    def is_closing(self):
        return False


class FakeStdin:
    def __init__(self, sink):
        self.transport = _Transport()
        self._sink = sink

    def write(self, data):
        self._sink.append(data)

    async def drain(self):
        return None

    def close(self):
        pass


class FakeProc:
    _pids = iter(range(1000, 2000))

    def __init__(self, sink):
        self.returncode = None
        self.stdin = FakeStdin(sink)
        self.pid = next(self._pids)

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        if self.returncode is None:
            self.returncode = -9


def _bt_manager():
    return SimpleNamespace(
        mac_address=ENEBY,
        connected=True,
        adapter_handle=None,
        check_bluetooth_available=lambda: True,
        allow_reconnect=lambda: None,
        cancel_reconnect=lambda: None,
        disconnect_device=lambda: None,
        shutdown=lambda: None,
    )


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
async def lifecycle_env(monkeypatch):
    from sendspin_bridge.bridge import state as bridge_state
    from sendspin_bridge.bridge.client import SendspinClient
    from sendspin_bridge.services.bluetooth import device_registry

    envs = []

    async def build(*, calibration_ms=None, switch=True):
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(bridge_state, "get_main_loop", lambda: loop)
        recs = list(read_btsnoop(FIX))
        hub = FakeHub({(1, 70): ENEBY})
        writes: list[bytes] = []
        client = SendspinClient("ENEBY20", "localhost", 9000, bt_manager=_bt_manager())
        client.config_id = "eneby"
        client.air_calibration_ms = calibration_ms
        registry = [client]
        env = SimpleNamespace(mac=ENEBY, client=client, hub=hub, writes=writes, service=None, registry=registry)
        env.now_us = recs[0].t * 1e6 - OFFSET_US
        if switch:
            env.service = AirSensorService(
                hub=hub,
                loop=loop,
                resolve_client=lambda mac: next((c for c in registry if c.bt_manager.mac_address == mac.upper()), None),
                clock=lambda: env.now_us,
            )
            air_mod.set_air_sensor_service(env.service)

        def sent_dicts():
            return [json.loads(line) for line in writes]

        def client_sent_air_commands():
            return [d for d in sent_dicts() if str(d.get("cmd", "")).startswith(("air_", "set_air_"))]

        async def daemon_starts(accepts=(1,)):
            client._daemon_proc = FakeProc(writes)
            await _settle()
            if accepts is not None:
                client._update_status({"air_sensor": {"state": "acquiring", "accepts": list(accepts)}})
            await _settle()

        async def daemon_exits():
            # The real health path of the status loop notices the exit.
            client._daemon_proc.returncode = 1
            client.running = True
            task = asyncio.create_task(client._status_monitor_loop())
            for _ in range(200):
                await asyncio.sleep(0)
                if client._daemon_proc is None:
                    break
            client.running = False
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert client._daemon_proc is None

        async def bt_disconnect():
            client.bt_manager.connected = False
            client._update_status({"bluetooth_connected": False})
            await _settle()

        async def bt_connect():
            client.bt_manager.connected = True
            client._update_status({"bluetooth_connected": True})
            await _settle()

        async def reconfig(*, enabled, removed=False):
            from sendspin_bridge.services.infrastructure.config_diff import ActionKind, ReconfigAction
            from sendspin_bridge.services.lifecycle.reconfig_orchestrator import ReconfigOrchestrator

            ctx = SimpleNamespace()  # re-enabling an existing client only checks that a context exists
            orch = ReconfigOrchestrator(
                loop, device_registry.DeviceRegistrySnapshot(active_clients=list(registry)), activation_context=ctx
            )
            if enabled:
                device = {"mac": ENEBY, "player_name": "ENEBY20", "enabled": True}
                action = ReconfigAction(
                    kind=ActionKind.START_CLIENT,
                    mac=ENEBY,
                    fields=["device"],
                    payload={"device": device},
                    label="ENEBY20",
                    player_id="eneby",
                )
            else:
                action = ReconfigAction(
                    kind=ActionKind.STOP_CLIENT,
                    mac=ENEBY,
                    fields=["removed"] if removed else ["enabled"],
                    label="ENEBY20",
                    player_id="eneby",
                )
            # The API applies a reconfiguration from a worker thread.
            summary = await asyncio.to_thread(orch.apply, [action])
            assert summary.errors == []
            await _settle()
            if enabled:
                # Management is back: the monitor reconnects and the daemon respawns.
                await bt_connect()
                await daemon_starts(accepts=[1])

        async def stream(lo, hi):
            for r in recs[lo:hi]:
                env.now_us = r.t * 1e6 - OFFSET_US
                hub.deliver(HubRecord(env.now_us, r.index, r.opcode, r.payload, r.drops, "kernel", 0))
            for _ in range(8):
                env.now_us += TICK_S * 1e6
                env.service.tick()
                await _settle()

        half = len(recs) // 2

        async def stream_some():
            await stream(*env._next_span.pop(0))

        env._next_span = [(0, half), (half, len(recs))]

        async def hub_error_then_open():
            hub.state("error", "monitor_error")
            env.service.tick()
            await _settle()
            hub.state("open")
            await _settle()

        def last_envelope():
            return [c for c in map(_decode, sent_dicts()) if type(c).__name__ == "AirEnvelope"][-1]

        def _decode(d):
            from sendspin_bridge.services.ipc.commands import decode_command

            try:
                return decode_command(d, now_raw_us=lambda: env.now_us)
            except Exception:
                return None

        def last_source():
            return [c for c in map(_decode, sent_dicts()) if isinstance(c, AirSource)][-1]

        env.sent_dicts = sent_dicts
        env.client_sent_air_commands = client_sent_air_commands
        env.daemon_starts = daemon_starts
        env.daemon_exits = daemon_exits
        env.bt_disconnect = bt_disconnect
        env.bt_connect = bt_connect
        env.reconfig = reconfig
        env.stream_some = stream_some
        env.hub_error_then_open = hub_error_then_open
        env.last_envelope = last_envelope
        env.last_source = last_source
        envs.append(env)

        # Start state: connected speaker, daemon running with its sensor built.
        client._update_status({"bluetooth_connected": True})
        await daemon_starts(accepts=[1])
        return env

    yield build
    air_mod.set_air_sensor_service(None)
    for env in envs:
        if env.service is not None:
            await env.service.stop()


async def test_daemon_respawn_resubscribes_and_resends_calibration(lifecycle_env):
    env = await lifecycle_env(calibration_ms=203.0)
    await env.daemon_exits()
    assert not env.service.is_subscribed(env.mac)
    await env.daemon_starts(accepts=[1])
    assert env.service.is_subscribed(env.mac)
    assert env.sent_dicts()[-1] == {"cmd": "set_air_calibration", "v": 1, "latency_ms": 203.0, "protocol_version": 2}


async def test_calibration_goes_out_once_per_daemon(lifecycle_env):
    env = await lifecycle_env(calibration_ms=203.0)
    first = [d for d in env.sent_dicts() if d["cmd"] == "set_air_calibration"]
    env.client._update_status({"air_sensor": {"state": "locked", "accepts": [1]}})
    await asyncio.sleep(0)
    assert [d for d in env.sent_dicts() if d["cmd"] == "set_air_calibration"] == first
    assert len(first) == 1


async def test_a_daemon_without_the_sensor_is_not_subscribed(lifecycle_env):
    env = await lifecycle_env()
    await env.daemon_exits()
    sent_before = len(env.client_sent_air_commands())
    await env.daemon_starts(accepts=None)
    assert not env.service.is_subscribed(env.mac)
    await env.daemon_starts(accepts=[2])
    assert not env.service.is_subscribed(env.mac)
    assert len(env.client_sent_air_commands()) == sent_before


async def test_bluetooth_disconnect_unsubscribes(lifecycle_env):
    env = await lifecycle_env()
    await env.bt_disconnect()
    assert not env.service.is_subscribed(env.mac)
    await env.bt_connect()
    assert env.service.is_subscribed(env.mac)


async def test_standby_unsubscribes(lifecycle_env):
    env = await lifecycle_env()
    env.client._update_status({"bt_standby": True})
    assert not env.service.is_subscribed(env.mac)
    env.client._update_status({"bt_standby": False})
    assert env.service.is_subscribed(env.mac)


async def test_live_reconfig_disable_and_add(lifecycle_env):
    env = await lifecycle_env()
    await env.reconfig(enabled=False)
    assert not env.service.is_subscribed(env.mac)
    await env.reconfig(enabled=True)
    assert env.service.is_subscribed(env.mac)


async def test_a_removed_player_is_unsubscribed_even_when_the_release_fails(lifecycle_env):
    env = await lifecycle_env()

    def fail(_enabled):
        raise RuntimeError("adapter busy")

    async def slow_stop():
        return None  # the daemon is still running when the removal returns

    env.client.set_bt_management_enabled = fail
    env.client.stop_sendspin = slow_stop
    await env.reconfig(enabled=False, removed=True)
    assert env.client.is_running()
    assert not env.service.is_subscribed(env.mac)
    env.client._update_status({"air_sensor": {"state": "acquiring", "accepts": [1]}})
    assert not env.service.is_subscribed(env.mac)  # a late status of the old daemon does not bring it back


async def test_hub_reopen_starts_new_epochs_and_resends_sources(lifecycle_env):
    env = await lifecycle_env()
    await env.stream_some()
    epoch_before = env.last_envelope().epoch
    sources_before = len([d for d in env.sent_dicts() if d["cmd"] == "air_source"])
    await env.hub_error_then_open()
    # No tick in between: the reopen itself sends a fresh source.
    assert len([d for d in env.sent_dicts() if d["cmd"] == "air_source"]) >= sources_before + 2
    assert env.last_source().state in ("streaming", "no_traffic")
    assert env.service.tracker_epoch(env.mac) > epoch_before
    await env.stream_some()
    assert env.last_envelope().epoch > epoch_before


async def test_switch_off_never_subscribes(lifecycle_env):
    env = await lifecycle_env(switch=False)
    assert env.service is None and env.client_sent_air_commands() == []
    assert air_mod.get_air_sensor_service() is None


# ── Sender tasks are created and cancelled only on the loop (no orphans) ──


class _Client:
    air_accepts = (1,)
    status = {"bt_codec_name": "SBC"}

    def send_air_command(self, cmd):
        return True


async def test_an_unsubscribe_during_a_tick_leaves_no_orphan_sender():
    hub = FakeHub({})
    service: AirSensorService

    def resolve(mac):
        if mac == ENEBY:
            service.unsubscribe(OTHER)  # e.g. a status edge on another thread, mid-tick
        return _Client()

    service = AirSensorService(hub=hub, loop=asyncio.get_running_loop(), resolve_client=resolve)
    service.subscribe(ENEBY)
    service.subscribe(OTHER)
    try:
        service.tick()
        await _settle()
        names = {t.get_name() for t in asyncio.all_tasks() if not t.done()}
        assert f"air-sender-{OTHER}" not in names
        assert f"air-sender-{ENEBY}" in names
    finally:
        await service.stop()
        await _settle()
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("air-sender-") and not t.done()]


async def test_an_unsubscribe_off_the_loop_cancels_the_sender_on_the_loop():
    service = AirSensorService(hub=FakeHub({}), loop=asyncio.get_running_loop(), resolve_client=lambda m: _Client())
    service.subscribe(ENEBY)
    service.tick()
    await _settle()
    (task,) = [t for t in asyncio.all_tasks() if t.get_name() == f"air-sender-{ENEBY}"]
    await asyncio.to_thread(service.unsubscribe, ENEBY)
    await _settle()
    assert task.cancelled()
    await service.stop()


# ── Calibration: the stored entry for the current codec, SBC rate and adapter, applied live (A15) ──

ADAPTER = "C0:FB:F9:62:D7:D6"


def _entry(rate=44100, latency=203.4, adapter=ADAPTER):
    return {
        "codec": "sbc",
        "rate": rate,
        "adapter": adapter,
        "latency_ms": latency,
        "measured_at": "2026-10-28T09:00:00+00:00",
    }


def _cal(latency):
    return {"cmd": "set_air_calibration", "v": 1, "latency_ms": latency, "protocol_version": 2}


def _cals(env):
    return [d for d in env.sent_dicts() if d["cmd"] == "set_air_calibration"]


def _air_status(**extra):
    return {"air_sensor": {"state": "locked", "accepts": [1], "codec": "sbc", "adapter": ADAPTER, **extra}}


async def test_the_entry_for_the_current_codec_rate_and_adapter_is_sent(lifecycle_env):
    env = await lifecycle_env()
    env.client.air_to_sound_latency = [_entry(44100, 203.4), _entry(48000, 150.0)]
    env.client._update_status(_air_status(rate=44100))
    await _settle()
    assert _cals(env)[-1] == _cal(203.4)
    env.client._update_status(_air_status(rate=48000))
    await _settle()
    assert _cals(env)[-1] == _cal(150.0)
    # No entry for this rate: the daemon is told it is uncalibrated.
    env.client._update_status(_air_status(rate=32000))
    await _settle()
    assert _cals(env)[-1] == _cal(None)
    count = len(_cals(env))
    env.client._update_status(_air_status(rate=32000, host_placement_ms=3.0))
    await _settle()
    assert len(_cals(env)) == count
    assert env.client.air_calibration_ms is None


async def test_another_adapter_does_not_match(lifecycle_env):
    env = await lifecycle_env()
    env.client.air_to_sound_latency = [_entry(adapter="C0:FB:F9:62:D6:9D")]
    env.client._update_status(_air_status(rate=44100))
    await _settle()
    assert _cals(env)[-1] == _cal(None)


async def test_a_live_change_is_resent_to_the_running_daemon(lifecycle_env):
    env = await lifecycle_env()
    env.client.air_to_sound_latency = []
    env.client._update_status(_air_status(rate=44100))
    await _settle()
    assert _cals(env)[-1] == _cal(None)
    applied = await env.client.apply_hot_config({"air_to_sound_latency": [_entry(latency=199.0)]})
    assert applied == ["air_to_sound_latency"]
    assert _cals(env)[-1] == _cal(199.0)
    assert env.client.air_calibration_ms == 199.0
    applied = await env.client.apply_hot_config({"air_to_sound_latency": None})
    assert applied == ["air_to_sound_latency"]
    assert _cals(env)[-1] == _cal(None)
    assert env.client.air_to_sound_latency == []


async def test_codec_and_adapter_fall_back_to_the_connection(lifecycle_env):
    env = await lifecycle_env()
    env.client.bt_manager.effective_adapter_mac = ADAPTER.lower()
    env.client.air_to_sound_latency = [_entry()]
    env.client._update_status(
        {"bt_codec_name": "SBC", "air_sensor": {"state": "acquiring", "accepts": [1], "rate": 44100}}
    )
    await _settle()
    assert _cals(env)[-1] == _cal(203.4)


async def test_no_send_is_scheduled_while_the_daemon_has_the_value(lifecycle_env):
    env = await lifecycle_env()
    env.client.air_to_sound_latency = [_entry()]
    env.client._update_status(_air_status(rate=44100))
    await _settle()
    assert _cals(env)[-1] == _cal(203.4)
    loop = asyncio.get_running_loop()
    scheduled = []
    real = loop.call_soon_threadsafe

    def counting(cb, *args, **kwargs):
        if cb == env.client._send_air_calibration_once:
            scheduled.append(cb)
        return real(cb, *args, **kwargs)

    loop.call_soon_threadsafe = counting
    try:
        # Status updates from other threads (D-Bus, API workers) with nothing new to send.
        await asyncio.to_thread(env.client._update_status, _air_status(rate=44100, host_placement_ms=1.0))
        await asyncio.to_thread(env.client._update_status, {"rssi_dbm": -60})
        assert scheduled == []
        await asyncio.to_thread(env.client._update_status, _air_status(rate=48000))
        assert len(scheduled) == 1
    finally:
        del loop.call_soon_threadsafe
    await _settle()
    assert _cals(env)[-1] == _cal(None)


async def test_switch_off_stores_the_calibration_but_sends_nothing(lifecycle_env):
    env = await lifecycle_env(switch=False)
    applied = await env.client.apply_hot_config({"air_to_sound_latency": [_entry()]})
    assert applied == ["air_to_sound_latency"]
    assert env.client.air_to_sound_latency == [_entry()]
    assert env.client_sent_air_commands() == []


async def test_a_respawn_keeps_the_last_seen_rate_for_the_spawn_value(lifecycle_env):
    env = await lifecycle_env()
    env.client.air_to_sound_latency = [_entry()]
    env.client._update_status(_air_status(rate=44100))
    await _settle()
    await env.daemon_exits()
    env.client._update_status({"air_sensor": None})
    # Before the new daemon reports a rate the last one seen still picks the entry.
    assert env.client._resolve_air_calibration() == 203.4
