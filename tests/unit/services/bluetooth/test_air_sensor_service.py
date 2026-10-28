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

        async def replay_fixture(both_links=False, mid_replay=None):
            next_tick = recs[0].t * 1e6 - OFFSET_US + TICK_S * 1e6
            for i, r in enumerate(recs):
                if mid_replay is not None and i == len(recs) // 2:
                    mid_replay(env)
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


async def test_the_service_status_tells_a_cpu_trip_for_diagnostics(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1], cpu_fraction=0.06, streaming_links=1)
    assert env.service.status() == {"state": "running", "reason": None}
    env.service.subscribe(ENEBY)
    await env.ticks(2)
    assert env.service.status() == {"state": "tripped", "reason": "cpu_budget"}


async def test_one_window_over_budget_does_not_trip_the_guard(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1], cpu_fraction=0.06, streaming_links=1)
    env.service.subscribe(ENEBY)
    await env.ticks(1)
    env.hub.set_counters(cpu_fraction=0.001)
    await env.ticks(3)
    assert env.hub.consumers_wanting_acl_tx() == 1


async def test_the_cpu_guard_counts_one_hub_window_once(air_env):
    # The hub reports the same completed window (cpu_windows=1) at every check: one window over
    # budget, however often the service looks, never trips the guard.
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1], cpu_fraction=0.06, streaming_links=1)
    env.hub.set_counters(cpu_windows=1)
    env.service.subscribe(ENEBY)
    await env.ticks(4)
    assert env.hub.consumers_wanting_acl_tx() == 1
    env.hub.set_counters(cpu_windows=2)  # a second completed window over budget
    await env.ticks(1)
    assert env.hub.consumers_wanting_acl_tx() == 0


async def test_the_cpu_guard_ignores_the_partial_first_window(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1], cpu_fraction=0.5, streaming_links=1)
    env.hub.set_counters(cpu_windows=0)
    env.service.subscribe(ENEBY)
    await env.ticks(4)
    env.hub.set_counters(cpu_windows=1)
    await env.ticks(1)
    assert env.hub.consumers_wanting_acl_tx() == 1  # one completed window over budget so far


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


# ── the stand-in when the service could not start (A13) ──────────────────


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_service_that_never_started_tells_every_subscribed_daemon_unavailable():
    from sendspin_bridge.services.bluetooth.air_sensor_service import UnavailableAirSensorService

    clients = {ENEBY: FakeClient([1]), OTHER: FakeClient([1])}
    stub = UnavailableAirSensorService(
        loop=asyncio.get_running_loop(), resolve_client=clients.get, reason="internal_error"
    )
    stub.subscribe(ENEBY)
    await _settle()
    (src,) = clients[ENEBY].sent
    assert isinstance(src, AirSource) and (src.state, src.reason) == ("unavailable", "internal_error")
    assert clients[OTHER].sent == []  # only subscribed speakers
    assert stub.status() == {"state": "failed", "reason": "internal_error"}


async def test_the_stand_in_prefers_the_hubs_own_reason():
    from sendspin_bridge.services.bluetooth.air_sensor_service import UnavailableAirSensorService

    client = FakeClient([1])
    hub = FakeHub({}, reason="no_cap_net_raw")
    stub = UnavailableAirSensorService(
        loop=asyncio.get_running_loop(), resolve_client=lambda mac: client, reason="internal_error", hub=hub
    )
    stub.subscribe(ENEBY)
    await _settle()
    assert client.sent[-1].reason == "no_cap_net_raw"


async def test_the_stand_in_repeats_and_retries_a_refused_write():
    from sendspin_bridge.services.bluetooth.air_sensor_service import UnavailableAirSensorService

    client = FakeClient([1], stalled=True)
    stub = UnavailableAirSensorService(
        loop=asyncio.get_running_loop(), resolve_client=lambda mac: client, reason="internal_error"
    )
    stub.subscribe(ENEBY)
    await _settle()
    assert client.sent == [] and len(client.sent_attempts) == 1
    client.stalled = False
    stub.tick()
    assert len(client.sent) == 1
    stub.unsubscribe(ENEBY)
    stub.tick()
    assert len(client.sent) == 1 and not stub.is_subscribed(ENEBY)


async def test_the_stand_in_sends_nothing_to_a_daemon_without_accepts():
    from sendspin_bridge.services.bluetooth.air_sensor_service import UnavailableAirSensorService

    client = FakeClient([])
    stub = UnavailableAirSensorService(
        loop=asyncio.get_running_loop(), resolve_client=lambda mac: client, reason="internal_error"
    )
    stub.subscribe(ENEBY)
    await _settle()
    stub.tick()
    assert client.sent_attempts == []


