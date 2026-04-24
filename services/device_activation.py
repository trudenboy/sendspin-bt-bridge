"""Factory for materializing a SendspinClient + BluetoothManager pair.

Used by the bridge startup path (``bridge_orchestrator.initialize_devices``)
and by online-reconfig (``reconfig_orchestrator._apply_start_client``) so
both entry points share the same client-wiring semantics.

The factory is intentionally stateless — all runtime dependencies are
captured in :class:`DeviceActivationContext`, which the startup path
publishes via :mod:`services.bridge_runtime_state` so the reconfig
orchestrator can pull it from a Flask request thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceActivationContext:
    """Runtime dependencies needed to build a client + BT manager pair.

    Captured once at startup and stored on the main runtime-state module so
    Flask-thread code (``POST /api/config``) can reach it without walking
    the bridge orchestrator.
    """

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
    base_listen_port: int
    client_factory: Callable[..., Any]
    bt_manager_factory: Callable[..., Any]
    # ``Sendspin-<hostname>`` by default, with ``SENDSPIN_NAME`` / caller
    # override applied at startup.  Captured here so online-activation
    # reuses the exact same default when a new device omits ``player_name``
    # — otherwise the restart-vs-live-add paths diverge on naming and
    # downstream identity mapping (MA, UI) breaks across a restart.
    default_player_name: str = "Sendspin"
    load_saved_volume_fn: Callable[[str], int | None] | None = None
    persist_enabled_fn: Callable[[str, bool], None] | None = None


@dataclass
class ActivationResult:
    """Outcome of :func:`activate_device`."""

    client: Any
    bt_manager: Any | None
    bt_available: bool
    listen_port: int


def activate_device(
    device: dict[str, Any],
    *,
    index: int,
    context: DeviceActivationContext,
    default_player_name: str,
) -> ActivationResult:
    """Build a fully-wired SendspinClient + BluetoothManager from a device dict.

    Mirrors the per-device body of
    ``BridgeOrchestrator.initialize_devices`` — port math, keepalive
    clamps, ``_on_sink_found`` closure with sink-monitor registration,
    BT-availability probe, persisted volume restore, and released-state
    restore — so both startup and live reconfig share the exact same
    client-wiring semantics.

    ``index`` is used only as the fallback component of
    ``base_listen_port + index`` when the device dict doesn't supply an
    explicit ``listen_port``.

    Raises the same exceptions the underlying factories raise; callers
    decide whether to surface them as ``summary.errors`` or propagate.
    """
    mac = str(device.get("mac") or "")
    adapter = str(device.get("adapter") or "")
    player_name = str(device.get("player_name") or default_player_name)
    if context.effective_bridge:
        player_name = f"{player_name} @ {context.effective_bridge}"

    listen_port = int(device.get("listen_port") or context.base_listen_port + index)
    listen_host = device.get("listen_host")
    static_delay_ms = device.get("static_delay_ms")
    if static_delay_ms is not None:
        static_delay_ms = float(static_delay_ms)
    preferred_format = device.get("preferred_format", "flac:44100:16:2")
    keepalive_interval = int(device.get("keepalive_interval") or 0)
    keepalive_enabled = keepalive_interval > 0
    keepalive_interval = max(30, keepalive_interval) if keepalive_enabled else 30
    idle_disconnect_minutes = int(device.get("idle_disconnect_minutes") or 0)
    idle_mode = str(device.get("idle_mode") or "default")
    power_save_delay_minutes = int(device.get("power_save_delay_minutes") or 1)

    client = context.client_factory(
        player_name,
        context.server_host,
        context.server_port,
        None,
        listen_port=listen_port,
        static_delay_ms=static_delay_ms,
        listen_host=listen_host,
        effective_bridge=context.effective_bridge,
        preferred_format=preferred_format or None,
        keepalive_enabled=keepalive_enabled,
        keepalive_interval=keepalive_interval,
        idle_disconnect_minutes=idle_disconnect_minutes,
        idle_mode=idle_mode,
        power_save_delay_minutes=power_save_delay_minutes,
    )

    bt_mgr: Any | None = None
    bt_available = False
    if mac:

        def _on_sink_found(
            sink_name: str,
            restored_volume: int | None = None,
            _client: Any = client,
            _mac: str = mac,
        ) -> None:
            _client.bluetooth_sink_name = sink_name
            logger.info("Stored Bluetooth sink for volume sync: %s", sink_name)
            if restored_volume is not None:
                _client._update_status({"volume": restored_volume})
            sm = getattr(_client, "_sink_monitor", None)
            if sm is not None and _mac:
                sm.register(_mac, sink_name, _client._on_sink_active, _client._on_sink_idle)

        bt_mgr = context.bt_manager_factory(
            mac,
            adapter=adapter,
            device_name=player_name,
            host=client,
            prefer_sbc=context.prefer_sbc,
            check_interval=context.bt_check_interval,
            max_reconnect_fails=context.bt_max_reconnect_fails,
            on_sink_found=_on_sink_found,
            churn_threshold=context.bt_churn_threshold,
            churn_window=context.bt_churn_window,
            enable_a2dp_dance=context.enable_a2dp_sink_recovery_dance,
            enable_pa_module_reload=context.enable_pa_module_reload,
            enable_adapter_auto_recovery=context.enable_adapter_auto_recovery,
        )
        bt_available = bool(bt_mgr.check_bluetooth_available())
        if not bt_available:
            logger.warning("BT adapter '%s' not available for %s", adapter or "default", player_name)
        client.bt_manager = bt_mgr
        client._update_status({"bluetooth_available": bt_available})
        if context.load_saved_volume_fn is not None:
            saved_volume = context.load_saved_volume_fn(mac)
            if saved_volume is not None and 0 <= saved_volume <= 100:
                client._update_status({"volume": saved_volume})

    if device.get("released", False):
        client.set_bt_management_enabled(False)
        logger.info("  Player '%s': restored released state", player_name)

    return ActivationResult(
        client=client,
        bt_manager=bt_mgr,
        bt_available=bt_available,
        listen_port=listen_port,
    )
