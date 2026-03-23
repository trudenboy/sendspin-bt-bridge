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


class RecordingLifecycleState:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def begin_startup(self, *, demo_mode: bool) -> None:
        self.calls.append(("begin_startup", {"demo_mode": demo_mode}))

    def publish_main_loop(self, loop, *, web_thread_name: str = "") -> None:
        self.calls.append(("publish_main_loop", {"loop": loop, "web_thread_name": web_thread_name}))

    def publish_clients(self, clients) -> None:
        self.calls.append(("publish_clients", {"clients": clients}))

    def publish_runtime_prepared(self, **kwargs) -> None:
        self.calls.append(("publish_runtime_prepared", kwargs))

    def publish_device_registry(self, **kwargs) -> None:
        self.calls.append(("publish_device_registry", kwargs))

    def publish_ma_integration(self, **kwargs) -> None:
        self.calls.append(("publish_ma_integration", kwargs))

    def publish_startup_failure(self, message: str, *, phase: str, details=None) -> None:
        self.calls.append(("publish_startup_failure", {"message": message, "phase": phase, "details": details}))

    def complete_startup(self, **kwargs) -> None:
        self.calls.append(("complete_startup", kwargs))

    def publish_shutdown_started(self, *, active_clients: int) -> None:
        self.calls.append(("publish_shutdown_started", {"active_clients": active_clients}))

    def publish_shutdown_complete(self, *, stopped_clients: int) -> None:
        self.calls.append(("publish_shutdown_complete", {"stopped_clients": stopped_clients}))


class RecordingMaIntegrationService:
    def __init__(self, resolved):
        self.resolved = resolved
        self.calls: list[tuple[dict[str, object], list[object], str]] = []

    async def initialize(self, config, clients, *, server_host: str):
        self.calls.append((config, clients, server_host))
        return self.resolved


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "SENDSPIN_SERVER": "music-assistant.local",
                "SENDSPIN_PORT": 9000,
                "BLUETOOTH_DEVICES": [{"player_name": "Kitchen", "mac": "AA:BB:CC:DD:EE:01"}],
                "PREFER_SBC_CODEC": True,
                "BT_CHECK_INTERVAL": 15,
                "BT_MAX_RECONNECT_FAILS": 5,
                "BT_CHURN_THRESHOLD": 3,
                "BT_CHURN_WINDOW": 120,
                "PULSE_LATENCY_MSEC": 250,
                "LOG_LEVEL": "DEBUG",
            }
        )
    )
    state.reset_startup_progress()
    state.set_runtime_mode_info(None)
    state.set_clients([])
    state.set_disabled_devices([])
    state.set_ma_api_credentials("", "")
    state.set_ma_groups({}, [])
    state.set_ma_connected(False)


@pytest.mark.asyncio
async def test_initialize_runtime_loads_config_and_updates_progress():
    orchestrator = BridgeOrchestrator()

    bootstrap = await orchestrator.initialize_runtime()

    assert bootstrap.delivery_channel == "stable"
    assert bootstrap.server_host == "music-assistant.local"
    assert bootstrap.server_port == 9000
    assert bootstrap.web_port == 8080
    assert bootstrap.base_listen_port == 8928
    assert bootstrap.pulse_latency_msec == 250
    assert bootstrap.log_level == "DEBUG"
    assert bootstrap.prefer_sbc is True
    assert bootstrap.bt_check_interval == 15
    assert bootstrap.bt_max_reconnect_fails == 5
    assert bootstrap.bt_churn_threshold == 3
    assert bootstrap.bt_churn_window == 120.0
    assert bootstrap.device_configs[0]["player_name"] == "Kitchen"
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
async def test_initialize_runtime_derives_channel_specific_ports_for_ha_addon(monkeypatch):
    orchestrator = BridgeOrchestrator()
    monkeypatch.setenv("SUPERVISOR_TOKEN", "demo-supervisor-token")
    monkeypatch.setenv("HOSTNAME", "85b1ecde-sendspin-bt-bridge-rc")

    bootstrap = await orchestrator.initialize_runtime()

    assert bootstrap.delivery_channel == "rc"
    assert bootstrap.web_port == 8081
    assert bootstrap.base_listen_port == 9028


