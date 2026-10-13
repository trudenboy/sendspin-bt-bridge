"""A ``pulse_sink`` player follows its device across unplug and replug.

The client is wired to a real :class:`SinkMonitor` driven by fake PulseAudio
events and a fake inventory.  Stream moves, the standby null sink, daemon IPC
and config writes are recorded, never performed.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.audio.pulse import STANDBY_SINK_NAME
from sendspin_bridge.services.audio.sink_inventory import LocalSink, SinkInventoryUnavailable
from sendspin_bridge.services.audio.sink_monitor import SinkMonitor
from sendspin_bridge.services.ipc.commands import SetStandby

SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"
RENAMED = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo.2"
OTHER = "alsa_output.pci-0000_00_1f.3.analog-stereo"
SERIAL = "Generic_USB_DAC_0000A1"


def _sink(name: str, state: str = "idle", **ids: str) -> LocalSink:
    return LocalSink(name, name, state, "s16le 2ch 48000Hz", False, 100, None, None, dict(ids))


class _Inventory:
    def __init__(self, *sinks: LocalSink) -> None:
        self.sinks = list(sinks)
        self.fail = False

    async def __call__(self) -> list[LocalSink]:
        if self.fail:
            raise SinkInventoryUnavailable("no pulse")
        return list(self.sinks)


class _Audio:
    """Records what the client asks PulseAudio and the daemon to do."""

    def __init__(self, monkeypatch, *, null_sink_ok: bool = True) -> None:
        self.moves: list[tuple[int, str]] = []
        self.commands: list[object] = []
        self.events: list[tuple[str, str, dict]] = []
        self.config_writes: list[dict] = []
        self.spawned: list[tuple[dict, dict]] = []
        self.null_sink_ok = null_sink_ok

        async def fake_move(pid: int, sink: str) -> int:
            self.moves.append((pid, sink))
            return 1

        async def fake_null_sink() -> bool:
            return self.null_sink_ok

        def fake_event(device_id, event_type, **kwargs):
            self.events.append((device_id, str(getattr(event_type, "value", event_type)), kwargs.get("details") or {}))

        config = {
            "players": [
                {"id": "dac-1", "name": "Desk DAC", "backend": {"type": "pulse_sink", "sink": SINK, "match": {}}}
            ]
        }

        def fake_update_config(mutator):
            mutator(config)
            self.config_writes.append(json.loads(json.dumps(config)))

        async def fake_exec(*args, env=None, **_kw):
            self.spawned.append((json.loads(args[3]), dict(env or {})))
            raise RuntimeError("intentional short-circuit")

        monkeypatch.setattr("sendspin_bridge.services.audio.pulse.amove_pid_sink_inputs", fake_move)
        monkeypatch.setattr("sendspin_bridge.services.audio.pulse.aensure_null_sink", fake_null_sink)
        monkeypatch.setattr("sendspin_bridge.bridge.state.publish_device_event", fake_event)
        monkeypatch.setattr("sendspin_bridge.config.update_config", fake_update_config)
        monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
        monkeypatch.setattr("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", fake_exec)

    def event_types(self) -> list[str]:
        return [e[1] for e in self.events]


def _client(audio: _Audio, sink: str | None, *, daemon_running: bool) -> SendspinClient:
    client = SendspinClient("Desk DAC", "localhost", 9000)
    client.player_id = client.config_id = "dac-1"
    client.backend_type = "pulse_sink"
    client.volume_controller = "sendspin"
    client.bluetooth_sink_name = sink
    client.sink_match = {"serial": SERIAL}
    client.configured_sink = SINK
    client.running = True
    client._start_sendspin_lock = asyncio.Lock()

    async def record_command(cmd):
        audio.commands.append(cmd)

    client._send_subprocess_command = record_command  # type: ignore[method-assign]
    if daemon_running:
        client._daemon_proc = SimpleNamespace(pid=4242, returncode=None)  # type: ignore[assignment]

    async def fake_stop():
        audio.commands.append("stop_sendspin")
        client._daemon_proc = None

    client.stop_sendspin = fake_stop  # type: ignore[method-assign]
    return client


async def _settle(client: SendspinClient) -> None:
    while client._output_tasks:
        await asyncio.gather(*list(client._output_tasks))


def _pulse_with(name: str) -> AsyncMock:
    pulse = AsyncMock()
    info = MagicMock()
    info.name = name
    info.state = 1
    pulse.sink_info.return_value = info
    return pulse


def _wired(audio: _Audio, inv: _Inventory, sink: str | None, *, daemon_running: bool):
    client = _client(audio, sink, daemon_running=daemon_running)
    mon = SinkMonitor(inventory=inv)
    client.follow_output(mon)
    if sink:
        mon._sink_index_to_name[7] = sink
    return client, mon


# ── Unplug ──────────────────────────────────────────────────────────────


async def test_unplug_while_running_parks_the_daemon_on_the_standby_sink(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL), _sink(OTHER))
    client, mon = _wired(audio, inv, SINK, daemon_running=True)

    inv.sinks = [_sink(OTHER)]
    await mon._dispatch_event(AsyncMock(), "remove", 7)
    await _settle(client)

    assert SetStandby(sink=STANDBY_SINK_NAME) in audio.commands
    assert audio.moves == [(4242, STANDBY_SINK_NAME)]
    assert client.status["sink_missing"] is True
    assert client.status["sink_match_status"] == "sink_missing"
    assert "sink-lost" in audio.event_types()


async def test_while_unplugged_nothing_is_routed_to_another_output(monkeypatch):
    """Review focus 3: the only move target is the bridge's standby null sink."""
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL), _sink(OTHER))
    client, mon = _wired(audio, inv, SINK, daemon_running=True)

    inv.sinks = [_sink(OTHER)]
    await mon._dispatch_event(AsyncMock(), "remove", 7)
    await mon.refresh_presence()  # a later poll / new-sink event changes nothing
    await mon._dispatch_event(_pulse_with(OTHER), "new", 8)
    await _settle(client)

    assert {sink for _pid, sink in audio.moves} == {STANDBY_SINK_NAME}
    standby_cmds = [c for c in audio.commands if isinstance(c, SetStandby)]
    assert standby_cmds == [SetStandby(sink=STANDBY_SINK_NAME)]
    assert audio.spawned == []


