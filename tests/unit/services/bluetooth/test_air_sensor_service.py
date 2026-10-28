"""AirSensorService: hub consumer → per-device trackers → one sender task per device.

A fake hub replays the click-track capture as ``HubRecord`` s with RAW times
(``t_raw_us = t_unix·1e6 − OFFSET_US``); fake clients record the air commands the service hands
them. One client can be "stalled": every write reports a full buffer.
"""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from sendspin_bridge.services.bluetooth.a2dp_air import A2dpAirParser, parse_records, read_btsnoop
from sendspin_bridge.services.bluetooth.air_sensor_service import AirSensorService
from sendspin_bridge.services.bluetooth.hci_monitor_hub import HubRecord
from sendspin_bridge.services.bluetooth.hci_socket import MONITOR_ACL_TX_PKT, MONITOR_EVENT_PKT
from sendspin_bridge.services.ipc.commands import AirEnvelope, AirSource

pytestmark = pytest.mark.asyncio

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "a2dp_air" / "eneby_clicks.btsnoop.gz"
ENEBY, OTHER = "FC:58:FA:EB:08:6C", "2C:D2:6B:B8:EC:5B"
OFFSET_US = 1_793_130_000_000_000.0  # unix → a plausible RAW clock (≈ 3141 s after boot)
TICK_S = 0.25


class FakeHub:
    def __init__(self, peers, *, reason=None, cpu_fraction=None, streaming_links=0):
        self._peers = dict(peers)
        self.unavailable_reason = reason
        self.available = reason is None
        self.generation = 1
        self.consumers: list = []
        self._counters = {"drops": 0, "cpu_fraction": cpu_fraction, "streaming_links": streaming_links}

    def add_consumer(self, c):
        if c in self.consumers:
            return
        self.consumers.append(c)
        if self.available:
            c.on_hub_state("open", None)

    def remove_consumer(self, c):
        self.consumers = [x for x in self.consumers if x is not c]

    def peer(self, index, handle):
        return self._peers.get((index, handle))

    def peers(self):
        return dict(self._peers)

    def adapters(self):
        return {0: "C0:FB:F9:62:D6:9D", 1: "C0:FB:F9:62:D7:D6"}

    def counters(self):
        return dict(self._counters)

    def set_counters(self, **kw):
        self._counters.update(kw)

    def consumers_wanting_acl_tx(self):
        return sum(1 for c in self.consumers if c.wants_acl_tx)

    def deliver(self, rec: HubRecord):
        for c in list(self.consumers):
            if rec.opcode == MONITOR_ACL_TX_PKT and not c.wants_acl_tx:
                continue
            c.on_record(rec)


class FakeClient:
    def __init__(self, accepts, *, stalled=False):
        self.air_accepts = tuple(accepts or ())
        self.stalled = stalled
        self.status = {"bt_codec_name": "SBC"}
        self.sent: list = []
        self.sent_attempts: list = []

    def send_air_command(self, cmd):
        self.sent_attempts.append(cmd)
        if self.stalled:
            return False
        self.sent.append(cmd)
        return True


def _rehandle(rec, index, old, new):
    """A copy of an ACL TX / NOCP record of handle *old* on (index, new); other records → None."""
    if rec.opcode == MONITOR_ACL_TX_PKT:
        (hf,) = struct.unpack_from("<H", rec.payload, 0)
        if hf & 0x0FFF != old:
            return None
        payload = struct.pack("<H", (hf & 0xF000) | new) + rec.payload[2:]
        return SimpleNamespace(t=rec.t, index=index, opcode=rec.opcode, payload=payload, drops=rec.drops)
    if rec.opcode == MONITOR_EVENT_PKT and len(rec.payload) >= 3 and rec.payload[0] == 0x13:
        body = bytearray(rec.payload)
        for k in range(body[2]):
            off = 3 + 4 * k
            (h,) = struct.unpack_from("<H", body, off)
            if h & 0x0FFF == old:
                struct.pack_into("<H", body, off, (h & 0xF000) | new)
        return SimpleNamespace(t=rec.t, index=index, opcode=rec.opcode, payload=bytes(body), drops=rec.drops)
    return None