@pytest.mark.asyncio
async def test_initialize_runtime_delegates_startup_publication_to_lifecycle_state():
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state)

    await orchestrator.initialize_runtime()

    assert lifecycle_state.calls[0] == ("begin_startup", {"demo_mode": False})


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


@pytest.mark.asyncio
async def test_configure_executor_delegates_main_loop_publication():
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state)
    loop = asyncio.get_running_loop()
    original_executor = getattr(loop, "_default_executor", None)

    try:
        await orchestrator.configure_executor(2, web_thread_name="DelegatedWeb")
    finally:
        current_executor = getattr(loop, "_default_executor", None)
        if current_executor is not None and current_executor is not original_executor:
            current_executor.shutdown(wait=False, cancel_futures=True)
        loop._default_executor = original_executor

    assert lifecycle_state.calls[-1][0] == "publish_main_loop"
    assert lifecycle_state.calls[-1][1]["loop"] is loop
    assert lifecycle_state.calls[-1][1]["web_thread_name"] == "DelegatedWeb"


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


def test_start_web_server_delegates_client_publication():
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state)
    clients = [SimpleNamespace(player_name="Kitchen")]

    thread = orchestrator.start_web_server(clients, web_main=lambda: None, thread_name="DelegatedWebServer")
    thread.join(timeout=1)

    assert lifecycle_state.calls == [("publish_clients", {"clients": clients})]


@pytest.mark.asyncio
async def test_graceful_shutdown_mutes_sinks_and_stops_clients():
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state)
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
    assert lifecycle_state.calls[-3:] == [
        ("publish_shutdown_started", {"active_clients": 2}),
        ("publish_clients", {"clients": []}),
        ("publish_shutdown_complete", {"stopped_clients": 2}),
    ]
    assert state.get_clients_snapshot() == []


