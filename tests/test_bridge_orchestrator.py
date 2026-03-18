"""Focused tests for the first BridgeOrchestrator slice."""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

import config
import state
from bridge_orchestrator import BridgeOrchestrator


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "SENDSPIN_SERVER": "music-assistant.local",
                "SENDSPIN_PORT": 9000,
                "PULSE_LATENCY_MSEC": 250,
                "LOG_LEVEL": "DEBUG",
            }
        )
    )
    state.reset_startup_progress()
    state.set_runtime_mode_info(None)
    state.set_clients([])
    state.set_ma_api_credentials("", "")
    state.set_ma_groups({}, [])
    state.set_ma_connected(False)


@pytest.mark.asyncio
async def test_initialize_runtime_loads_config_and_updates_progress():
    orchestrator = BridgeOrchestrator()

    bootstrap = await orchestrator.initialize_runtime()

    assert bootstrap.server_host == "music-assistant.local"
    assert bootstrap.server_port == 9000
    assert bootstrap.pulse_latency_msec == 250
    assert bootstrap.log_level == "DEBUG"
    progress = state.get_startup_progress()
    assert progress["phase"] == "config"
    assert progress["status"] == "running"
    assert progress["details"]["demo_mode"] is False
    runtime_info = state.get_runtime_mode_info()
    assert runtime_info["mode"] == "production"


@pytest.mark.asyncio
async def test_initialize_runtime_invokes_demo_install_when_enabled(monkeypatch):
    orchestrator = BridgeOrchestrator()
    called: list[str] = []
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setattr("demo.install", lambda: called.append("install"))

    bootstrap = await orchestrator.initialize_runtime()

    assert bootstrap.demo_mode is True
    assert called == ["install"]
    progress = state.get_startup_progress()
    assert progress["details"]["demo_mode"] is True


@pytest.mark.asyncio
async def test_configure_executor_registers_main_loop_and_web_progress():
    orchestrator = BridgeOrchestrator()
    loop = asyncio.get_running_loop()
    original_executor = getattr(loop, "_default_executor", None)

    try:
        pool_size = await orchestrator.configure_executor(3, web_thread_name="WebServer")
        assert pool_size == 10
        assert state.get_main_loop() is loop
        progress = state.get_startup_progress()
        assert progress["phase"] == "web"
        assert progress["details"]["web_thread"] == "WebServer"
    finally:
        current_executor = getattr(loop, "_default_executor", None)
        if current_executor is not None and current_executor is not original_executor:
            current_executor.shutdown(wait=False, cancel_futures=True)
        loop._default_executor = original_executor


def test_start_web_server_publishes_clients_before_running_web_main():
    orchestrator = BridgeOrchestrator()
    clients = [SimpleNamespace(player_name="Kitchen")]
    seen = threading.Event()

    def fake_web_main() -> None:
        snapshot = state.get_clients_snapshot()
        assert snapshot == clients
        seen.set()

    thread = orchestrator.start_web_server(clients, web_main=fake_web_main, thread_name="TestWebServer")
    thread.join(timeout=1)

    assert seen.is_set()
    assert thread.name == "TestWebServer"


@pytest.mark.asyncio
async def test_graceful_shutdown_mutes_sinks_and_stops_clients():
    orchestrator = BridgeOrchestrator()
    stopped: list[str] = []
    muted: list[tuple[str, bool]] = []

    class FakeClient:
        def __init__(self, name: str, sink: str | None):
            self.player_name = name
            self.bluetooth_sink_name = sink
            self.running = True

        async def stop_sendspin(self) -> None:
            stopped.append(self.player_name)

    async def fake_mute_sink(sink: str, muted_flag: bool) -> bool:
        muted.append((sink, muted_flag))
        return True

    clients = [FakeClient("Kitchen", "sink.one"), FakeClient("Bedroom", None)]

    await orchestrator.graceful_shutdown(clients=clients, mute_sink=fake_mute_sink)

    assert muted == [("sink.one", True)]
    assert stopped == ["Kitchen", "Bedroom"]
    assert all(client.running is False for client in clients)


def test_install_signal_handlers_schedules_shutdown_factory():
    orchestrator = BridgeOrchestrator()
    scheduled = []
    callbacks = []

    class FakeLoop:
        def add_signal_handler(self, sig, callback):
            callbacks.append((sig, callback))

        def create_task(self, coro):
            scheduled.append(coro)

    async def fake_shutdown() -> None:
        return None

    loop = FakeLoop()
    orchestrator.install_signal_handlers(loop, shutdown_factory=fake_shutdown)

    assert len(callbacks) == 2
    callbacks[0][1]()
    assert len(scheduled) == 1
    scheduled[0].close()


@pytest.mark.asyncio
async def test_initialize_ma_integration_autodetects_addon_url(monkeypatch):
    orchestrator = BridgeOrchestrator()
    monkeypatch.setenv("SUPERVISOR_TOKEN", "demo-supervisor-token")
    config_data = {
        "MA_API_URL": "",
        "MA_API_TOKEN": "",
        "MA_WEBSOCKET_MONITOR": False,
    }

    bootstrap = await orchestrator.initialize_ma_integration(config_data, [], server_host="ma-host.local")

    assert bootstrap.ma_api_url == "http://ma-host.local:8095"
    assert bootstrap.ma_api_token == ""
    progress = state.get_startup_progress()
    assert progress["phase"] == "integrations"
    assert progress["details"]["ma_configured"] is False


