"""Bridge-wide startup sequencing helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
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
from services.update_checker import run_update_checker

logger = logging.getLogger(__name__)


@dataclass
class RuntimeBootstrap:
    config: dict[str, Any]
    device_configs: list[dict[str, Any]]
    demo_mode: bool
    server_host: str
    server_port: int
    effective_bridge: str
    prefer_sbc: bool
    bt_check_interval: int
    bt_max_reconnect_fails: int
    bt_churn_threshold: int
    bt_churn_window: float
    pulse_latency_msec: int
    log_level: str


@dataclass
class MaBootstrap:
    ma_api_url: str
    ma_api_token: str
    ma_monitor_task: asyncio.Task[None] | None


@dataclass
class DeviceBootstrap:
    bt_devices: list[dict[str, Any]]
    clients: list[Any]
    disabled_devices: list[dict[str, Any]]


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
        prefer_sbc = bool(config.get("PREFER_SBC_CODEC", False))
        if prefer_sbc:
            logger.info("PREFER_SBC_CODEC: enabled — will request SBC codec after BT connect")
        bt_check_interval = int(config.get("BT_CHECK_INTERVAL", 10))
        bt_max_reconnect_fails = int(config.get("BT_MAX_RECONNECT_FAILS", 0))
        bt_churn_threshold = int(config.get("BT_CHURN_THRESHOLD", 0))
        bt_churn_window = float(config.get("BT_CHURN_WINDOW", 300.0))
        if bt_churn_threshold > 0:
            logger.info("BT churn isolation: enabled (threshold=%d in %.0fs)", bt_churn_threshold, bt_churn_window)

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
            device_configs=list(config.get("BLUETOOTH_DEVICES", [])),
            demo_mode=demo_mode,
            server_host=server_host,
            server_port=server_port,
            effective_bridge=effective_bridge,
            prefer_sbc=prefer_sbc,
            bt_check_interval=bt_check_interval,
            bt_max_reconnect_fails=bt_max_reconnect_fails,
            bt_churn_threshold=bt_churn_threshold,
            bt_churn_window=bt_churn_window,
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

    def initialize_devices(
        self,
        bootstrap: RuntimeBootstrap,
        *,
        client_factory: Callable[..., Any],
        bt_manager_factory: Callable[..., Any],
        filter_devices_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
        load_saved_volume_fn: Callable[[str], int | None] | None = None,
        persist_enabled_fn: Callable[[str, bool], None] | None = None,
        base_listen_port: int = 8928,
        default_player_name: str | None = None,
    ) -> DeviceBootstrap:
        """Build device clients, register disabled devices, and publish startup progress."""
        bt_devices = (
            filter_devices_fn(bootstrap.device_configs)
            if filter_devices_fn is not None
            else list(bootstrap.device_configs)
        )
        _state.update_startup_progress(
            "runtime",
            "Runtime configuration prepared",
            current_step=2,
            details={
                "configured_devices": len(bt_devices),
                "log_level": bootstrap.log_level,
                "pulse_latency_msec": bootstrap.pulse_latency_msec,
            },
        )

        logger.info("Starting %s player instance(s)", len(bt_devices))
        if not bt_devices:
            logger.warning("No Bluetooth devices configured — bridge will run without players")
        if bootstrap.server_host and bootstrap.server_host.lower() not in ["auto", "discover", ""]:
            logger.info("Server: %s:%s", bootstrap.server_host, bootstrap.server_port)
        else:
            logger.info("Server: Auto-discovery enabled (mDNS)")

        resolved_default_name = default_player_name or os.getenv("SENDSPIN_NAME") or f"Sendspin-{socket.gethostname()}"
        clients: list[Any] = []
        disabled_list: list[dict[str, Any]] = []

        for index, device in enumerate(bt_devices):
            mac = str(device.get("mac") or "")
            adapter = str(device.get("adapter") or "")
            player_name = str(device.get("player_name") or resolved_default_name)
            if bootstrap.effective_bridge:
                player_name = f"{player_name} @ {bootstrap.effective_bridge}"

            if not device.get("enabled", True):
                disabled_list.append(
                    {
                        "player_name": player_name,
                        "mac": mac,
                        "adapter": adapter,
                        "enabled": False,
                    }
                )
                logger.info("  Player '%s': globally disabled — skipping", player_name)
                continue

            listen_port = int(device.get("listen_port") or base_listen_port + index)
            listen_host = device.get("listen_host")
            static_delay_ms = device.get("static_delay_ms")
            if static_delay_ms is not None:
                static_delay_ms = float(static_delay_ms)
            preferred_format = device.get("preferred_format", "flac:44100:16:2")
            keepalive_interval = int(device.get("keepalive_interval") or 0)
            keepalive_enabled = keepalive_interval > 0
            keepalive_interval = max(30, keepalive_interval) if keepalive_enabled else 30

            client = client_factory(
                player_name,
                bootstrap.server_host,
                bootstrap.server_port,
                None,
                listen_port=listen_port,
                static_delay_ms=static_delay_ms,
                listen_host=listen_host,
                effective_bridge=bootstrap.effective_bridge,
                preferred_format=preferred_format or None,
                keepalive_enabled=keepalive_enabled,
                keepalive_interval=keepalive_interval,
            )
            if mac:

                def _on_sink_found(sink_name: str, restored_volume: int | None = None, _client=client) -> None:
                    _client.bluetooth_sink_name = sink_name
                    logger.info("Stored Bluetooth sink for volume sync: %s", sink_name)
                    if restored_volume is not None:
                        _client._update_status({"volume": restored_volume})

                bt_mgr = bt_manager_factory(
                    mac,
                    adapter=adapter,
                    device_name=player_name,
                    client=client,
                    prefer_sbc=bootstrap.prefer_sbc,
                    check_interval=bootstrap.bt_check_interval,
                    max_reconnect_fails=bootstrap.bt_max_reconnect_fails,
                    on_sink_found=_on_sink_found,
                    churn_threshold=bootstrap.bt_churn_threshold,
                    churn_window=bootstrap.bt_churn_window,
                )
                bt_available = bool(bt_mgr.check_bluetooth_available())
                if not bt_available:
                    logger.warning("BT adapter '%s' not available for %s", adapter or "default", player_name)
                client.bt_manager = bt_mgr
                client._update_status({"bluetooth_available": bt_available})
                if load_saved_volume_fn is not None:
                    saved_volume = load_saved_volume_fn(mac)
                    if saved_volume is not None and 0 <= saved_volume <= 100:
                        client._update_status({"volume": saved_volume})

            clients.append(client)
            if device.get("released", False):
                client.set_bt_management_enabled(False)
                logger.info("  Player '%s': restored released state", player_name)
            logger.info("  Player: '%s', BT: %s, Adapter: %s", player_name, mac or "none", adapter or "default")

        logger.info("Client instance(s) registered")
        _state.set_disabled_devices(disabled_list)
        _state.update_startup_progress(
            "devices",
            "Device registry prepared",
            current_step=3,
            details={
                "configured_devices": len(bt_devices),
                "active_clients": len(clients),
                "disabled_devices": len(disabled_list),
            },
        )

        if persist_enabled_fn is not None:
            try:
                for client in clients:
                    if getattr(client, "bt_management_enabled", True):
                        persist_enabled_fn(str(client.player_name), True)
            except Exception as exc:
                logger.debug("Could not sync enabled state to options.json: %s", exc)

        used_ports: set[int] = set()
        for client in clients:
            listen_port = int(client.listen_port)
            if listen_port in used_ports:
                logger.warning(
                    "[%s] listen_port %s already used by another client — sendspin daemon will fail to bind. Set unique 'listen_port' per device.",
                    client.player_name,
                    listen_port,
                )
            elif listen_port == base_listen_port and len(clients) > 1:
                logger.warning(
                    "[%s] Using default listen_port %s with multiple devices — set explicit ports.",
                    client.player_name,
                    base_listen_port,
                )
            used_ports.add(listen_port)

        return DeviceBootstrap(bt_devices=bt_devices, clients=clients, disabled_devices=disabled_list)

    def assemble_runtime_tasks(
        self,
        clients: list[Any],
        *,
        ma_monitor_task: asyncio.Task[None] | None,
        demo_mode: bool,
        version: str,
        run_simulator_fn: Callable[[list[Any]], Coroutine[Any, Any, None]] | None = None,
        run_update_checker_fn: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> list[asyncio.Task[Any]]:
        """Create the long-running runtime tasks and mark startup complete."""
        tasks: list[asyncio.Task[Any]] = [asyncio.create_task(client.run()) for client in clients]
        if ma_monitor_task is not None:
            tasks.append(ma_monitor_task)

        simulator_fn = run_simulator_fn
        if demo_mode:
            if simulator_fn is None:
                from demo.simulator import run_simulator as imported_run_simulator

                simulator_fn = imported_run_simulator
            tasks.append(asyncio.create_task(simulator_fn(clients)))

        update_checker_fn = run_update_checker_fn or run_update_checker
        tasks.append(asyncio.create_task(update_checker_fn(version)))
        _state.complete_startup_progress(
            "Startup complete",
            details={
                "active_clients": len(clients),
                "ma_monitor_enabled": bool(ma_monitor_task),
                "demo_mode": demo_mode,
            },
        )
        return tasks

    async def run_runtime(
        self,
        clients: list[Any],
        *,
        ma_monitor_task: asyncio.Task[None] | None,
        demo_mode: bool,
        version: str,
        run_simulator_fn: Callable[[list[Any]], Coroutine[Any, Any, None]] | None = None,
        run_update_checker_fn: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        gather_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> Any:
        """Assemble and await the long-running bridge runtime tasks."""
        tasks = self.assemble_runtime_tasks(
            clients,
            ma_monitor_task=ma_monitor_task,
            demo_mode=demo_mode,
            version=version,
            run_simulator_fn=run_simulator_fn,
            run_update_checker_fn=run_update_checker_fn,
        )
        runtime_gather = gather_fn or asyncio.gather
        return await runtime_gather(*tasks)

    async def run_bridge_lifecycle(
        self,
        bootstrap: RuntimeBootstrap,
        *,
        version: str,
        client_factory: Callable[..., Any],
        bt_manager_factory: Callable[..., Any],
        filter_devices_fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
        load_saved_volume_fn: Callable[[str], int | None] | None = None,
        persist_enabled_fn: Callable[[str, bool], None] | None = None,
        web_main: Callable[[], None] | None = None,
    ) -> Any:
        """Run the remaining bridge lifecycle after runtime bootstrap is complete."""
        device_bootstrap = self.initialize_devices(
            bootstrap,
            client_factory=client_factory,
            bt_manager_factory=bt_manager_factory,
            filter_devices_fn=filter_devices_fn,
            load_saved_volume_fn=load_saved_volume_fn,
            persist_enabled_fn=persist_enabled_fn,
        )
        clients = device_bootstrap.clients
        web_thread = self.start_web_server(clients, web_main=web_main) if web_main else self.start_web_server(clients)
        loop = asyncio.get_running_loop()
        self.install_signal_handlers(loop)
        await self.configure_executor(len(clients), web_thread_name=web_thread.name)
        ma_bootstrap = await self.initialize_ma_integration(
            bootstrap.config, clients, server_host=bootstrap.server_host
        )
        return await self.run_runtime(
            clients,
            ma_monitor_task=ma_bootstrap.ma_monitor_task,
            demo_mode=bootstrap.demo_mode,
            version=version,
        )
