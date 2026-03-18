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