@pytest.mark.asyncio
async def test_initialize_ma_integration_discovers_groups_and_starts_monitor(monkeypatch):
    orchestrator = BridgeOrchestrator()
    monitor_started = asyncio.Event()

    async def fake_discover(_url, _token, player_info):
        return (
            {"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen Group"}},
            [{"id": "syncgroup_1", "name": "Kitchen Group", "members": []}],
        )

    class FakeMonitor:
        async def run(self) -> None:
            monitor_started.set()
            await asyncio.sleep(3600)

    monkeypatch.setattr("services.ma_client.discover_ma_groups", fake_discover)
    monkeypatch.setattr("services.ma_monitor.start_monitor", lambda _url, _token: FakeMonitor())
    clients = [SimpleNamespace(player_id="sendspin-kitchen", player_name="Kitchen")]

    bootstrap = await orchestrator.initialize_ma_integration(
        {
            "MA_API_URL": "http://ma.local:8095",
            "MA_API_TOKEN": "token",
            "MA_WEBSOCKET_MONITOR": True,
        },
        clients,
        server_host="music-assistant.local",
    )

    assert bootstrap.ma_api_url == "http://ma.local:8095"
    assert bootstrap.ma_api_token == "token"
    assert bootstrap.ma_monitor_task is not None
    await asyncio.wait_for(monitor_started.wait(), timeout=0.2)
    assert state.get_ma_groups()[0]["id"] == "syncgroup_1"
    assert state.is_ma_connected() is True
    progress = state.get_startup_progress()
    assert progress["phase"] == "integrations"
    assert progress["details"]["ma_configured"] is True
    assert progress["details"]["ma_monitor_enabled"] is True

    bootstrap.ma_monitor_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bootstrap.ma_monitor_task


@pytest.mark.asyncio
async def test_assemble_runtime_tasks_marks_startup_complete_and_wires_demo_helpers():
    orchestrator = BridgeOrchestrator()
    client_runs: list[str] = []
    update_checker_started = asyncio.Event()
    simulator_started = asyncio.Event()

    class FakeClient:
        def __init__(self, name: str):
            self.player_name = name

        async def run(self) -> None:
            client_runs.append(self.player_name)
            await asyncio.sleep(3600)

    async def fake_update_checker(_version: str) -> None:
        update_checker_started.set()
        await asyncio.sleep(3600)

    async def fake_simulator(clients) -> None:
        assert len(clients) == 2
        simulator_started.set()
        await asyncio.sleep(3600)

    clients = [FakeClient("Kitchen"), FakeClient("Bedroom")]
    tasks = orchestrator.assemble_runtime_tasks(
        clients,
        ma_monitor_task=None,
        demo_mode=True,
        version="2.32.12",
        run_simulator_fn=fake_simulator,
        run_update_checker_fn=fake_update_checker,
    )

    await asyncio.wait_for(update_checker_started.wait(), timeout=0.2)
    await asyncio.wait_for(simulator_started.wait(), timeout=0.2)
    assert sorted(client_runs) == ["Bedroom", "Kitchen"]
    progress = state.get_startup_progress()
    assert progress["status"] == "ready"
    assert progress["phase"] == "ready"
    assert progress["details"]["demo_mode"] is True
    assert progress["details"]["active_clients"] == 2

    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_assemble_runtime_tasks_includes_existing_ma_monitor_task():
    orchestrator = BridgeOrchestrator()
    update_checker_started = asyncio.Event()

    class FakeClient:
        async def run(self) -> None:
            await asyncio.sleep(3600)

    async def fake_update_checker(_version: str) -> None:
        update_checker_started.set()
        await asyncio.sleep(3600)

    async def fake_monitor() -> None:
        await asyncio.sleep(3600)

    monitor_task = asyncio.create_task(fake_monitor())
    tasks = orchestrator.assemble_runtime_tasks(
        [FakeClient()],
        ma_monitor_task=monitor_task,
        demo_mode=False,
        version="2.32.12",
        run_update_checker_fn=fake_update_checker,
    )

    await asyncio.wait_for(update_checker_started.wait(), timeout=0.2)
    assert monitor_task in tasks
    progress = state.get_startup_progress()
    assert progress["details"]["ma_monitor_enabled"] is True

    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_run_runtime_delegates_to_gather_with_assembled_tasks():
    orchestrator = BridgeOrchestrator()
    gathered: list[asyncio.Task[object]] = []
    update_checker_started = asyncio.Event()

    class FakeClient:
        async def run(self) -> None:
            await asyncio.sleep(3600)

    async def fake_update_checker(_version: str) -> None:
        update_checker_started.set()
        await asyncio.sleep(3600)

    async def fake_gather(*tasks):
        gathered.extend(tasks)
        await asyncio.sleep(0)
        for task in tasks:
            task.cancel()
        return await asyncio.gather(*tasks, return_exceptions=True)

    await orchestrator.run_runtime(
        [FakeClient()],
        ma_monitor_task=None,
        demo_mode=False,
        version="2.32.12",
        run_update_checker_fn=fake_update_checker,
        gather_fn=fake_gather,
    )

    await asyncio.wait_for(update_checker_started.wait(), timeout=0.2)
    assert len(gathered) == 2
    progress = state.get_startup_progress()
    assert progress["status"] == "ready"