async def test_unplug_without_a_standby_sink_stops_the_daemon_instead(monkeypatch):
    audio = _Audio(monkeypatch, null_sink_ok=False)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, mon = _wired(audio, inv, SINK, daemon_running=True)

    inv.sinks = []
    await mon._dispatch_event(AsyncMock(), "remove", 7)
    await _settle(client)

    assert audio.moves == []
    assert "stop_sendspin" in audio.commands
    assert client.status["sink_missing"] is True


# ── Replug ──────────────────────────────────────────────────────────────


async def test_replug_restores_the_daemon_to_its_sink(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, mon = _wired(audio, inv, SINK, daemon_running=True)
    inv.sinks = []
    await mon._dispatch_event(AsyncMock(), "remove", 7)
    await _settle(client)
    audio.commands.clear()
    audio.moves.clear()

    inv.sinks = [_sink(SINK, serial=SERIAL)]
    await mon._dispatch_event(_pulse_with(SINK), "new", 9)
    await _settle(client)

    assert audio.commands[0] == SetStandby(sink=SINK)
    assert audio.moves == [(4242, SINK)]
    assert client.status["sink_missing"] is False
    assert client.status["sink_match_status"] == "ok"
    assert client.bluetooth_sink_name == SINK
    assert "sink-restored" in audio.event_types()
    assert "sink-rematched" not in audio.event_types()
    assert audio.config_writes == []


async def test_replug_under_a_new_name_is_rematched_and_saved(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, mon = _wired(audio, inv, SINK, daemon_running=True)
    inv.sinks = []
    await mon._dispatch_event(AsyncMock(), "remove", 7)
    await _settle(client)
    audio.moves.clear()

    inv.sinks = [_sink(RENAMED, serial=SERIAL)]
    await mon._dispatch_event(_pulse_with(RENAMED), "new", 9)
    await _settle(client)

    assert SetStandby(sink=RENAMED) in audio.commands
    assert audio.moves == [(4242, RENAMED)]
    assert client.bluetooth_sink_name == RENAMED
    assert client.status["audio_sink"] == RENAMED
    assert "sink-rematched" in audio.event_types()
    assert "sink-restored" in audio.event_types()
    assert audio.config_writes[-1]["players"][0]["backend"]["sink"] == RENAMED
    assert client.configured_sink == RENAMED


async def test_a_player_whose_sink_was_absent_at_start_starts_when_it_appears(monkeypatch):
    """Review focus 5."""
    audio = _Audio(monkeypatch)
    inv = _Inventory()
    client, mon = _wired(audio, inv, None, daemon_running=False)
    client._update_status({"sink_missing": True, "sink_match_status": "sink_missing"})

    inv.sinks = [_sink(SINK, serial=SERIAL)]
    await mon._dispatch_event(_pulse_with(SINK), "new", 9)
    await _settle(client)

    assert len(audio.spawned) == 1
    params, env = audio.spawned[0]
    assert env["PULSE_SINK"] == SINK
    assert params["bluetooth_sink_name"] == SINK
    assert audio.moves == []
    assert client.status["sink_missing"] is False


async def test_a_stopped_player_does_not_start_when_its_sink_appears(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory()
    client, mon = _wired(audio, inv, None, daemon_running=False)
    client.running = False

    inv.sinks = [_sink(SINK, serial=SERIAL)]
    await mon._dispatch_event(_pulse_with(SINK), "new", 9)
    await _settle(client)

    assert audio.spawned == []


# ── Polling fallback ────────────────────────────────────────────────────


async def test_polling_detects_unplug_and_replug_the_same_way(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, mon = _wired(audio, inv, SINK, daemon_running=True)

    inv.sinks = []
    await mon.refresh_presence()
    await _settle(client)
    assert client.status["sink_missing"] is True
    assert audio.moves == [(4242, STANDBY_SINK_NAME)]

    inv.sinks = [_sink(SINK, serial=SERIAL)]
    await mon.refresh_presence()
    await _settle(client)
    assert client.status["sink_missing"] is False
    assert audio.moves[-1] == (4242, SINK)
    assert audio.event_types().count("sink-lost") == 1
    assert audio.event_types().count("sink-restored") == 1


# ── Supervisor restart re-matches the sink ──────────────────────────────


async def test_a_restart_after_the_sink_vanished_does_not_spawn(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, _mon = _wired(audio, inv, SINK, daemon_running=False)

    inv.sinks = [_sink(OTHER)]
    await client.start_sendspin()
    await _settle(client)

    assert audio.spawned == []
    assert client.bluetooth_sink_name is None
    assert client.status["sink_missing"] is True
    assert "sink-lost" in audio.event_types()


async def test_a_restart_follows_a_renamed_sink(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(RENAMED, serial=SERIAL))
    client, _mon = _wired(audio, inv, SINK, daemon_running=False)

    await client.start_sendspin()

    assert len(audio.spawned) == 1
    assert audio.spawned[0][1]["PULSE_SINK"] == RENAMED
    assert "sink-rematched" in audio.event_types()


async def test_a_restart_keeps_the_sink_when_the_inventory_is_unreadable(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, _mon = _wired(audio, inv, SINK, daemon_running=False)

    inv.fail = True
    await client.start_sendspin()

    assert len(audio.spawned) == 1
    assert audio.spawned[0][1]["PULSE_SINK"] == SINK


# ── Unplug racing a start ───────────────────────────────────────────────


async def _unplug(inv: _Inventory, mon: SinkMonitor) -> None:
    inv.sinks = [_sink(OTHER)]
    await mon._dispatch_event(AsyncMock(), "remove", 7)


async def test_an_unplug_after_the_rematch_but_before_the_spawn_starts_nothing(monkeypatch):
    """The output goes while the old daemon stops: never start without PULSE_SINK."""
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL), _sink(OTHER))
    client, mon = _wired(audio, inv, SINK, daemon_running=False)

    async def stop_then_unplug():
        audio.commands.append("stop_sendspin")
        client._daemon_proc = None
        await _unplug(inv, mon)
        await _settle(client)

    client.stop_sendspin = stop_then_unplug  # type: ignore[method-assign]
    await client.start_sendspin()
    await _settle(client)

    assert audio.spawned == []
    assert client.status["sink_missing"] is True


async def test_an_unplug_during_the_spawn_parks_the_new_daemon_on_the_standby_sink(monkeypatch):
    """The remove event lands while the subprocess is being created."""
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL), _sink(OTHER))
    client, mon = _wired(audio, inv, SINK, daemon_running=False)

    async def exec_while_unplugging(*args, env=None, **_kw):
        audio.spawned.append((json.loads(args[3]), dict(env or {})))
        await _unplug(inv, mon)
        for _ in range(5):  # let the scheduled output change run if it can
            await asyncio.sleep(0)
        return SimpleNamespace(pid=5151, returncode=None)

    async def idle_reader():
        await asyncio.Event().wait()

    monkeypatch.setattr("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", exec_while_unplugging)
    client._read_subprocess_output = idle_reader  # type: ignore[method-assign]
    client._read_subprocess_stderr = idle_reader  # type: ignore[method-assign]

    await client.start_sendspin()
    await _settle(client)
    try:
        assert audio.spawned[0][1]["PULSE_SINK"] == SINK
        assert client.status["sink_missing"] is True
        assert SetStandby(sink=STANDBY_SINK_NAME) in audio.commands
        assert audio.moves == [(5151, STANDBY_SINK_NAME)]
    finally:
        for task in (client._daemon_task, client._stderr_task):
            if task is not None:
                task.cancel()


async def test_a_failing_output_change_is_logged_when_it_fails(monkeypatch, caplog):
    audio = _Audio(monkeypatch)
    client, _mon = _wired(audio, _Inventory(), None, daemon_running=False)

    async def broken():
        raise RuntimeError("pulse went away mid-move")

    with caplog.at_level("ERROR", logger="sendspin_bridge.bridge.client"):
        client._schedule_output_change(broken())
        for _ in range(3):
            await asyncio.sleep(0)

    assert client._output_tasks == set()
    failures = [r for r in caplog.records if r.name == "sendspin_bridge.bridge.client" and r.exc_info]
    assert len(failures) == 1
    assert "pulse went away mid-move" in str(failures[0].exc_info[1])


# ── power_save idle detection ───────────────────────────────────────────


async def test_a_present_sink_drives_the_power_save_idle_machine(monkeypatch):
    audio = _Audio(monkeypatch)
    inv = _Inventory(_sink(SINK, state="idle", serial=SERIAL))
    client = _client(audio, SINK, daemon_running=True)
    seen: list[str] = []
    client._on_sink_idle = lambda: seen.append("idle")  # type: ignore[method-assign]
    client._on_sink_active = lambda: seen.append("active")  # type: ignore[method-assign]
    mon = SinkMonitor(inventory=inv)

    client.follow_output(mon)
    await mon.refresh_presence()

    assert seen == ["idle"]


def test_stop_following_by_a_retired_client_keeps_its_successor_registered(monkeypatch):
    audio = _Audio(monkeypatch)
    mon = SinkMonitor(inventory=_Inventory())
    old = _client(audio, SINK, daemon_running=False)
    new = _client(audio, SINK, daemon_running=False)

    old.follow_output(mon)
    new.follow_output(mon)
    old.stop_following_output()

    assert mon.has_presence("dac-1")


# ── Volume persisted by player id ───────────────────────────────────────


async def test_a_volume_change_is_saved_by_player_id(monkeypatch):
    audio = _Audio(monkeypatch)
    client = _client(audio, SINK, daemon_running=True)
    persisted: list[tuple] = []
    client._schedule_persist = lambda key, fn, ident, value: persisted.append((key, fn.__name__, ident, value))  # type: ignore[method-assign]
    client._ipc_service.handle_message = lambda msg: {"volume": 37}  # type: ignore[method-assign]

    await client._handle_daemon_message({"type": "status", "volume": 37})

    assert persisted == [("volume", "save_player_volume", "dac-1", 37)]


@pytest.mark.parametrize("volume", [0, 55])
def test_save_player_volume_writes_player_state_by_id(tmp_config, volume):
    import sendspin_bridge.config as config_module

    path = tmp_config
    path.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [{"id": "dac-1", "name": "Desk", "backend": {"type": "pulse_sink", "sink": SINK}}],
                "PLAYER_STATE": {},
            }
        )
    )

    config_module.save_player_volume("dac-1", volume)

    assert json.loads(path.read_text())["PLAYER_STATE"]["dac-1"]["volume"] == volume
    assert config_module.load_player_volume("dac-1") == volume
    assert config_module.load_player_volume("unknown") is None


