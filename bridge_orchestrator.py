"""Bridge-wide startup sequencing helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

import state as _state
from config import ensure_bridge_name, load_config
from services.sendspin_compat import format_dependency_versions, get_runtime_dependency_versions

logger = logging.getLogger(__name__)


@dataclass
class RuntimeBootstrap:
    config: dict[str, Any]
    demo_mode: bool
    server_host: str
    server_port: int
    effective_bridge: str
    pulse_latency_msec: int
    log_level: str


@dataclass
class MaBootstrap:
    ma_api_url: str
    ma_api_token: str
    ma_monitor_task: asyncio.Task[None] | None


class BridgeOrchestrator:
    """Own bridge-wide runtime bootstrap without changing device behavior yet."""

    def __init__(self, startup_steps: int = 6):
        self.startup_steps = startup_steps

    async def initialize_runtime(self) -> RuntimeBootstrap:
        """Load bridge config and apply process-wide runtime settings."""
        demo_mode = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")
        _state.set_runtime_mode_info(
            {
                "mode": "demo" if demo_mode else "production",
                "is_mocked": bool(demo_mode),
                "simulator_active": bool(demo_mode),
            }
        )
        _state.reset_startup_progress(self.startup_steps, message="Startup initiated")
        _state.update_startup_progress(
            "config",
            "Loading configuration",
            current_step=1,
            details={"demo_mode": demo_mode},
        )
        if demo_mode:
            from demo import install

            install()

        config = load_config()
        server_host = config.get("SENDSPIN_SERVER", "auto")
        server_port = int(config.get("SENDSPIN_PORT") or 9000)
        effective_bridge = ensure_bridge_name(config)

        tz = os.getenv("TZ", config.get("TZ", "UTC"))
        os.environ["TZ"] = tz
        time.tzset()
        logger.info("Timezone: %s", tz)

        pulse_latency_msec = int(config.get("PULSE_LATENCY_MSEC") or 200)
        os.environ["PULSE_LATENCY_MSEC"] = str(pulse_latency_msec)
        logger.info("PULSE_LATENCY_MSEC: %s ms", pulse_latency_msec)

        log_level = config.get("LOG_LEVEL", "INFO").upper()
        if log_level not in ("INFO", "DEBUG"):
            log_level = "INFO"
        logging.getLogger().setLevel(getattr(logging, log_level))
        os.environ["LOG_LEVEL"] = log_level
        logger.info("Log level: %s", log_level)
        logger.info("Runtime deps: %s", format_dependency_versions(get_runtime_dependency_versions()))

        return RuntimeBootstrap(
            config=config,
            demo_mode=demo_mode,
            server_host=server_host,
            server_port=server_port,
            effective_bridge=effective_bridge,
            pulse_latency_msec=pulse_latency_msec,
            log_level=log_level,
        )

    async def configure_executor(self, device_count: int, *, web_thread_name: str = "") -> int:
        """Register the main loop and size its default executor for bridge load."""
        pool_size = min(64, max(8, device_count * 2 + 4))
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=pool_size))
        logger.debug("ThreadPoolExecutor: max_workers=%s", pool_size)
        _state.set_main_loop(asyncio.get_running_loop())
        _state.update_startup_progress(
            "web",
            "Web interface and event loop ready",
            current_step=4,
            details={"web_thread": web_thread_name} if web_thread_name else {},
        )
        return pool_size

    def start_web_server(
        self,
        clients: list[Any],
        *,
        web_main: Callable[[], None] | None = None,
        thread_name: str = "WebServer",
    ) -> threading.Thread:
        """Publish clients to shared state and start the web server thread."""
        web_main_fn = web_main
        if web_main_fn is None:
            from web_interface import main as imported_web_main

            web_main_fn = imported_web_main

        def _run_web_server() -> None:
            _state.set_clients(clients)
            web_main_fn()

        web_thread = threading.Thread(target=_run_web_server, daemon=True, name=thread_name)
        web_thread.start()
        logger.info("Web interface starting in background...")
        return web_thread

    async def graceful_shutdown(
        self,
        *,
        clients: list[Any] | None = None,
        mute_sink: Callable[[str, bool], Awaitable[bool]] | None = None,
    ) -> None:
        """Mute active sinks and stop all clients in a controlled order."""
        logger.info("Received shutdown signal — muting sinks before exit...")
        mute_sink_fn = mute_sink
        if mute_sink_fn is None:
            from services.pulse import aset_sink_mute as imported_mute_sink

            mute_sink_fn = imported_mute_sink

        shutdown_clients = list(clients) if clients is not None else _state.get_clients_snapshot()
        muted: list[str] = []
        for client in shutdown_clients:
            sink = getattr(client, "bluetooth_sink_name", None)
            if sink and await mute_sink_fn(sink, True):
                muted.append(sink)
        if muted:
            logger.info("Muted %d sink(s): %s", len(muted), ", ".join(muted))

        for client in shutdown_clients:
            client.running = False
            await client.stop_sendspin()

    def install_signal_handlers(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        shutdown_factory: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        """Register SIGTERM/SIGINT handlers that schedule graceful shutdown."""
        shutdown_factory_fn = shutdown_factory
        if shutdown_factory_fn is None:
            shutdown_factory_fn = self.graceful_shutdown

        def _signal_handler() -> None:
            loop.create_task(shutdown_factory_fn())

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _signal_handler)

    async def initialize_ma_integration(
        self,
        config: dict[str, Any],
        clients: list[Any],
        *,
        server_host: str,
    ) -> MaBootstrap:
        """Resolve MA credentials, discover groups, and start the optional monitor."""
        ma_api_url = config.get("MA_API_URL", "").strip()
        ma_api_token = config.get("MA_API_TOKEN", "").strip()

        supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        if supervisor_token:
            if not ma_api_url:
                if server_host and server_host.lower() not in ("auto", "discover", ""):
                    ma_api_url = f"http://{server_host}:8095"
                else:
                    ma_api_url = "http://localhost:8095"
                logger.info("MA API URL auto-detected (addon mode): %s", ma_api_url)
            if not ma_api_token:
                logger.warning(
                    "MA API: running in HA addon mode but no 'ma_api_token' configured. "
                    "Create a long-lived token in MA → Settings → API Tokens and set ma_api_token in bridge config."
                )

        if ma_api_url and ma_api_token:
            _state.set_ma_api_credentials(ma_api_url, ma_api_token)
            try:
                from services.ma_client import discover_ma_groups

                player_info = [{"player_id": client.player_id, "player_name": client.player_name} for client in clients]
                name_map, all_groups = await discover_ma_groups(ma_api_url, ma_api_token, player_info)
                _state.set_ma_groups(name_map, all_groups)
                if name_map:
                    _state.set_ma_connected(True)
            except Exception as ma_exc:
                logger.warning("MA API group discovery error: %s", ma_exc)

        ma_monitor_task: asyncio.Task[None] | None = None
        if ma_api_url and ma_api_token and config.get("MA_WEBSOCKET_MONITOR", True):
            from services.ma_monitor import start_monitor

            monitor = start_monitor(ma_api_url, ma_api_token)
            ma_monitor_task = asyncio.create_task(monitor.run())

        _state.update_startup_progress(
            "integrations",
            "Music Assistant integrations initialized",
            current_step=5,
            details={
                "ma_configured": bool(ma_api_url and ma_api_token),
                "ma_monitor_enabled": bool(ma_monitor_task),
            },
        )
        return MaBootstrap(
            ma_api_url=ma_api_url,
            ma_api_token=ma_api_token,
            ma_monitor_task=ma_monitor_task,
        )