@pytest.fixture
async def air_env():
    envs = []

    async def build(
        *, peers, accepts, stalled=(), hub_reason=None, cpu_fraction=None, streaming_links=0, cpu_window_s=TICK_S
    ):
        recs = list(read_btsnoop(FIX))
        first = next(p for p in parse_records(recs, A2dpAirParser(check_crc="first")) if p.codec == "sbc")
        hub = FakeHub(peers, reason=hub_reason, cpu_fraction=cpu_fraction, streaming_links=streaming_links)
        clients = {mac: FakeClient(accepts, stalled=mac in stalled) for mac in (ENEBY, OTHER)}
        env = SimpleNamespace(hub=hub, clients=clients, OFFSET_US=OFFSET_US, first_media_unix_s=first.t_send)
        env.now_us = recs[0].t * 1e6 - OFFSET_US
        env.service = AirSensorService(
            hub=hub,
            loop=asyncio.get_running_loop(),
            resolve_client=clients.get,
            clock=lambda: env.now_us,
            cpu_window_s=cpu_window_s,
        )
        env.client = clients.__getitem__

        async def settle():
            for _ in range(5):
                await asyncio.sleep(0)

        async def ticks(n):
            for _ in range(n):
                env.now_us += TICK_S * 1e6
                env.service.tick()
                await settle()

        async def replay_fixture(both_links=False):
            next_tick = recs[0].t * 1e6 - OFFSET_US + TICK_S * 1e6
            for r in recs:
                t_raw = r.t * 1e6 - OFFSET_US
                while t_raw >= next_tick:
                    env.now_us = next_tick
                    env.service.tick()
                    await settle()
                    next_tick += TICK_S * 1e6
                env.now_us = t_raw
                copies = [r]
                if both_links:
                    twin = _rehandle(r, 0, 70, 71)
                    if twin is not None:
                        copies.append(twin)
                for c in copies:
                    hub.deliver(HubRecord(t_raw, c.index, c.opcode, c.payload, c.drops, "kernel", 0))

        env.ticks = ticks
        env.replay_fixture = replay_fixture
        envs.append(env)
        return env

    yield build
    for env in envs:
        await env.service.stop()


async def test_batches_reach_only_the_subscribed_speaker(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    await env.ticks(8)
    envs = [c for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)]
    assert envs and all(len(c.e) <= 128 for c in envs)
    assert env.client(OTHER).sent == []


async def test_air_times_are_raw_seconds_from_the_record(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    await env.ticks(8)
    first = next(c for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope))
    assert abs(first.t0_us - (env.first_media_unix_s * 1e6 - env.OFFSET_US)) < 50_000  # no wall-clock leak


async def test_nothing_is_sent_before_the_daemon_advertises(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=None)
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    await env.ticks(8)
    assert env.client(ENEBY).sent == []
    assert env.client(ENEBY).sent_attempts == []


async def test_unattributed_links_are_ignored(air_env):
    env = await air_env(peers={}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    await env.ticks(8)
    assert not [c for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)]


async def test_a_stalled_daemon_does_not_delay_another(air_env):
    env = await air_env(peers={(1, 70): ENEBY, (0, 71): OTHER}, accepts=[1], stalled={ENEBY})
    env.service.subscribe(ENEBY)
    env.service.subscribe(OTHER)
    await env.replay_fixture(both_links=True)
    await env.ticks(40)
    assert [c for c in env.client(OTHER).sent if isinstance(c, AirEnvelope)]
    assert env.service.queued(ENEBY) <= 16
    src = [c for c in env.client(ENEBY).sent_attempts if isinstance(c, AirSource)][-1]
    assert src.ipc_dropped > 0
    other_src = [c for c in env.client(OTHER).sent if isinstance(c, AirSource)][-1]
    assert other_src.ipc_dropped == 0 and (other_src.index, other_src.handle) == (0, 71)


async def test_hub_without_permission_reports_unavailable(air_env):
    env = await air_env(peers={}, accepts=[1], hub_reason="no_cap_net_raw")
    env.service.subscribe(ENEBY)
    await env.ticks(1)
    src = [c for c in env.client(ENEBY).sent if isinstance(c, AirSource)][-1]
    assert (src.state, src.reason) == ("unavailable", "no_cap_net_raw")


async def test_cpu_guard_scales_with_streaming_speakers(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1], cpu_fraction=0.06, streaming_links=2)
    env.service.subscribe(ENEBY)
    await env.ticks(2)
    assert env.hub.consumers_wanting_acl_tx() == 1  # 6 % < 2 x 4 %: within budget
    env.hub.set_counters(cpu_fraction=0.06, streaming_links=1)
    await env.ticks(2)  # two windows over 1 x 4 %
    assert env.hub.consumers_wanting_acl_tx() == 0
    src = [c for c in env.client(ENEBY).sent if isinstance(c, AirSource)][-1]
    assert src.reason == "cpu_budget"


async def test_one_window_over_budget_does_not_trip_the_guard(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1], cpu_fraction=0.06, streaming_links=1)
    env.service.subscribe(ENEBY)
    await env.ticks(1)
    env.hub.set_counters(cpu_fraction=0.001)
    await env.ticks(3)
    assert env.hub.consumers_wanting_acl_tx() == 1