# ── Startup wiring ──────────────────────────────────────────────────────


async def test_startup_publishes_the_sink_monitor_and_polls_at_the_bt_check_interval(monkeypatch):
    from sendspin_bridge.bridge.orchestrator import BridgeOrchestrator
    from sendspin_bridge.services.audio import sink_monitor as sink_monitor_module
    from sendspin_bridge.services.lifecycle import bridge_runtime_state as runtime

    observed: dict[str, object] = {}

    class _FakeMonitor:
        def __init__(self) -> None:
            observed["monitor"] = self

        async def start(self) -> None:
            return None

        def start_presence_polling(self, interval: float) -> None:
            observed["interval"] = interval

        async def stop(self) -> None:
            observed["stopped"] = True

    monkeypatch.setattr(sink_monitor_module, "SinkMonitor", _FakeMonitor)
    orchestrator = BridgeOrchestrator()
    bootstrap = SimpleNamespace(
        config={}, bt_check_interval=7, base_listen_port=8928, server_host="auto", demo_mode=False
    )
    loader = object()

    def fake_initialize_devices(*_a, **kwargs):
        observed["load_player_volume_fn"] = kwargs.get("load_player_volume_fn")
        return SimpleNamespace(clients=[])

    async def fake_read_local_sinks_for(_bootstrap):
        return []

    async def fake_run_runtime(clients, **_kw):
        observed["published"] = runtime.get_sink_monitor()
        return "done"

    async def _noop(*_a, **_kw):
        return None

    async def fake_ma(*_a, **_kw):
        return SimpleNamespace(ma_monitor_task=None)

    monkeypatch.setattr(orchestrator, "read_local_sinks_for", fake_read_local_sinks_for)
    monkeypatch.setattr(orchestrator, "initialize_devices", fake_initialize_devices)
    monkeypatch.setattr(orchestrator, "configure_executor", _noop)
    monkeypatch.setattr(orchestrator, "start_web_server", _noop)
    monkeypatch.setattr(orchestrator, "install_signal_handlers", lambda *_a, **_kw: None)
    monkeypatch.setattr(orchestrator, "initialize_ma_integration", fake_ma)
    monkeypatch.setattr(orchestrator, "_run_duplicate_device_check", _noop)
    monkeypatch.setattr(orchestrator, "run_runtime", fake_run_runtime)
    monkeypatch.setattr(
        "sendspin_bridge.services.bluetooth.hci_avrcp_monitor.get_monitor",
        lambda: SimpleNamespace(start=_noop, stop=_noop),
    )

    result = await orchestrator.run_bridge_lifecycle(
        bootstrap,  # type: ignore[arg-type]
        version="x",
        client_factory=object,
        bt_manager_factory=object,
        load_player_volume_fn=loader,  # type: ignore[arg-type]
    )

    assert result == "done"
    assert observed["published"] is observed["monitor"]
    assert observed["interval"] == 7.0
    assert observed["load_player_volume_fn"] is loader
    assert runtime.get_sink_monitor() is None
    assert observed["stopped"] is True