async def test_the_stand_in_runs_until_cancelled():
    from sendspin_bridge.services.bluetooth.air_sensor_service import UnavailableAirSensorService

    client = FakeClient([1])
    stub = UnavailableAirSensorService(
        loop=asyncio.get_running_loop(), resolve_client=lambda mac: client, reason="internal_error", repeat_s=0.01
    )
    stub.subscribe(ENEBY)
    task = asyncio.create_task(stub.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stub.stop()
    assert len(client.sent) >= 2


async def test_monitor_drops_warn_once_per_ten_minutes(air_env, caplog):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)

    def drop(total):
        env.hub.deliver(HubRecord(env.now_us, 1, MONITOR_EVENT_PKT, b"\x0e\x00", total, "kernel", 0))

    def warnings():
        return [r for r in caplog.records if r.levelname == "WARNING" and "dropped" in r.getMessage()]

    with caplog.at_level("WARNING", logger="sendspin_bridge.services.bluetooth.air_sensor_service"):
        drop(0)
        drop(2)
        drop(5)
        assert len(warnings()) == 1
        env.now_us += 601e6
        drop(6)
    assert len(warnings()) == 2
    assert all("\\x" not in r.getMessage() for r in warnings())  # counts only, never record bytes


# ── calibration air fold (A16b) ──────────────────────────────────────────


def _tool_phase_ms() -> float:
    """What ``scripts/a2dp_air_phase.py`` reports for the fixture (send anchor, unix axis)."""
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[4] / "scripts" / "a2dp_air_phase.py"
    spec = importlib.util.spec_from_file_location("a2dp_air_phase_a16b", path)
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    sys.modules["a2dp_air_phase_a16b"] = tool
    spec.loader.exec_module(tool)
    (rep,), _ = tool.analyze(FIX)
    return rep.phase_send_ms


TOOL_PHASE_MS = _tool_phase_ms()


async def fold_fixture(air_env, *, offset_us=OFFSET_US, mid_replay=None):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    fold_id = env.service.arm_fold(ENEBY, from_us=env.now_us)
    await env.replay_fixture(mid_replay=mid_replay)
    return env, env.service.finish_fold(fold_id, to_us=env.now_us + 1e6)


async def test_air_fold_on_the_fixture_equals_the_phase_tool(air_env):
    # the fixture's frames at RAW = unix·1e6 - OFFSET_US through the tracker's anchor give the tool's
    # phase - OFFSET mod cycle
    _env, res = await fold_fixture(air_env, offset_us=OFFSET_US)
    assert res.found and res.epochs == 1 and res.reason is None
    assert res.phase_ms == pytest.approx((TOOL_PHASE_MS - OFFSET_US / 1000.0) % 10_000.0, abs=0.5)
    assert (res.codec, res.rate, res.adapter) == ("sbc", 44100, "C0:FB:F9:62:D7:D6")
    assert res.frames == 2065 and res.clicks >= 2 and res.quality is not None and res.quality >= 3.0


async def test_air_fold_without_sbc_frames_is_not_found():
    from sendspin_bridge.services.bluetooth.air_sensor_service import AirClickFold

    f = AirClickFold(from_us=0.0)
    res = f.result(6e6)
    assert not res.found
    assert (res.phase_ms, res.reason, res.epochs, res.frames) == (None, "not_sbc", 0, 0)


def _flat_frame(sf=3):
    from sendspin_bridge.services.bluetooth.a2dp_air import ChannelMode, SbcFrame

    return SbcFrame(
        sampling_rate=44100,
        blocks=16,
        subbands=8,
        channel_mode=ChannelMode.JOINT_STEREO,
        allocation_snr=False,
        bitpool=53,
        length=119,
        join=(False,) * 8,
        scale_factors=((sf,) * 8, (sf,) * 8),
    )


async def test_air_fold_of_a_stream_without_clicks_is_no_air_clicks():
    from sendspin_bridge.services.bluetooth.air_sensor_service import AirClickFold

    f = AirClickFold(from_us=0.0)
    frame_us = 128 / 44100 * 1e6
    for k in range(round(8e6 / frame_us)):
        f.add_frame(k * frame_us, _flat_frame(), epoch=0)
    res = f.result(8e6)
    assert (res.found, res.phase_ms, res.reason, res.epochs, res.codec, res.rate) == (
        False,
        None,
        "no_air_clicks",
        1,
        "sbc",
        44100,
    )