@pytest.mark.asyncio
async def test_orchestrator_lifecycle_contract_sequence_updates_shared_state(monkeypatch):
    published = []
    orchestrator = BridgeOrchestrator()

    def _capture_internal_event(*, event_type, category, subject_id, payload=None):
        published.append(
            {
                "event_type": event_type,
                "category": category,
                "subject_id": subject_id,
                "payload": payload,
            }
        )
        return SimpleNamespace(at="2026-03-20T00:00:00+00:00")

    monkeypatch.setattr(state, "publish_internal_event", _capture_internal_event)

    class FakeClient:
        def __init__(self, name: str, sink: str | None, player_id: str):
            self.player_name = name
            self.player_id = player_id
            self.bluetooth_sink_name = sink
            self.status: dict[str, object] = {}
            self.running = True

        async def stop_sendspin(self) -> None:
            self.running = False

    async def _mute_sink(_sink: str, _muted_flag: bool) -> bool:
        return True

    bootstrap = await orchestrator.initialize_runtime()
    loop = asyncio.get_running_loop()
    original_executor = getattr(loop, "_default_executor", None)
    clients = [FakeClient("Kitchen", "sink.one", "sendspin-kitchen")]

    try:
        await orchestrator.configure_executor(len(bootstrap.device_configs), web_thread_name="LifecycleWeb")
        web_thread = orchestrator.start_web_server(clients, web_main=lambda: None, thread_name="LifecycleWeb")
        web_thread.join(timeout=1)

        orchestrator.lifecycle_state.publish_device_registry(
            configured_devices=len(bootstrap.device_configs),
            active_clients=clients,
            disabled_devices=[],
        )
        orchestrator.lifecycle_state.publish_ma_integration(
            ma_api_url="http://ma.local:8095",
            ma_api_token="token",
            groups_loaded=True,
            name_map={"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen Group"}},
            all_groups=[{"id": "syncgroup_1", "name": "Kitchen Group", "members": []}],
            monitor_enabled=True,
        )
        orchestrator.lifecycle_state.complete_startup(
            active_clients=clients,
            demo_mode=False,
            monitor_enabled=True,
        )

        await orchestrator.graceful_shutdown(clients=clients, mute_sink=_mute_sink)
    finally:
        current_executor = getattr(loop, "_default_executor", None)
        if current_executor is not None and current_executor is not original_executor:
            current_executor.shutdown(wait=False, cancel_futures=True)
        loop._default_executor = original_executor

    progress = state.get_startup_progress()
    runtime_info = state.get_runtime_mode_info()
    assert runtime_info["mode"] == "production"
    assert progress["phase"] == "shutdown"
    assert progress["status"] == "stopped"
    assert progress["details"]["stopped_clients"] == 1
    assert state.get_main_loop() is None
    assert state.get_clients_snapshot() == []
    assert [event["event_type"] for event in published] == [
        "bridge.startup.started",
        "bridge.startup.completed",
        "bridge.shutdown.started",
        "bridge.shutdown.completed",
    ]
    assert published[0]["payload"] == {"demo_mode": False}
    assert published[1]["payload"] == {
        "active_clients": 1,
        "ma_monitor_enabled": True,
        "demo_mode": False,
    }
    assert published[2]["payload"] == {"active_clients": 1}
    assert published[3]["payload"] == {"stopped_clients": 1}


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
async def test_initialize_ma_integration_delegates_state_publication(monkeypatch):
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state)

    async def fake_discover(_url, _token, _player_info):
        return (
            {"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen Group"}},
            [{"id": "syncgroup_1", "name": "Kitchen Group", "members": []}],
        )

    monkeypatch.setattr("services.ma_client.discover_ma_groups", fake_discover)

    await orchestrator.initialize_ma_integration(
        {
            "MA_API_URL": "http://ma.local:8095",
            "MA_API_TOKEN": "token",
            "MA_WEBSOCKET_MONITOR": False,
        },
        [SimpleNamespace(player_id="sendspin-kitchen", player_name="Kitchen")],
        server_host="music-assistant.local",
    )

    assert lifecycle_state.calls[-1][0] == "publish_ma_integration"
    assert lifecycle_state.calls[-1][1]["ma_api_url"] == "http://ma.local:8095"
    assert lifecycle_state.calls[-1][1]["ma_api_token"] == "token"
    assert lifecycle_state.calls[-1][1]["groups_loaded"] is True
    assert lifecycle_state.calls[-1][1]["monitor_enabled"] is False


@pytest.mark.asyncio
async def test_initialize_ma_integration_delegates_to_ma_service():
    resolved = SimpleNamespace(
        ma_api_url="http://ma.local:8095",
        ma_api_token="token",
        name_map={"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen Group"}},
        all_groups=[{"id": "syncgroup_1", "name": "Kitchen Group", "members": []}],
        groups_loaded=True,
        ma_monitor_task=None,
    )
    ma_service = RecordingMaIntegrationService(resolved)
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state, ma_integration_service=ma_service)
    clients = [SimpleNamespace(player_id="sendspin-kitchen", player_name="Kitchen")]
    config_data = {
        "MA_API_URL": "http://ma.local:8095",
        "MA_API_TOKEN": "token",
        "MA_WEBSOCKET_MONITOR": False,
    }

    bootstrap = await orchestrator.initialize_ma_integration(config_data, clients, server_host="ma-host.local")

    assert ma_service.calls == [(config_data, clients, "ma-host.local")]
    assert bootstrap.ma_api_url == "http://ma.local:8095"
    assert bootstrap.ma_api_token == "token"
    assert lifecycle_state.calls[-1][0] == "publish_ma_integration"
    assert lifecycle_state.calls[-1][1]["name_map"] == resolved.name_map


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
async def test_assemble_runtime_tasks_delegates_startup_completion():
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state)

    class FakeClient:
        async def run(self) -> None:
            await asyncio.sleep(3600)

    async def fake_update_checker(_version: str) -> None:
        await asyncio.sleep(3600)

    clients = [FakeClient()]
    tasks = orchestrator.assemble_runtime_tasks(
        clients,
        ma_monitor_task=None,
        demo_mode=False,
        version="2.32.12",
        run_update_checker_fn=fake_update_checker,
    )

    assert lifecycle_state.calls[-1][0] == "complete_startup"
    assert lifecycle_state.calls[-1][1]["active_clients"] == clients
    assert lifecycle_state.calls[-1][1]["demo_mode"] is False
    assert lifecycle_state.calls[-1][1]["monitor_enabled"] is False

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