async def test_a_player_started_later_follows_its_output_through_the_published_monitor(monkeypatch):
    from sendspin_bridge.services.lifecycle import bridge_runtime_state as runtime

    audio = _Audio(monkeypatch)
    mon = SinkMonitor(inventory=_Inventory(_sink(SINK, serial=SERIAL)))
    client = _client(audio, SINK, daemon_running=False)
    client.running = False
    runtime.set_sink_monitor(mon)
    try:
        client._status_monitor_loop = AsyncMock()  # type: ignore[method-assign]
        task = asyncio.create_task(client.run())
        for _ in range(50):
            if mon.has_presence("dac-1"):
                break
            await asyncio.sleep(0.01)
        assert mon.has_presence("dac-1")
        client.running = False
        await task
    finally:
        runtime.set_sink_monitor(None)

    # Its run loop ended: it no longer follows the output.
    assert not mon.has_presence("dac-1")


# ── Optional reset of the output before the first start ─────────────────


def _record_suspends(monkeypatch) -> list[tuple[str, bool]]:
    calls: list[tuple[str, bool]] = []

    async def fake_suspend(sink_name: str, suspend: bool) -> bool:
        calls.append((sink_name, suspend))
        return True

    monkeypatch.setattr("sendspin_bridge.services.audio.pulse.asuspend_sink", fake_suspend)
    return calls