async def test_no_subscriber_means_no_acl_tx_consumer(air_env):
    env = await air_env(peers={}, accepts=[1])
    assert env.hub.consumers_wanting_acl_tx() == 0
    env.service.subscribe(ENEBY)
    assert env.hub.consumers_wanting_acl_tx() == 1
    env.service.unsubscribe(ENEBY)
    assert env.hub.consumers_wanting_acl_tx() == 0


async def test_the_source_is_repeated_every_five_seconds(air_env):
    env = await air_env(peers={}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.ticks(1)
    await env.ticks(19)  # 4.75 s more: no change, no repeat yet
    assert len([c for c in env.client(ENEBY).sent if isinstance(c, AirSource)]) == 1
    await env.ticks(1)
    assert len([c for c in env.client(ENEBY).sent if isinstance(c, AirSource)]) == 2


async def test_a_hub_reopen_starts_a_new_epoch(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    epochs_before = {c.epoch for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)}
    env.service.on_hub_state("error", "monitor_error")
    await env.ticks(1)
    src = [c for c in env.client(ENEBY).sent if isinstance(c, AirSource)][-1]
    assert (src.state, src.reason) == ("unavailable", "monitor_error")
    env.service.on_hub_state("open", None)
    await env.ticks(1)
    src = [c for c in env.client(ENEBY).sent if isinstance(c, AirSource)][-1]
    assert src.state != "unavailable"
    assert env.service.tracker_epoch(ENEBY) > max(epochs_before)


async def test_a_drop_increase_starts_a_new_epoch(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    before = env.service.tracker_epoch(ENEBY)
    env.hub.deliver(HubRecord(env.now_us, 1, MONITOR_EVENT_PKT, b"\x0e\x00", 3, "kernel", 0))
    assert env.service.tracker_epoch(ENEBY) == before + 1


# ── SendspinClient.send_air_command ──────────────────────────────────────


class _Transport:
    def __init__(self, buffered=0):
        self.buffered = buffered

    def get_write_buffer_size(self):
        return self.buffered

    def is_closing(self):
        return False


class _Stdin:
    def __init__(self, buffered=0):
        self.transport = _Transport(buffered)
        self.writes: list[bytes] = []

    def write(self, data):
        self.writes.append(data)


def _client_with_stdin(stdin, accepts):
    from sendspin_bridge.bridge.client import SendspinClient

    client = SendspinClient("Test Player", "localhost", 9000)
    client._daemon_proc = SimpleNamespace(returncode=None, stdin=stdin, pid=1)
    if accepts is not None:
        client.status.air_sensor = {"state": "acquiring", "accepts": accepts}
    return client


def _source():
    return AirSource("streaming", None, "sbc", None, 1, 70, False, 0, 0)


async def test_client_writes_one_air_line_when_the_daemon_accepts():
    stdin = _Stdin()
    client = _client_with_stdin(stdin, [1])
    assert client.send_air_command(_source()) is True
    (line,) = stdin.writes
    assert line.endswith(b"\n")
    msg = json.loads(line)
    assert (msg["cmd"], msg["v"], msg["handle"]) == ("air_source", 1, 70)


async def test_client_refuses_without_accepts():
    for accepts in (None, [], [2]):
        stdin = _Stdin()
        client = _client_with_stdin(stdin, accepts)
        assert client.send_air_command(_source()) is False
        assert stdin.writes == []


async def test_client_refuses_when_the_write_buffer_is_full():
    # 32 KiB, well below the pipe high-water mark: control commands keep headroom.
    stdin = _Stdin(buffered=32 * 1024 + 1)
    client = _client_with_stdin(stdin, [1])
    assert client.send_air_command(_source()) is False
    assert stdin.writes == []
    stdin.transport.buffered = 32 * 1024
    assert client.send_air_command(_source()) is True


async def test_client_refuses_when_the_daemon_is_gone():
    stdin = _Stdin()
    client = _client_with_stdin(stdin, [1])
    client._daemon_proc.returncode = 0
    assert client.send_air_command(_source()) is False
    assert stdin.writes == []


async def test_the_service_declares_the_monitor_records_its_parser_reads():
    # NEW_INDEX, DEL_INDEX, COMMAND (HCI_Reset), EVENT, ACL TX; and of the events, Connection
    # Complete, Disconnection Complete, Hardware Error and Number Of Completed Packets: the hub
    # skips everything else for it (R-19).
    assert AirSensorService.opcodes == frozenset({0, 1, 2, 3, 4})
    assert AirSensorService.event_codes == frozenset({0x03, 0x05, 0x10, 0x13})