@pytest.mark.asyncio
async def test_run_bridge_lifecycle_sequences_remaining_flow(monkeypatch):
    orchestrator = BridgeOrchestrator()
    call_order: list[str] = []
    observed: dict[str, object] = {}
    loop = asyncio.get_running_loop()
    bootstrap = await orchestrator.initialize_runtime()

    class FakeThread:
        name = "LifecycleWebThread"

    fake_clients = [SimpleNamespace(player_name="Kitchen"), SimpleNamespace(player_name="Bedroom")]
    fake_monitor_task = asyncio.create_task(asyncio.sleep(3600))

    def fake_initialize_devices(*args, **kwargs):
        assert args[0] is bootstrap
        assert kwargs["base_listen_port"] == bootstrap.base_listen_port
        call_order.append("devices")
        return SimpleNamespace(clients=fake_clients)

    def fake_start_web_server(clients, *, web_main=None, thread_name="WebServer"):
        assert clients == fake_clients
        observed["web_main"] = web_main
        observed["thread_name"] = thread_name
        call_order.append("web")
        return FakeThread()

    def fake_install_signal_handlers(current_loop, *, shutdown_factory=None):
        assert current_loop is loop
        observed["shutdown_factory"] = shutdown_factory
        call_order.append("signals")

    async def fake_configure_executor(device_count, *, web_thread_name=""):
        assert device_count == 2
        assert web_thread_name == "LifecycleWebThread"
        call_order.append("executor")
        return 8

    async def fake_initialize_ma(config_data, clients, *, server_host):
        assert config_data is bootstrap.config
        assert clients == fake_clients
        assert server_host == bootstrap.server_host
        call_order.append("ma")
        return SimpleNamespace(ma_monitor_task=fake_monitor_task)

    async def fake_run_runtime(clients, *, ma_monitor_task, demo_mode, version, **_kwargs):
        assert clients == fake_clients
        assert ma_monitor_task is fake_monitor_task
        assert demo_mode is bootstrap.demo_mode
        assert version == "2.32.12"
        call_order.append("runtime")
        return "done"

    monkeypatch.setattr(orchestrator, "initialize_devices", fake_initialize_devices)
    monkeypatch.setattr(orchestrator, "start_web_server", fake_start_web_server)
    monkeypatch.setattr(orchestrator, "install_signal_handlers", fake_install_signal_handlers)
    monkeypatch.setattr(orchestrator, "configure_executor", fake_configure_executor)
    monkeypatch.setattr(orchestrator, "initialize_ma_integration", fake_initialize_ma)
    monkeypatch.setattr(orchestrator, "run_runtime", fake_run_runtime)

    result = await orchestrator.run_bridge_lifecycle(
        bootstrap,
        version="2.32.12",
        client_factory=object,
        bt_manager_factory=object,
    )

    assert result == "done"
    assert call_order == ["devices", "web", "executor", "signals", "ma", "runtime"]
    assert observed["web_main"] is None
    assert observed["thread_name"] == "WebServer"
    assert callable(observed["shutdown_factory"])

    fake_monitor_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fake_monitor_task