async def test_a_reset_suspends_and_resumes_the_sink_once_before_the_first_start(monkeypatch):
    audio = _Audio(monkeypatch)
    suspends = _record_suspends(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, _mon = _wired(audio, inv, SINK, daemon_running=False)
    client.reset_output_on_start = True

    await client.start_sendspin()
    await client.start_sendspin()

    assert suspends == [(SINK, True), (SINK, False)]
    assert len(audio.spawned) == 2


async def test_without_a_reset_the_sink_is_never_suspended(monkeypatch):
    audio = _Audio(monkeypatch)
    suspends = _record_suspends(monkeypatch)
    inv = _Inventory(_sink(SINK, serial=SERIAL))
    client, _mon = _wired(audio, inv, SINK, daemon_running=False)

    await client.start_sendspin()

    assert suspends == []
    assert len(audio.spawned) == 1


async def test_a_reset_waits_for_the_sink_to_be_present(monkeypatch):
    audio = _Audio(monkeypatch)
    suspends = _record_suspends(monkeypatch)
    inv = _Inventory()
    client, _mon = _wired(audio, inv, None, daemon_running=False)
    client.reset_output_on_start = True

    await client.start_sendspin()

    assert suspends == []
    assert audio.spawned == []
    assert client.reset_output_on_start is True


@pytest.mark.parametrize("setting", [True, False])
def test_the_startup_reset_setting_reaches_the_activation_context(setting):
    from sendspin_bridge.bridge.orchestrator import BridgeOrchestrator
    from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_activation_context

    bootstrap = SimpleNamespace(
        config={"PULSE_SINK_RESET_ON_START": setting},
        device_configs=[],
        server_host="localhost",
        server_port=9000,
        effective_bridge="",
        prefer_sbc=False,
        bt_check_interval=10,
        bt_max_reconnect_fails=0,
        bt_churn_threshold=0,
        bt_churn_window=300.0,
        enable_a2dp_sink_recovery_dance=False,
        enable_pa_module_reload=False,
        enable_adapter_auto_recovery=False,
        enable_rssi_badge=False,
        log_level="INFO",
        pulse_latency_msec=200,
    )

    BridgeOrchestrator().initialize_devices(
        bootstrap,  # type: ignore[arg-type]
        client_factory=MagicMock(),
        bt_manager_factory=MagicMock(),
        filter_devices_fn=lambda devices: devices,
    )

    context = get_activation_context()
    assert context is not None
    assert context.pulse_sink_reset_on_start is setting
