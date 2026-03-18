"""Focused tests for the first BridgeOrchestrator slice."""

from __future__ import annotations

import asyncio
import json

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
