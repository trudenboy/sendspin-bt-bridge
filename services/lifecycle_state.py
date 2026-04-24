"""Bridge lifecycle state publication helpers."""

from __future__ import annotations

from typing import Any

import state as _state
from services.device_registry import set_active_clients, set_disabled_devices


class BridgeLifecycleState:
    """Own publication of bridge-wide lifecycle state into the shared store."""

    def __init__(self, startup_steps: int = 6):
        self.startup_steps = startup_steps

    def begin_startup(self, *, demo_mode: bool) -> None:
        """Publish initial runtime mode and startup progress."""
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
        _state.publish_bridge_event("bridge.startup.started", payload={"demo_mode": demo_mode})

    def publish_main_loop(self, loop, *, web_thread_name: str = "") -> None:
        """Publish the main event loop and web readiness progress."""
        _state.set_main_loop(loop)
        _state.update_startup_progress(
            "web",
            "Web interface and event loop ready",
            current_step=4,
            details={"web_thread": web_thread_name} if web_thread_name else {},
        )

    def publish_clients(self, clients: list[Any]) -> None:
        """Publish active clients for route and UI access."""
        set_active_clients(clients)

    def publish_runtime_prepared(
        self,
        *,
        configured_devices: int,
        log_level: str,
        pulse_latency_msec: int,
    ) -> None:
        """Publish runtime configuration progress after device filtering."""
        _state.update_startup_progress(
            "runtime",
            "Runtime configuration prepared",
            current_step=2,
            details={
                "configured_devices": configured_devices,
                "log_level": log_level,
                "pulse_latency_msec": pulse_latency_msec,
            },
        )

    def publish_device_registry(
        self,
        *,
        configured_devices: int,
        active_clients: list[Any],
        disabled_devices: list[dict[str, Any]],
    ) -> None:
        """Publish device registry inventory and related startup progress."""
        set_disabled_devices(disabled_devices)
        _state.update_startup_progress(
            "devices",
            "Device registry prepared",
            current_step=3,
            details={
                "configured_devices": configured_devices,
                "active_clients": len(active_clients),
                "disabled_devices": len(disabled_devices),
            },
        )

    def publish_ma_integration(
        self,
        *,
        ma_api_url: str,
        ma_api_token: str,
        groups_loaded: bool,
        name_map: dict[str, dict[str, Any]] | None = None,
        all_groups: list[dict[str, Any]] | None = None,
        monitor_enabled: bool,
    ) -> None:
        """Publish MA credentials, optional group cache, and integration progress."""
        if ma_api_url and ma_api_token:
            _state.set_ma_api_credentials(ma_api_url, ma_api_token)
        if groups_loaded:
            _state.set_ma_groups(name_map or {}, all_groups or [])
            if name_map:
                _state.set_ma_connected(True)
        _state.update_startup_progress(
            "integrations",
            "Music Assistant integrations initialized",
            current_step=5,
            details={
                "ma_configured": bool(ma_api_url and ma_api_token),
                "ma_monitor_enabled": monitor_enabled,
            },
        )

    def publish_startup_failure(
        self,
        message: str,
        *,
        phase: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Mark startup as failed with an explicit lifecycle phase marker."""
        payload = {"startup_phase": phase}
        if details:
            payload.update(details)
        _state.fail_startup_progress(message, details=payload)
        _state.publish_bridge_event(
            "bridge.startup.failed",
            payload={"message": message, "startup_phase": phase, **payload},
        )

    def complete_startup(
        self,
        *,
        active_clients: list[Any],
        demo_mode: bool,
        monitor_enabled: bool,
    ) -> None:
        """Mark bridge startup as complete with final runtime details."""
        _state.complete_startup_progress(
            "Startup complete",
            details={
                "active_clients": len(active_clients),
                "ma_monitor_enabled": monitor_enabled,
                "demo_mode": demo_mode,
            },
        )
        _state.publish_bridge_event(
            "bridge.startup.completed",
            payload={
                "active_clients": len(active_clients),
                "ma_monitor_enabled": monitor_enabled,
                "demo_mode": demo_mode,
            },
        )

    def publish_shutdown_started(self, *, active_clients: int) -> None:
        """Mark bridge shutdown as in progress with explicit lifecycle metadata."""
        _state.update_startup_progress(
            "shutdown",
            "Shutdown in progress",
            current_step=self.startup_steps,
            total_steps=self.startup_steps,
            status="stopping",
            details={"active_clients": active_clients},
        )
        _state.publish_bridge_event("bridge.shutdown.started", payload={"active_clients": active_clients})

    def publish_shutdown_complete(self, *, stopped_clients: int) -> None:
        """Mark bridge shutdown as complete and clear loop publication."""
        from services.bridge_runtime_state import set_activation_context

        _state.set_main_loop(None)
        # Clear the activation context the startup path published so Flask
        # threads that outlive the bridge process (e.g. in-test graceful
        # shutdown) can't accidentally materialize a new client against
        # torn-down factories.  Matches the set_main_loop(None) semantic.
        set_activation_context(None)
        _state.update_startup_progress(
            "shutdown",
            "Shutdown complete",
            current_step=self.startup_steps,
            total_steps=self.startup_steps,
            status="stopped",
            details={"stopped_clients": stopped_clients},
        )
        _state.publish_bridge_event("bridge.shutdown.completed", payload={"stopped_clients": stopped_clients})
