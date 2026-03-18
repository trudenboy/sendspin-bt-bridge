"""Bridge-wide startup sequencing helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

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