async def test_air_fold_across_two_epochs_is_refused():
    from sendspin_bridge.services.bluetooth.air_sensor_service import AirClickFold

    f = AirClickFold(from_us=0.0)
    frame_us = 128 / 44100 * 1e6
    n = round(8e6 / frame_us)
    for k in range(n):
        f.add_frame(k * frame_us, _flat_frame(), epoch=0 if k < n // 2 else 1)
    res = f.result(8e6)
    assert (res.found, res.phase_ms, res.reason, res.epochs) == (False, None, "air_epoch_changed", 2)


async def test_air_fold_takes_only_frames_inside_its_span():
    from sendspin_bridge.services.bluetooth.air_sensor_service import AirClickFold

    f = AirClickFold(from_us=2e6, max_s=30.0)
    frame_us = 128 / 44100 * 1e6
    for k in range(round(10e6 / frame_us)):
        f.add_frame(k * frame_us, _flat_frame(), epoch=0 if k * frame_us < 1e6 else 1)
    res = f.result(9e6)
    assert res.epochs == 1  # the epoch-0 second lies before from_us
    assert res.frames == pytest.approx(7e6 / frame_us, abs=2)


async def test_air_fold_keeps_at_most_max_s():
    from sendspin_bridge.services.bluetooth.air_sensor_service import AirClickFold

    f = AirClickFold(from_us=0.0, max_s=5.0)
    frame_us = 128 / 44100 * 1e6
    for k in range(round(20e6 / frame_us)):
        f.add_frame(k * frame_us, _flat_frame(), epoch=0)
    assert f.held_s() <= 5.0 + 1e-3


async def test_an_epoch_change_inside_the_air_span_is_air_epoch_changed(air_env):
    def reopen_hub(env):
        env.service.on_hub_state("error", "monitor_error")
        env.service.on_hub_state("open", None)

    _env, res = await fold_fixture(air_env, mid_replay=reopen_hub)
    assert (res.found, res.phase_ms, res.reason) == (False, None, "air_epoch_changed")
    assert res.epochs == 2


async def test_the_hub_thread_never_folds(air_env, monkeypatch):
    import threading

    from sendspin_bridge.services.bluetooth import air_sensor_service as svc

    calls = []
    real = svc.click_phase_ms

    def spy(*a, **kw):
        calls.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(svc, "click_phase_ms", spy)
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    fold_id = env.service.arm_fold(ENEBY, from_us=env.now_us)
    await env.replay_fixture()  # every record is the hub thread's work
    assert calls == []
    res = env.service.finish_fold(fold_id, to_us=env.now_us + 1e6)
    assert res.found and len(calls) == 1


async def test_a_fold_of_an_unknown_id_is_not_armed(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    res = env.service.finish_fold("nope", to_us=1e6)
    assert (res.found, res.reason) == (False, "not_armed")


async def test_a_finished_fold_stops_collecting(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    fold_id = env.service.arm_fold(ENEBY, from_us=env.now_us)
    env.service.finish_fold(fold_id, to_us=env.now_us)
    assert env.service.finish_fold(fold_id, to_us=env.now_us).reason == "not_armed"
    await env.replay_fixture()  # nothing is collected for a finished fold (no error either)


async def test_arming_again_replaces_the_devices_fold(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    first = env.service.arm_fold(ENEBY, from_us=env.now_us)
    second = env.service.arm_fold(ENEBY, from_us=env.now_us)
    await env.replay_fixture()
    assert env.service.finish_fold(first, to_us=env.now_us + 1e6).reason == "not_armed"
    assert env.service.finish_fold(second, to_us=env.now_us + 1e6).found


# ── fold bounds and the undecoded fast path (A16b fix round 1) ───────────


async def test_the_fold_energy_comes_from_undecoded_frames(air_env, monkeypatch):
    """The live parser leaves frames undecoded; an armed fold must not decode them on the hub thread."""
    from sendspin_bridge.services.bluetooth.a2dp_air import SbcFrames

    def no_decode(self):
        raise AssertionError("decode() called on the hub thread")

    monkeypatch.setattr(SbcFrames, "decode", no_decode)
    _env, res = await fold_fixture(air_env)
    assert res.found and res.frames == 2065


async def test_the_undecoded_fold_equals_the_decoded_fold(air_env):
    from sendspin_bridge.services.bluetooth.a2dp_air_fold import frame_energy, sbc_chirp_energies

    recs = list(read_btsnoop(FIX))
    undecoded = [p for p in parse_records(recs, A2dpAirParser(check_crc="first", decode_frames=False)) if p.sbc]
    decoded = [p for p in parse_records(recs, A2dpAirParser(check_crc="first")) if p.frames]
    assert [e for p in undecoded for e in sbc_chirp_energies(p.sbc)] == [
        frame_energy(f) for p in decoded for f in p.frames
    ]
    _env, res_live = await fold_fixture(air_env)
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service._parser = A2dpAirParser(check_crc="first")  # the decoding parser: SbcFrame path
    env.service.subscribe(ENEBY)
    fold_id = env.service.arm_fold(ENEBY, from_us=env.now_us)
    await env.replay_fixture()
    assert env.service.finish_fold(fold_id, to_us=env.now_us + 1e6) == res_live


async def test_an_armed_fold_expires_and_stops_collecting(air_env, monkeypatch):
    """Past max_s + slack after from_us the hub path stops computing energies; finish still answers
    with the frames collected until then (documented choice)."""
    from sendspin_bridge.services.bluetooth import air_sensor_service as svc

    monkeypatch.setattr(svc, "FOLD_EXPIRY_SLACK_S", 0.5)
    seen = []
    real = svc.sbc_chirp_energies

    def spy(frames):
        seen.append(env.now_us)
        return real(frames)

    monkeypatch.setattr(svc, "sbc_chirp_energies", spy)
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    start = env.now_us
    fold_id = env.service.arm_fold(ENEBY, from_us=start, max_s=1.0)
    await env.replay_fixture()
    assert seen and max(seen) - start <= 1.5e6 + 0.3e6  # the packet that crossed the limit, then nothing
    assert len(seen) < 295 / 2
    res = env.service.finish_fold(fold_id, to_us=env.now_us)
    assert res.reason != "not_armed" and 0 < res.frames <= round(1.0 / (128 / 44100)) + 7


async def test_unsubscribe_drops_the_speakers_fold(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    fold_id = env.service.arm_fold(ENEBY, from_us=env.now_us)
    env.service.unsubscribe(ENEBY)
    assert env.service.finish_fold(fold_id, to_us=env.now_us).reason == "not_armed"


async def test_stop_drops_every_fold(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    fold_id = env.service.arm_fold(ENEBY, from_us=env.now_us)
    await env.service.stop()
    assert env.service.finish_fold(fold_id, to_us=env.now_us).reason == "not_armed"


async def test_a_resubscribe_continues_the_speakers_epochs(air_env):
    # The same daemon sees a new epoch after an unsubscribe/subscribe, never epoch 0 again.
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    await env.ticks(8)
    first = [c.epoch for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)]
    assert first
    env.service.unsubscribe(ENEBY)
    env.client(ENEBY).sent.clear()
    env.service.subscribe(ENEBY)
    await env.replay_fixture()
    await env.ticks(8)
    second = [c.epoch for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)]
    assert second and min(second) > max(first)


# -- the service contains its own errors (a late death never leaves stale sources) -----------------


async def test_a_dying_run_hands_over_to_an_unavailable_stand_in(air_env, monkeypatch, caplog):
    from sendspin_bridge.services.bluetooth import air_sensor_service as air_mod

    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    resubscribed: list[str] = []
    service = env.service

    def resync():
        current = air_mod.get_air_sensor_service()
        service.unsubscribe(ENEBY) if current is not service else None
        current.subscribe(ENEBY)
        resubscribed.append(type(current).__name__)

    env.client(ENEBY).sync_air_subscription = resync
    air_mod.set_air_sensor_service(service)
    service.subscribe(ENEBY)
    assert env.hub.consumers_wanting_acl_tx() == 1
    monkeypatch.setattr(service, "_tick_s", None)  # the loop's own sleep fails: run() dies late
    task = asyncio.create_task(service.run())
    for _ in range(20):
        await asyncio.sleep(0)
    assert not task.done()  # it keeps running as the stand-in
    stand_in = air_mod.get_air_sensor_service()
    assert isinstance(stand_in, air_mod.UnavailableAirSensorService)
    assert stand_in.status() == {"state": "failed", "reason": "internal_error"}
    assert resubscribed == ["UnavailableAirSensorService"]
    assert env.hub.consumers_wanting_acl_tx() == 0  # no longer a hub consumer
    src = [c for c in env.client(ENEBY).sent if isinstance(c, AirSource)][-1]
    assert (src.state, src.reason) == ("unavailable", "internal_error")
    assert any("air sensor" in r.getMessage().lower() for r in caplog.records)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert air_mod.get_air_sensor_service() is None  # the stand-in leaves with the task
    air_mod.set_air_sensor_service(None)


async def test_a_dead_sender_is_restarted_by_the_next_tick(air_env):
    env = await air_env(peers={(1, 70): ENEBY}, accepts=[1])
    env.service.subscribe(ENEBY)
    await env.ticks(1)
    dev = env.service._devices[ENEBY]
    dev.task.cancel()  # the sender died
    await env.ticks(1)
    env.client(ENEBY).sent.clear()
    await env.replay_fixture()
    await env.ticks(8)
    assert [c for c in env.client(ENEBY).sent if isinstance(c, AirEnvelope)]