@pytest.mark.asyncio
async def test_run_bridge_lifecycle_marks_startup_failure_when_ma_init_fails(monkeypatch):
    orchestrator = BridgeOrchestrator()
    bootstrap = await orchestrator.initialize_runtime()

    fake_clients = [SimpleNamespace(player_name="Kitchen")]

    def fake_initialize_devices(*args, **kwargs):
        return SimpleNamespace(clients=fake_clients)

    def fake_start_web_server(clients, *, web_main=None, thread_name="WebServer"):
        return SimpleNamespace(name="LifecycleWebThread")

    async def fake_configure_executor(device_count, *, web_thread_name=""):
        return 8

    async def fake_initialize_ma(config_data, clients, *, server_host):
        raise RuntimeError("MA bootstrap failed")

    monkeypatch.setattr(orchestrator, "initialize_devices", fake_initialize_devices)
    monkeypatch.setattr(orchestrator, "start_web_server", fake_start_web_server)
    monkeypatch.setattr(orchestrator, "configure_executor", fake_configure_executor)
    monkeypatch.setattr(orchestrator, "install_signal_handlers", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(orchestrator, "initialize_ma_integration", fake_initialize_ma)

    with pytest.raises(RuntimeError, match="MA bootstrap failed"):
        await orchestrator.run_bridge_lifecycle(
            bootstrap,
            version="2.32.12",
            client_factory=object,
            bt_manager_factory=object,
        )

    progress = state.get_startup_progress()
    assert progress["status"] == "error"
    assert progress["details"]["startup_phase"] == "integrations"
    assert progress["details"]["error_type"] == "RuntimeError"
    assert "Startup failed during integrations" in progress["message"]


def test_initialize_devices_builds_clients_and_registers_disabled_devices():
    orchestrator = BridgeOrchestrator()
    persisted: list[tuple[str, bool]] = []

    class FakeClient:
        def __init__(
            self,
            player_name,
            server_host,
            server_port,
            _bt_manager,
            *,
            listen_port,
            static_delay_ms,
            listen_host,
            effective_bridge,
            preferred_format,
            keepalive_enabled,
            keepalive_interval,
        ):
            self.player_name = player_name
            self.server_host = server_host
            self.server_port = server_port
            self.listen_port = listen_port
            self.listen_host = listen_host
            self.static_delay_ms = static_delay_ms
            self._effective_bridge = effective_bridge
            self.preferred_format = preferred_format
            self.keepalive_enabled = keepalive_enabled
            self.keepalive_interval = keepalive_interval
            self.bt_manager = None
            self.bt_management_enabled = True
            self.bluetooth_sink_name = None
            self.status = {"volume": 100, "bluetooth_available": None}

        def _update_status(self, updates):
            self.status.update(updates)

        def set_bt_management_enabled(self, enabled):
            self.bt_management_enabled = enabled

    class FakeBtManager:
        def __init__(self, mac_address, **kwargs):
            self.mac_address = mac_address
            self.kwargs = kwargs
            self.on_sink_found = kwargs["on_sink_found"]
            self.management_enabled = True

        def check_bluetooth_available(self):
            return True

    bootstrap_result = orchestrator.initialize_devices(
        SimpleNamespace(
            device_configs=[
                {
                    "player_name": "Kitchen",
                    "mac": "AA:BB:CC:DD:EE:01",
                    "adapter": "hci0",
                    "keepalive_interval": 45,
                    "released": True,
                },
                {
                    "player_name": "Bedroom",
                    "mac": "AA:BB:CC:DD:EE:02",
                    "adapter": "hci1",
                    "enabled": False,
                },
            ],
            server_host="music-assistant.local",
            server_port=9000,
            effective_bridge="Bridge",
            prefer_sbc=True,
            bt_check_interval=15,
            bt_max_reconnect_fails=5,
            bt_churn_threshold=3,
            bt_churn_window=120.0,
            log_level="DEBUG",
            pulse_latency_msec=250,
        ),
        client_factory=FakeClient,
        bt_manager_factory=FakeBtManager,
        filter_devices_fn=lambda devices: devices,
        load_saved_volume_fn=lambda mac: 33 if mac.endswith("01") else None,
        persist_enabled_fn=lambda player_name, enabled: persisted.append((player_name, enabled)),
    )

    assert len(bootstrap_result.bt_devices) == 2
    assert len(bootstrap_result.clients) == 1
    client = bootstrap_result.clients[0]
    assert client.player_name == "Kitchen @ Bridge"
    assert client.status["volume"] == 33
    assert client.status["bluetooth_available"] is True
    assert client.keepalive_enabled is True
    assert client.keepalive_interval == 45
    assert client.bt_management_enabled is False
    assert state.get_disabled_devices() == [
        {
            "player_name": "Bedroom @ Bridge",
            "mac": "AA:BB:CC:DD:EE:02",
            "adapter": "hci1",
            "enabled": False,
        }
    ]
    progress = state.get_startup_progress()
    assert progress["phase"] == "devices"
    assert progress["details"]["configured_devices"] == 2
    assert progress["details"]["active_clients"] == 1
    assert progress["details"]["disabled_devices"] == 1
    assert persisted == []

    client.bt_manager.on_sink_found("bluez_output.demo", 44)
    assert client.bluetooth_sink_name == "bluez_output.demo"
    assert client.status["volume"] == 44


def test_initialize_devices_enables_default_keepalive_for_fast_handoff():
    orchestrator = BridgeOrchestrator()

    class FakeClient:
        def __init__(
            self,
            player_name,
            server_host,
            server_port,
            _bt_manager,
            *,
            listen_port,
            static_delay_ms,
            listen_host,
            effective_bridge,
            preferred_format,
            keepalive_enabled,
            keepalive_interval,
        ):
            self.player_name = player_name
            self.server_host = server_host
            self.server_port = server_port
            self.listen_port = listen_port
            self.listen_host = listen_host
            self.static_delay_ms = static_delay_ms
            self._effective_bridge = effective_bridge
            self.preferred_format = preferred_format
            self.keepalive_enabled = keepalive_enabled
            self.keepalive_interval = keepalive_interval
            self.bt_manager = None
            self.bt_management_enabled = True
            self.bluetooth_sink_name = None
            self.status = {"volume": 100, "bluetooth_available": None}

        def _update_status(self, updates):
            self.status.update(updates)

        def set_bt_management_enabled(self, enabled):
            self.bt_management_enabled = enabled

    class FakeBtManager:
        def __init__(self, mac_address, **kwargs):
            self.mac_address = mac_address
            self.on_sink_found = kwargs["on_sink_found"]

        def check_bluetooth_available(self):
            return True

    bootstrap_result = orchestrator.initialize_devices(
        SimpleNamespace(
            device_configs=[
                {
                    "player_name": "Kitchen",
                    "mac": "AA:BB:CC:DD:EE:01",
                    "handoff_mode": "fast_handoff",
                }
            ],
            server_host="music-assistant.local",
            server_port=9000,
            effective_bridge="Bridge",
            prefer_sbc=True,
            bt_check_interval=15,
            bt_max_reconnect_fails=5,
            bt_churn_threshold=3,
            bt_churn_window=120.0,
            log_level="DEBUG",
            pulse_latency_msec=250,
        ),
        client_factory=FakeClient,
        bt_manager_factory=FakeBtManager,
        filter_devices_fn=lambda devices: devices,
        load_saved_volume_fn=lambda _mac: None,
        persist_enabled_fn=lambda _player_name, _enabled: None,
    )

    client = bootstrap_result.clients[0]
    assert client.keepalive_enabled is True
    assert client.keepalive_interval == 45


def test_initialize_devices_delegates_runtime_and_registry_publication():
    lifecycle_state = RecordingLifecycleState()
    orchestrator = BridgeOrchestrator(lifecycle_state=lifecycle_state)

    class FakeClient:
        def __init__(self, player_name, *_args, listen_port, **_kwargs):
            self.player_name = player_name
            self.listen_port = listen_port
            self.bt_manager = None
            self.bt_management_enabled = True
            self.bluetooth_sink_name = None
            self.status = {"volume": 100, "bluetooth_available": None}

        def _update_status(self, updates):
            self.status.update(updates)

        def set_bt_management_enabled(self, enabled):
            self.bt_management_enabled = enabled

    class FakeBtManager:
        def __init__(self, _mac_address, **kwargs):
            self.on_sink_found = kwargs["on_sink_found"]

        def check_bluetooth_available(self):
            return True

    bootstrap_result = orchestrator.initialize_devices(
        SimpleNamespace(
            device_configs=[
                {"player_name": "Kitchen", "mac": "AA:BB:CC:DD:EE:01"},
                {"player_name": "Bedroom", "mac": "AA:BB:CC:DD:EE:02", "enabled": False},
            ],
            server_host="music-assistant.local",
            server_port=9000,
            effective_bridge="Bridge",
            prefer_sbc=False,
            bt_check_interval=15,
            bt_max_reconnect_fails=5,
            bt_churn_threshold=3,
            bt_churn_window=120.0,
            log_level="INFO",
            pulse_latency_msec=250,
        ),
        client_factory=FakeClient,
        bt_manager_factory=FakeBtManager,
        filter_devices_fn=lambda devices: devices,
    )

    runtime_call = lifecycle_state.calls[0]
    registry_call = lifecycle_state.calls[1]
    assert runtime_call == (
        "publish_runtime_prepared",
        {
            "configured_devices": 2,
            "log_level": "INFO",
            "pulse_latency_msec": 250,
        },
    )
    assert registry_call[0] == "publish_device_registry"
    assert registry_call[1]["configured_devices"] == 2
    assert registry_call[1]["active_clients"] == bootstrap_result.clients
    assert registry_call[1]["disabled_devices"] == [
        {
            "player_name": "Bedroom @ Bridge",
            "mac": "AA:BB:CC:DD:EE:02",
            "adapter": "",
            "enabled": False,
        }
    ]


def test_initialize_devices_warns_on_port_collisions_and_persists_enabled(caplog):
    orchestrator = BridgeOrchestrator()
    persisted: list[tuple[str, bool]] = []

    class FakeClient:
        def __init__(self, player_name, *_args, listen_port, **_kwargs):
            self.player_name = player_name
            self.listen_port = listen_port
            self.bt_manager = None
            self.bt_management_enabled = True
            self.bluetooth_sink_name = None
            self.status = {}

        def _update_status(self, updates):
            self.status.update(updates)

        def set_bt_management_enabled(self, enabled):
            self.bt_management_enabled = enabled

    class FakeBtManager:
        def __init__(self, mac_address, **kwargs):
            self.mac_address = mac_address
            self.kwargs = kwargs

        def check_bluetooth_available(self):
            return False

    with caplog.at_level("WARNING"):
        bootstrap = orchestrator.initialize_devices(
            SimpleNamespace(
                device_configs=[
                    {"player_name": "One", "mac": "AA:BB:CC:DD:EE:01", "listen_port": 8928},
                    {"player_name": "Two", "mac": "AA:BB:CC:DD:EE:02", "listen_port": 8928},
                ],
                server_host="music-assistant.local",
                server_port=9000,
                effective_bridge="",
                prefer_sbc=False,
                bt_check_interval=10,
                bt_max_reconnect_fails=0,
                bt_churn_threshold=0,
                bt_churn_window=300.0,
                log_level="INFO",
                pulse_latency_msec=200,
            ),
            client_factory=FakeClient,
            bt_manager_factory=FakeBtManager,
            filter_devices_fn=lambda devices: devices,
            persist_enabled_fn=lambda player_name, enabled: persisted.append((player_name, enabled)),
        )

    assert len(bootstrap.clients) == 2
    assert persisted == [("One", True), ("Two", True)]
    assert "Using default listen_port 8928 with multiple devices" in caplog.text
    assert "listen_port 8928 already used by another client" in caplog.text
    assert "BT adapter 'default' not available for One" in caplog.text
    assert "BT adapter 'default' not available for Two" in caplog.text
