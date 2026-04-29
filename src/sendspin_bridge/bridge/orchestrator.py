"""Bridge-wide startup sequencing helpers."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

from sendspin_bridge.config import (
    detect_ha_addon_channel,
    ensure_bridge_name,
    load_config,
    resolve_base_listen_port,
    resolve_web_port,
)
from sendspin_bridge.services.bluetooth.device_activation import DeviceActivationContext, activate_device
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.diagnostics.sendspin_compat import (
    format_dependency_versions,
    get_runtime_dependency_versions,
)
from sendspin_bridge.services.diagnostics.update_checker import run_update_checker
from sendspin_bridge.services.infrastructure.port_bind_probe import is_port_available
from sendspin_bridge.services.lifecycle.bridge_runtime_state import set_activation_context
from sendspin_bridge.services.lifecycle.lifecycle_state import BridgeLifecycleState
from sendspin_bridge.services.music_assistant.ma_integration_service import BridgeMaIntegrationService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PulseAudio runtime hardening
# ---------------------------------------------------------------------------

_PA_FALLBACK_SINK = "sendspin_fallback"


def _harden_pulseaudio(*, disable_rescue_streams: bool) -> None:
    """Apply PulseAudio runtime tweaks before any subprocess is spawned.

    1. Ensure a null-sink exists so orphaned streams never land on a real BT device.
    2. Set that null-sink as the PA default — ``module-rescue-streams`` (if loaded)
       will move orphans there instead of a random BT sink.
    3. Optionally unload ``module-rescue-streams`` entirely (config-gated).
    """
    try:
        # 1. Create a null-sink fallback (idempotent — skip if already present)
        r = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        sink_exists = r.returncode == 0 and _PA_FALLBACK_SINK in r.stdout.split()

        if sink_exists:
            logger.info("PA hardening: null-sink '%s' already present", _PA_FALLBACK_SINK)
        else:
            r = subprocess.run(
                [
                    "pactl",
                    "load-module",
                    "module-null-sink",
                    f"sink_name={_PA_FALLBACK_SINK}",
                    "rate=44100",
                    "channels=2",
                    "sink_properties=device.description=Sendspin_Fallback",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                logger.info("PA hardening: loaded null-sink '%s'", _PA_FALLBACK_SINK)
            else:
                logger.warning("PA hardening: null-sink load returned rc=%d: %s", r.returncode, r.stderr.strip())

        # 2. Set the null-sink as default so rescue-streams targets it
        r = subprocess.run(
            ["pactl", "set-default-sink", _PA_FALLBACK_SINK],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            logger.info("PA hardening: default sink → %s", _PA_FALLBACK_SINK)
        else:
            logger.warning("PA hardening: set-default-sink returned rc=%d: %s", r.returncode, r.stderr.strip())

        # 3. Optionally unload module-rescue-streams
        if disable_rescue_streams:
            r = subprocess.run(
                ["pactl", "unload-module", "module-rescue-streams"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                logger.info("PA hardening: unloaded module-rescue-streams")
            else:
                logger.warning(
                    "PA hardening: module-rescue-streams unload returned rc=%d: %s",
                    r.returncode,
                    r.stderr.strip(),
                )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("PA hardening skipped (pactl unavailable): %s", exc)


# ---------------------------------------------------------------------------
# Bluetooth Class of Device hardening (per-adapter)
# ---------------------------------------------------------------------------


def _apply_adapter_device_class_overrides(adapters: list[dict[str, Any]]) -> None:
    """Apply ``BLUETOOTH_ADAPTERS[].device_class`` to each kernel controller.

    Called from startup once the experimental
    ``EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE`` flag is on. The value is a
    6-hex-digit CoD (e.g. ``0x00010c``) and is delivered via raw HCI
    ``Write_Class_Of_Device`` (OGF=0x03, OCF=0x0024) on a
    ``BTPROTO_HCI`` socket — same capability surface (``CAP_NET_RAW``)
    the AVRCP HCI monitor already uses.

    Resolution order for the kernel hci index:

    1. ``adapter['hci']`` from config (e.g. ``'hci0'``) when present —
       lets operators pin the override regardless of sysfs ordering.
    2. Sysfs MAC→hci lookup via ``services.bluetooth.build_hci_map`` —
       handles configs that only list MAC and trust the bridge to find
       the controller.

    Failure modes are logged at WARNING and never raise; a missing
    capability / non-Linux dev box / kernel rejection lets the bridge
    keep booting (the operator's other guarantees still hold; only the
    Samsung-Q workaround is degraded).
    """
    if not adapters:
        return

    # Lazy-imported to avoid pulling sysfs / btsocket at module import
    # time on non-Linux dev boxes.
    from sendspin_bridge.services.bluetooth import build_hci_map
    from sendspin_bridge.services.bluetooth.bt_class_of_device import apply_device_class_for_hex

    hci_map: dict[str, str] | None = None
    for entry in adapters:
        if not isinstance(entry, dict):
            continue
        hex_value = str(entry.get("device_class") or "").strip()
        if not hex_value:
            continue

        hci_label = str(entry.get("hci") or "").strip()
        if not hci_label:
            mac = str(entry.get("mac") or "").strip()
            if not mac:
                logger.warning(
                    "CoD: cannot apply device_class=%s — adapter entry has neither 'hci' nor 'mac'",
                    hex_value,
                )
                continue
            if hci_map is None:
                hci_map = build_hci_map()
            hci_label = hci_map.get(mac.upper().replace(":", ""), "")
            if not hci_label:
                logger.warning(
                    "CoD: cannot apply device_class=%s — sysfs has no hci entry for adapter %s",
                    hex_value,
                    mac,
                )
                continue

        # ``hci0`` → ``0`` — guard against malformed labels.
        if not hci_label.startswith("hci"):
            logger.warning("CoD: ignoring adapter entry with malformed hci label %r", hci_label)
            continue
        try:
            adapter_index = int(hci_label[3:])
        except ValueError:
            logger.warning("CoD: ignoring adapter entry with non-numeric hci suffix %r", hci_label)
            continue

        apply_device_class_for_hex(adapter_index, hex_value)


@dataclass
class RuntimeBootstrap:
    config: dict[str, Any]
    device_configs: list[dict[str, Any]]
    demo_mode: bool
    delivery_channel: str
    server_host: str
    server_port: int
    effective_bridge: str
    prefer_sbc: bool
    bt_check_interval: int
    bt_max_reconnect_fails: int
    bt_churn_threshold: int
    bt_churn_window: float
    enable_a2dp_sink_recovery_dance: bool
    enable_pa_module_reload: bool
    enable_adapter_auto_recovery: bool
    enable_rssi_badge: bool
    base_listen_port: int
    web_port: int
    pulse_latency_msec: int
    log_level: str
    cod_override_enabled: bool = False
    bluetooth_adapters: list[dict[str, Any]] = field(default_factory=list)


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

    def __init__(
        self,
        startup_steps: int = 6,
        *,
        lifecycle_state: BridgeLifecycleState | None = None,
        ma_integration_service: BridgeMaIntegrationService | None = None,
    ):
        self.startup_steps = startup_steps
        self.lifecycle_state = lifecycle_state or BridgeLifecycleState(startup_steps=startup_steps)
        self.ma_integration_service = ma_integration_service or BridgeMaIntegrationService()

    async def initialize_runtime(self) -> RuntimeBootstrap:
        """Load bridge config and apply process-wide runtime settings."""
        demo_mode = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")
        self.lifecycle_state.begin_startup(demo_mode=demo_mode)
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
        enable_a2dp_sink_recovery_dance = bool(config.get("EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE", False))
        enable_pa_module_reload = bool(config.get("EXPERIMENTAL_PA_MODULE_RELOAD", False))
        enable_adapter_auto_recovery = bool(config.get("EXPERIMENTAL_ADAPTER_AUTO_RECOVERY", False))
        # RSSI badge default is True since v2.64.0 (was opt-in / experimental
        # earlier).  ``config_migration`` migrates the legacy
        # ``EXPERIMENTAL_RSSI_BADGE`` key to ``RSSI_BADGE`` on load, so by the
        # time we read it the value is under the new name.  Default ``True``
        # here matches the schema default and applies to fresh configs.
        enable_rssi_badge = bool(config.get("RSSI_BADGE", True))
        if enable_a2dp_sink_recovery_dance:
            logger.info("EXPERIMENTAL: A2DP sink recovery dance enabled")
        if enable_pa_module_reload:
            logger.info("EXPERIMENTAL: PulseAudio module-bluez5-discover reload enabled")
        if enable_rssi_badge:
            logger.info("Live RSSI badge enabled — refreshing every 5 s via mgmt opcode 0x0031")
        if enable_adapter_auto_recovery:
            logger.info(
                "EXPERIMENTAL: adapter auto-recovery enabled — bluetooth-auto-recovery ladder will run at reconnect-fail threshold",
            )

        tz = os.getenv("TZ", config.get("TZ", "UTC"))
        os.environ["TZ"] = tz
        time.tzset()
        logger.info("Timezone: %s", tz)

        pulse_latency_msec = int(config.get("PULSE_LATENCY_MSEC") or 600)
        os.environ["PULSE_LATENCY_MSEC"] = str(pulse_latency_msec)
        logger.info("PULSE_LATENCY_MSEC: %s ms", pulse_latency_msec)

        delivery_channel = detect_ha_addon_channel()
        base_listen_port = resolve_base_listen_port()
        web_port = resolve_web_port()
        logger.info(
            "Delivery channel defaults: channel=%s, web_port=%s, base_listen_port=%s",
            delivery_channel,
            web_port,
            base_listen_port,
        )

        log_level = config.get("LOG_LEVEL", "INFO").upper()
        if log_level not in ("INFO", "DEBUG"):
            log_level = "INFO"
        logging.getLogger().setLevel(getattr(logging, log_level))
        os.environ["LOG_LEVEL"] = log_level
        logger.info("Log level: %s", log_level)
        logger.info("Runtime deps: %s", format_dependency_versions(get_runtime_dependency_versions()))

        # PulseAudio runtime hardening — before any subprocess is spawned
        _harden_pulseaudio(disable_rescue_streams=bool(config.get("DISABLE_PA_RESCUE_STREAMS", False)))

        # Per-adapter Class of Device override — Samsung Q-series workaround
        # (bluez/bluez#1025).  Gated behind the experimental flag because
        # the raw HCI Write_Class_Of_Device the applier sends competes with
        # bluetoothd's own CoD management; opt-in only for users hitting
        # the Q-series filter. When the flag is off the applier is skipped
        # entirely — even configured ``device_class`` entries are ignored.
        if config.get("EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE"):
            _apply_adapter_device_class_overrides(list(config.get("BLUETOOTH_ADAPTERS") or []))

        return RuntimeBootstrap(
            config=config,
            device_configs=list(config.get("BLUETOOTH_DEVICES", [])),
            demo_mode=demo_mode,
            delivery_channel=delivery_channel,
            server_host=server_host,
            server_port=server_port,
            effective_bridge=effective_bridge,
            prefer_sbc=prefer_sbc,
            bt_check_interval=bt_check_interval,
            bt_max_reconnect_fails=bt_max_reconnect_fails,
            bt_churn_threshold=bt_churn_threshold,
            bt_churn_window=bt_churn_window,
            enable_a2dp_sink_recovery_dance=enable_a2dp_sink_recovery_dance,
            enable_pa_module_reload=enable_pa_module_reload,
            enable_adapter_auto_recovery=enable_adapter_auto_recovery,
            cod_override_enabled=bool(config.get("EXPERIMENTAL_BT_DEVICE_CLASS_OVERRIDE", False)),
            bluetooth_adapters=list(config.get("BLUETOOTH_ADAPTERS") or []),
            enable_rssi_badge=enable_rssi_badge,
            base_listen_port=base_listen_port,
            web_port=web_port,
            pulse_latency_msec=pulse_latency_msec,
            log_level=log_level,
        )

    async def configure_executor(self, device_count: int, *, web_thread_name: str = "") -> int:
        """Register the main loop and size its default executor for bridge load."""
        pool_size = min(64, max(8, device_count * 2 + 4))
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=pool_size))
        logger.debug("ThreadPoolExecutor: max_workers=%s", pool_size)
        self.lifecycle_state.publish_main_loop(asyncio.get_running_loop(), web_thread_name=web_thread_name)
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
            from sendspin_bridge.web.interface import main as imported_web_main

            web_main_fn = imported_web_main

        self.lifecycle_state.publish_clients(clients)

        def _run_web_server() -> None:
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
        get_sink_volume: Callable[[str], Awaitable[int | None]] | None = None,
        save_volume: Callable[[str | None, int], None] | None = None,
        sink_monitor: Any | None = None,
        hci_monitor: Any | None = None,
    ) -> None:
        """Mute active sinks, stop the sink monitor, and stop all clients."""
        logger.info("Received shutdown signal — muting sinks before exit...")
        mute_sink_fn = mute_sink
        if mute_sink_fn is None:
            from sendspin_bridge.services.audio.pulse import aset_sink_mute as imported_mute_sink

            mute_sink_fn = imported_mute_sink
        get_sink_volume_fn = get_sink_volume
        if get_sink_volume_fn is None:
            from sendspin_bridge.services.audio.pulse import aget_sink_volume as imported_get_sink_volume

            get_sink_volume_fn = imported_get_sink_volume
        save_volume_fn = save_volume
        if save_volume_fn is None:
            from sendspin_bridge.config import save_device_volume as imported_save_volume

            save_volume_fn = imported_save_volume

        shutdown_clients = list(clients) if clients is not None else get_device_registry_snapshot().active_clients
        self.lifecycle_state.publish_shutdown_started(active_clients=len(shutdown_clients))
        muted: list[str] = []
        for client in shutdown_clients:
            sink = getattr(client, "bluetooth_sink_name", None)
            mac = getattr(getattr(client, "bt_manager", None), "mac_address", None)
            if sink and mac:
                try:
                    sink_volume = await get_sink_volume_fn(sink)
                    if isinstance(sink_volume, int) and 0 <= sink_volume <= 100:
                        save_volume_fn(mac, sink_volume)
                except Exception as exc:
                    logger.debug("[%s] Could not persist sink volume during shutdown: %s", client.player_name, exc)
            if sink and await mute_sink_fn(sink, True):
                muted.append(sink)
        if muted:
            logger.info("Muted %d sink(s): %s", len(muted), ", ".join(muted))

        for client in shutdown_clients:
            client.running = False
            await client.stop_sendspin()

        if sink_monitor is not None:
            await sink_monitor.stop()
        if hci_monitor is not None:
            await hci_monitor.stop()

        self.lifecycle_state.publish_clients([])
        self.lifecycle_state.publish_shutdown_complete(stopped_clients=len(shutdown_clients))

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
            async def _shutdown_and_exit() -> None:
                try:
                    await shutdown_factory_fn()
                except Exception:
                    logger.exception("Error during graceful shutdown")
                finally:
                    for task in asyncio.all_tasks(loop):
                        if task is not asyncio.current_task():
                            task.cancel()

            loop.create_task(_shutdown_and_exit())

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
        resolved = await self.ma_integration_service.initialize(config, clients, server_host=server_host)

        self.lifecycle_state.publish_ma_integration(
            ma_api_url=resolved.ma_api_url,
            ma_api_token=resolved.ma_api_token,
            groups_loaded=resolved.groups_loaded,
            name_map=resolved.name_map,
            all_groups=resolved.all_groups,
            monitor_enabled=bool(resolved.ma_monitor_task),
        )
        return MaBootstrap(
            ma_api_url=resolved.ma_api_url,
            ma_api_token=resolved.ma_api_token,
            ma_monitor_task=resolved.ma_monitor_task,
        )

    async def _run_duplicate_device_check(self, config: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
        """Check MA API for devices already claimed by another bridge instance."""
        if not config.get("DUPLICATE_DEVICE_CHECK", True):
            return
        bridge_name = str(config.get("BRIDGE_NAME") or "").strip()
        try:
            from sendspin_bridge.bridge.state import set_duplicate_device_warnings
            from sendspin_bridge.services.bluetooth.duplicate_device_check import find_duplicate_devices

            warnings = await loop.run_in_executor(None, find_duplicate_devices, config, bridge_name)
            set_duplicate_device_warnings(warnings)
            for w in warnings:
                logger.warning(
                    "[%s] Also registered in MA as '%s' — may conflict with another bridge instance",
                    w.device_name,
                    w.other_bridge_name,
                )
        except Exception:
            logger.debug("Duplicate device check failed", exc_info=True)

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
        self.lifecycle_state.publish_runtime_prepared(
            configured_devices=len(bt_devices),
            log_level=bootstrap.log_level,
            pulse_latency_msec=bootstrap.pulse_latency_msec,
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

        activation_context = DeviceActivationContext(
            server_host=bootstrap.server_host,
            server_port=bootstrap.server_port,
            effective_bridge=bootstrap.effective_bridge,
            prefer_sbc=bootstrap.prefer_sbc,
            bt_check_interval=bootstrap.bt_check_interval,
            bt_max_reconnect_fails=bootstrap.bt_max_reconnect_fails,
            bt_churn_threshold=bootstrap.bt_churn_threshold,
            bt_churn_window=bootstrap.bt_churn_window,
            enable_a2dp_sink_recovery_dance=bootstrap.enable_a2dp_sink_recovery_dance,
            enable_pa_module_reload=bootstrap.enable_pa_module_reload,
            enable_adapter_auto_recovery=bootstrap.enable_adapter_auto_recovery,
            cod_override_enabled=getattr(bootstrap, "cod_override_enabled", False),
            bluetooth_adapters=getattr(bootstrap, "bluetooth_adapters", []),
            enable_rssi_badge=bootstrap.enable_rssi_badge,
            base_listen_port=base_listen_port,
            client_factory=client_factory,
            bt_manager_factory=bt_manager_factory,
            default_player_name=resolved_default_name,
            load_saved_volume_fn=load_saved_volume_fn,
            persist_enabled_fn=persist_enabled_fn,
        )
        # Published so reconfig_orchestrator._apply_start_client() can reach
        # the same factories from a Flask request thread when the user adds a
        # device via POST /api/config (online activation path).
        set_activation_context(activation_context)

        for index, device in enumerate(bt_devices):
            mac = str(device.get("mac") or "")
            adapter = str(device.get("adapter") or "")
            player_name_raw = str(device.get("player_name") or resolved_default_name)
            player_name = (
                f"{player_name_raw} @ {bootstrap.effective_bridge}" if bootstrap.effective_bridge else player_name_raw
            )

            if not device.get("enabled", True):
                # Carry the saved per-device config knobs into the
                # disabled_devices entry so the HA state projector
                # surfaces real values instead of hard-coded defaults.
                # Without this enrichment, HA's idle_mode/keep_alive/
                # static_delay/power_save_delay entities for a disabled
                # device would always show defaults and a write-back
                # from HA would silently overwrite the operator's saved
                # settings.
                disabled_entry: dict[str, Any] = {
                    "player_name": player_name,
                    "mac": mac,
                    "adapter": adapter,
                    "enabled": False,
                }
                for cfg_key in (
                    "idle_mode",
                    "keep_alive_method",
                    "static_delay_ms",
                    "power_save_delay_minutes",
                    "bt_management_enabled",
                    "preferred_format",
                    "room_id",
                    "room_name",
                ):
                    if cfg_key in device:
                        disabled_entry[cfg_key] = device[cfg_key]
                disabled_list.append(disabled_entry)
                logger.info("  Player '%s': globally disabled — skipping", player_name)
                continue

            result = activate_device(
                device,
                index=index,
                context=activation_context,
                default_player_name=resolved_default_name,
            )
            clients.append(result.client)
            logger.info("  Player: '%s', BT: %s, Adapter: %s", player_name, mac or "none", adapter or "default")

        logger.info("Client instance(s) registered")
        self.lifecycle_state.publish_device_registry(
            configured_devices=len(bt_devices),
            active_clients=clients,
            disabled_devices=disabled_list,
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
            elif not is_port_available(listen_port):
                logger.warning(
                    "[%s] Configured listen_port %d is already in use on the host. "
                    "Bridge will auto-shift at startup. Run 'lsof -i :%d' to identify the owner.",
                    client.player_name,
                    listen_port,
                    listen_port,
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

        # HA integration (MQTT publisher) — best-effort start.  Failures
        # here MUST NOT abort runtime assembly: the bridge core must keep
        # running even if HA's broker is unreachable or aiomqtt missing.
        ha_task = self._start_ha_integration(version=version)
        if ha_task is not None:
            tasks.append(ha_task)

        self.lifecycle_state.complete_startup(
            active_clients=clients,
            demo_mode=demo_mode,
            monitor_enabled=bool(ma_monitor_task),
        )
        return tasks

    def _start_ha_integration(self, *, version: str) -> asyncio.Task[Any] | None:
        """Construct + start the HA integration lifecycle.  Returns the
        publisher's main task so ``assemble_runtime_tasks`` can include it
        in the gather() set.

        Also kicks off the mDNS advertiser when ``HA_INTEGRATION.rest.advertise_mdns``
        is enabled — this is fire-and-forget and not part of the gather()
        set because it's an asyncio.Event-based registration that lives
        for the bridge's lifetime.
        """
        try:
            from sendspin_bridge.bridge.state import get_clients_snapshot, get_internal_event_publisher
            from sendspin_bridge.services.ha.ha_integration_lifecycle import (
                HaIntegrationLifecycle,
                set_default_lifecycle,
            )
            from sendspin_bridge.services.ha.ha_state_projector import project_snapshot
            from sendspin_bridge.services.lifecycle.status_snapshot import build_bridge_snapshot
        except Exception as exc:  # pragma: no cover — import-time guard
            logger.info("HA integration unavailable (import failed): %s", exc)
            return None

        try:
            event_publisher = get_internal_event_publisher()
        except Exception as exc:
            logger.info("HA integration: internal event publisher unavailable (%s)", exc)
            return None

        bridge_name = ensure_bridge_name(load_config())
        # Stable bridge_id derived from bridge_name; HA's MQTT discovery
        # uses this in object_id slugs that must not collide with another
        # Sendspin Bridge running on the same broker.
        import hashlib

        bridge_id = hashlib.sha1(bridge_name.encode("utf-8")).hexdigest()[:12]

        # mDNS advertisement (Path A1) — fire-and-forget.  Errors are
        # logged inside the advertiser; the bridge keeps running either way.
        self._start_mdns_advertiser(bridge_name=bridge_name, version=version)

        def _projection_provider():
            snapshot = build_bridge_snapshot(get_clients_snapshot())
            return project_snapshot(
                snapshot,
                bridge_id=bridge_id,
                bridge_name=bridge_name,
                runtime_extras={"version": version},
            )

        lifecycle = HaIntegrationLifecycle(
            event_publisher=event_publisher,
            projection_provider=_projection_provider,
            bridge_id_provider=lambda: bridge_id,
            bridge_name_provider=lambda: bridge_name,
        )
        set_default_lifecycle(lifecycle)
        publisher = lifecycle.start()
        if publisher is None:
            return None
        return publisher._task

    def _start_mdns_advertiser(self, *, bridge_name: str, version: str) -> None:
        try:
            from sendspin_bridge.config import resolve_web_port
            from sendspin_bridge.services.ipc.bridge_mdns import BridgeMdnsAdvertiser, set_default_advertiser
        except Exception as exc:  # pragma: no cover
            logger.info("mDNS advertiser unavailable (import failed): %s", exc)
            return

        config = load_config()
        block = config.get("HA_INTEGRATION") or {}
        if not block.get("enabled"):
            return
        # mDNS only runs when the REST / custom_component transport is
        # selected.  ``load_config()`` runs ``_normalize_loaded_config()``
        # which already coerces legacy ``"both"`` → ``"mqtt"`` (see
        # ``config_migration._normalize_ha_integration``), so the value
        # we see here is one of ``off`` / ``mqtt`` / ``rest``.
        mode = str(block.get("mode") or "off").lower()
        if mode != "rest":
            return
        rest_block = block.get("rest") or {}
        if not rest_block.get("advertise_mdns", True):
            return

        web_port = resolve_web_port() or 8080
        ingress_active = bool(os.environ.get("SUPERVISOR_TOKEN"))

        adv = BridgeMdnsAdvertiser(
            bridge_name=bridge_name,
            version=version,
            web_port=web_port,
            ingress_active=ingress_active,
        )
        set_default_advertiser(adv)
        # Kick the registration onto the running loop.  We don't await
        # it because zeroconf's registration is itself async-fire-and-forget.
        try:
            asyncio.create_task(adv.start(), name="bridge_mdns_register")
        except RuntimeError as exc:
            logger.debug("mDNS register: no running loop (%s)", exc)

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
        startup_phase = "devices"
        device_bootstrap = self.initialize_devices(
            bootstrap,
            client_factory=client_factory,
            bt_manager_factory=bt_manager_factory,
            filter_devices_fn=filter_devices_fn,
            load_saved_volume_fn=load_saved_volume_fn,
            persist_enabled_fn=persist_enabled_fn,
            base_listen_port=bootstrap.base_listen_port,
        )
        clients = device_bootstrap.clients

        # Start PA sink state monitor (idle disconnect ground truth).
        from sendspin_bridge.services.audio.sink_monitor import SinkMonitor

        sink_monitor = SinkMonitor()
        for client in clients:
            client._sink_monitor = sink_monitor

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import get_monitor as _get_hci_monitor

        hci_monitor = _get_hci_monitor()

        try:
            await sink_monitor.start()
            await hci_monitor.start()
            startup_phase = "web"
            web_thread = (
                self.start_web_server(clients, web_main=web_main) if web_main else self.start_web_server(clients)
            )
            loop = asyncio.get_running_loop()
            await self.configure_executor(len(clients), web_thread_name=web_thread.name)
            startup_phase = "signals"
            self.install_signal_handlers(
                loop,
                shutdown_factory=lambda: self.graceful_shutdown(
                    clients=clients, sink_monitor=sink_monitor, hci_monitor=hci_monitor
                ),
            )
            startup_phase = "integrations"
            ma_bootstrap = await self.initialize_ma_integration(
                bootstrap.config, clients, server_host=bootstrap.server_host
            )
            # Non-blocking cross-bridge duplicate device check
            await self._run_duplicate_device_check(bootstrap.config, loop)
        except Exception as exc:
            self.lifecycle_state.publish_startup_failure(
                f"Startup failed during {startup_phase}: {exc}",
                phase=startup_phase,
                details={"error_type": type(exc).__name__},
            )
            await sink_monitor.stop()
            await hci_monitor.stop()
            raise
        try:
            return await self.run_runtime(
                clients,
                ma_monitor_task=ma_bootstrap.ma_monitor_task,
                demo_mode=bootstrap.demo_mode,
                version=version,
            )
        finally:
            await sink_monitor.stop()
            await hci_monitor.stop()
