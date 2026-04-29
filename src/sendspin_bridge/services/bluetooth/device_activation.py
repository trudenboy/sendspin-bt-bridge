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

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.audio.mpris_player import (
    MprisPlayer,
    _build_player_iface,
    get_registry,
    resolve_avrcp_source_client,
)
from sendspin_bridge.services.bluetooth.avrcp_source_tracker import get_tracker as _get_avrcp_source_tracker

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# BlueZ delivers the inbound AVRCP D-Bus dispatch to our process before
# the kernel's HCI_CHANNEL_MONITOR copy reaches us — observed empirically
# at ~2-10ms on VM 105 in steady state, with the first post-restart event
# at ~115ms (cold ``asyncio.to_thread`` worker thread spin-up).  Without
# synchronisation the resolver runs against an empty AvrcpSourceTracker
# and falls back to ``default_client``, mis-routing the press.
#
# We synchronise via ``AvrcpSourceTracker.wait_for_next_activity`` —
# the resolver awaits the *next* note_activity (from HCI monitor or
# D-Bus PropertiesChanged) and resolves immediately on its arrival,
# instead of guessing a fixed sleep.  Common-case latency drops to
# ~5ms; cold-start no longer mis-routes.
#
# This timeout is the *safety cap* for degraded modes — HCI monitor
# unavailable (no CAP_NET_RAW) AND speaker has no MediaPlayer1 either.
# In normal operation users never see it.
_INBOUND_AVRCP_HCI_WAIT_S = 1.0


def _is_single_speaker_adapter(default_client: Any) -> bool:
    """True iff *default_client* is the ONLY MprisPlayer-bound client on
    its adapter.

    BlueZ on adapter X dispatches every speaker's AVRCP to its single
    addressed player on X.  When only one MprisPlayer is registered for
    X, that's the unambiguous source — no need to wait for HCI source
    correlation; dispatch directly via ``default_client``.

    Tolerant of missing ``bt_manager`` / ``adapter_hci_name`` (returns
    False so the caller takes the safe slow path) so test fixtures that
    skip wiring the bt_manager get the existing source-correlation
    behaviour.
    """
    my_adapter = getattr(getattr(default_client, "bt_manager", None), "adapter_hci_name", None)
    if not my_adapter:
        return False
    same_adapter = 0
    for player in get_registry().all_players():
        client = getattr(player, "client", None)
        if client is None:
            continue
        adapter = getattr(getattr(client, "bt_manager", None), "adapter_hci_name", None)
        if adapter == my_adapter:
            same_adapter += 1
            if same_adapter > 1:
                return False
    return same_adapter == 1


def _resolve_adapter_device_class(device_adapter: str, adapters: list[dict[str, Any]]) -> str:
    """Look up the configured ``device_class`` for a device's adapter.

    *device_adapter* is the value of ``BLUETOOTH_DEVICES[].adapter`` —
    either an ``hciN`` label or a MAC address. *adapters* is the
    ``BLUETOOTH_ADAPTERS`` config list. Returns the matching adapter's
    ``device_class`` hex string (e.g. ``"0x00010c"``) or empty string
    when no match / no override is configured.

    Empty input fields are tolerated; we just return ``""`` so the
    caller passes a no-op into BluetoothManager and the pre-pair hook
    short-circuits.
    """
    if not device_adapter or not adapters:
        return ""
    target = device_adapter.strip()
    target_normalized = target.upper().replace(":", "")
    for entry in adapters:
        if not isinstance(entry, dict):
            continue
        hex_value = str(entry.get("device_class") or "").strip()
        if not hex_value:
            continue
        hci_label = str(entry.get("hci") or "").strip()
        mac = str(entry.get("mac") or "").strip()
        mac_normalized = mac.upper().replace(":", "")
        if hci_label and hci_label == target:
            return hex_value
        if mac_normalized and mac_normalized == target_normalized:
            return hex_value
    return ""


# BlueZ AVRCP forwarding architecture (v2.63.0-rc.6+):
#
# When a BR/EDR speaker presses Play/Pause, BlueZ on the bridge side
# receives the AVRCP passthrough command and forwards it to a player
# REGISTERED via ``org.bluez.Media1.RegisterPlayer(path, properties)``
# on the corresponding adapter (``/org/bluez/<hciN>``).  The registered
# player object implements ``org.mpris.MediaPlayer2.Player`` — BlueZ
# calls our ``Play`` / ``Pause`` / ``Next`` / etc methods directly.
#
# Earlier rcs only ``bus.export``-ed the player path and tried to claim
# a well-known ``org.mpris.MediaPlayer2.sendspin_*`` bus name.  Neither
# step is what BlueZ uses for AVRCP forwarding — system-bus name
# requests are blocked by default ACL anyway, and BlueZ doesn't scan
# bus names; it only routes to paths handed to it via Media1.
_MPRIS_PATH_PREFIX = "/org/sendspin/players/"


def _mpris_dbus_path(mac: str) -> str:
    """Per-device MPRIS object path on the system bus.

    BlueZ's ``Media1.RegisterPlayer`` expects a unique path per
    registration on a given adapter — multiple speakers on the same
    adapter must each get a distinct player object.  MAC colons map
    to ``_`` because D-Bus paths must be ``[A-Za-z0-9_/]``.
    """
    return _MPRIS_PATH_PREFIX + mac.upper().replace(":", "_")


def _bluez_adapter_path(bt_manager: Any) -> str | None:
    """Return ``/org/bluez/<hciN>`` for the adapter this device is on.

    ``BluetoothManager.adapter_hci_name`` is resolved at startup from
    sysfs (``/sys/class/bluetooth/<hciN>``); when empty we can't tell
    which adapter to register against and the caller skips the
    Media1.RegisterPlayer step.
    """
    hci = getattr(bt_manager, "adapter_hci_name", "") or ""
    if not hci.startswith("hci"):
        return None
    return f"/org/bluez/{hci}"


def _build_mpris_transport_callback(default_client: Any) -> Callable[[str, str], Any]:
    """AVRCP method → SendspinClient transport command.

    Speaker buttons (Play/Pause/Stop/Next/Previous) reach the bridge via
    BlueZ MPRIS forwarding.  BlueZ picks ONE registered MPRIS player as
    the addressed player on the adapter and forwards every speaker's
    AVRCP commands to that one player — the source CT identity is stripped
    by the BlueZ AVRCP TG layer.  See ``services/avrcp_source_tracker``.

    To recover the source we await ``tracker.wait_for_next_activity``
    (fed by ``services/hci_avrcp_monitor`` from raw HCI traffic) and
    then call ``resolve_avrcp_source_client`` to look up the source
    MAC's MprisPlayer in the registry.  When the tracker has nothing
    recent (HCI-degraded mode, packet lost), the resolver returns
    ``default_client`` as a best-guess fallback — for single-speaker-
    per-adapter setups this is by construction correct, and that case
    short-circuits the wait+resolve via ``_is_single_speaker_adapter``
    for sub-millisecond latency.
    """

    async def _cb(_player_id: str, command: str) -> bool:
        if _is_single_speaker_adapter(default_client):
            client: Any = default_client
        else:
            await _get_avrcp_source_tracker().wait_for_next_activity(timeout=_INBOUND_AVRCP_HCI_WAIT_S)
            client = resolve_avrcp_source_client(default_client=default_client)
        if client is None:
            logger.info(
                "MPRIS transport %s: no source client identifiable, dropping (default=%r)",
                command,
                getattr(default_client, "player_name", "?"),
            )
            return False
        routed = getattr(client, "player_name", "?")
        default = getattr(default_client, "player_name", "?")
        if routed == default:
            logger.info("MPRIS transport %s → %s", command, routed)
        else:
            logger.info(
                "MPRIS transport %s → %s (BlueZ default=%s, corrected via HCI source)",
                command,
                routed,
                default,
            )
        try:
            ok = await client.send_transport_command(command)
        except Exception as exc:
            logger.warning(
                "MPRIS transport %s for %s failed: %s",
                command,
                routed,
                exc,
            )
            return False
        return bool(ok)

    return _cb


def _build_mpris_volume_callback(default_client: Any) -> Callable[[str, int], Any]:
    """AVRCP absolute volume → bridge volume.

    Note: speaker physical volume knob → AVRCP Set Absolute Volume PDU
    is NOT a passthrough op_id, and BlueZ does NOT forward it to our
    exported MPRIS Volume property — that path is covered by
    ``PulseVolumeController.set_external_change_tap`` in the daemon.
    This callback only fires for explicit MPRIS Volume property writes
    from BlueZ (rare in practice — most speakers route through the
    AVRCP Set Absolute Volume PDU).

    Same source-correlation logic as the transport path applies when
    it does fire: HCI tracker via wait_for_next_activity, resolver
    falls back to ``default_client`` for HCI-degraded mode, single-
    speaker-per-adapter short-circuits to ``default_client`` directly.
    """

    async def _cb(_player_id: str, volume_pct: int) -> bool:
        if _is_single_speaker_adapter(default_client):
            client: Any = default_client
        else:
            await _get_avrcp_source_tracker().wait_for_next_activity(timeout=_INBOUND_AVRCP_HCI_WAIT_S)
            client = resolve_avrcp_source_client(default_client=default_client)
        if client is None:
            logger.info(
                "MPRIS volume->%d: no source client identifiable, dropping (default=%r)",
                volume_pct,
                getattr(default_client, "player_name", "?"),
            )
            return False
        routed = getattr(client, "player_name", "?")
        default = getattr(default_client, "player_name", "?")
        if routed == default:
            logger.info("MPRIS volume %d%% → %s", volume_pct, routed)
        else:
            logger.info(
                "MPRIS volume %d%% → %s (BlueZ default=%s, corrected via HCI source)",
                volume_pct,
                routed,
                default,
            )
        try:
            await client._send_subprocess_command({"cmd": "set_volume", "value": int(volume_pct)})
        except Exception as exc:
            logger.warning(
                "MPRIS volume->%d for %s failed: %s",
                volume_pct,
                routed,
                exc,
            )
            return False
        try:
            client._update_status({"volume": int(volume_pct)})
        except Exception as exc:
            logger.warning(
                "MPRIS volume->%d for %s: _update_status raised: %s",
                volume_pct,
                routed,
                exc,
            )
        return True

    return _cb


def _make_mpris_connected_hook(
    client: Any,
    mac: str,
) -> Callable[[], None]:
    """Build the on_connected closure passed to BluetoothManager.

    Fires once per false→true connect transition.  Constructs the per-device
    MprisPlayer, registers it in the process-wide registry (so the MA
    monitor reverse hook and the Claim Audio endpoint can find it), and
    schedules a best-effort D-Bus export onto the main asyncio loop.

    All failure modes are non-fatal: if dbus_fast is unavailable on the
    host, or the system bus rejects the export, the registry entry still
    serves the in-process surfaces and BluetoothManager state stays
    coherent.
    """

    def _hook() -> None:
        from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

        registry = get_registry()
        player = MprisPlayer(
            mac=mac,
            player_id=str(client.player_id),
            transport_callback=_build_mpris_transport_callback(client),
            volume_callback=_build_mpris_volume_callback(client),
            client=client,
        )
        registry.register(mac, player)

        loop = get_main_loop()
        if loop is None:
            logger.debug("MprisPlayer for %s registered without D-Bus export (no main loop)", mac)
            return

        async def _export() -> None:
            try:
                from dbus_fast import BusType  # type: ignore[import-untyped]
                from dbus_fast.aio import MessageBus  # type: ignore[import-untyped]
                from dbus_fast.signature import Variant  # type: ignore[import-untyped]
            except Exception as exc:
                logger.debug("dbus_fast unavailable, skipping MPRIS export for %s: %s", mac, exc)
                return
            path = _mpris_dbus_path(mac)
            adapter_path = _bluez_adapter_path(client.bt_manager) if client.bt_manager else None
            try:
                iface = _build_player_iface(player)
                bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
                bus.export(path, iface)
                player._dbus_bus = bus  # type: ignore[attr-defined]
                player._dbus_iface = iface  # type: ignore[attr-defined]
                player._dbus_adapter_path = adapter_path  # type: ignore[attr-defined]
                if adapter_path is None:
                    logger.warning(
                        "MPRIS player for %s exported at %s but adapter unknown — AVRCP forwarding inactive",
                        mac,
                        path,
                    )
                    return
                # Register with BlueZ Media1 so AVRCP passthrough commands
                # from the speaker (Play / Pause / Next / Previous / volume)
                # are routed to our exported player object.  Properties are
                # the minimum AVRCP advertisement; the speaker reads them
                # via ``org.bluez.MediaPlayer1`` from BlueZ.
                props = {
                    "PlaybackStatus": Variant("s", "Stopped"),
                    "LoopStatus": Variant("s", "None"),
                    "Rate": Variant("d", 1.0),
                    "Shuffle": Variant("b", False),
                    "Volume": Variant("d", 1.0),
                    "Position": Variant("x", 0),
                    "MinimumRate": Variant("d", 1.0),
                    "MaximumRate": Variant("d", 1.0),
                    "CanGoNext": Variant("b", True),
                    "CanGoPrevious": Variant("b", True),
                    "CanPlay": Variant("b", True),
                    "CanPause": Variant("b", True),
                    "CanSeek": Variant("b", False),
                    "CanControl": Variant("b", True),
                    "Metadata": Variant(
                        "a{sv}",
                        {
                            "xesam:title": Variant("s", "Sendspin Bridge"),
                            "xesam:artist": Variant("as", [""]),
                            "mpris:length": Variant("x", 0),
                        },
                    ),
                }
                introspect = await bus.introspect("org.bluez", adapter_path)
                proxy = bus.get_proxy_object("org.bluez", adapter_path, introspect)
                try:
                    media = proxy.get_interface("org.bluez.Media1")
                except Exception as exc:
                    logger.warning(
                        "MPRIS register: org.bluez.Media1 not on %s for %s: %s",
                        adapter_path,
                        mac,
                        exc,
                    )
                    return
                try:
                    await media.call_register_player(path, props)  # type: ignore[attr-defined]
                    player._dbus_registered = True  # type: ignore[attr-defined]
                    logger.info(
                        "MPRIS player registered with BlueZ Media1 for %s at %s on %s",
                        mac,
                        path,
                        adapter_path,
                    )
                except Exception as exc:
                    logger.warning(
                        "MPRIS register: Media1.RegisterPlayer(%s) on %s failed for %s: %s",
                        path,
                        adapter_path,
                        mac,
                        exc,
                    )

            except Exception as exc:
                logger.warning("MPRIS D-Bus export for %s failed: %s", mac, exc)

        try:
            asyncio.run_coroutine_threadsafe(_export(), loop)
        except Exception as exc:
            logger.debug("Failed to schedule MPRIS export for %s: %s", mac, exc)

    return _hook


def _make_mpris_disconnected_hook(mac: str) -> Callable[[], None]:
    """Build the on_disconnected closure for BluetoothManager.

    Removes the per-device MprisPlayer from the registry and tears down its
    D-Bus export on the main loop.  Safe to fire repeatedly — extras are
    silent no-ops.
    """

    def _hook() -> None:
        from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

        # Forget any tracker activity for this MAC so a stale recent-
        # activity record doesn't mis-route a subsequent button press
        # from another speaker after this device is gone.
        _get_avrcp_source_tracker().clear(mac)

        registry = get_registry()
        player = registry.unregister(mac)
        if player is None:
            return
        loop = get_main_loop()
        if loop is None:
            return
        bus = getattr(player, "_dbus_bus", None)
        path = _mpris_dbus_path(mac)
        if bus is None:
            return
        adapter_path = getattr(player, "_dbus_adapter_path", None)
        was_registered = getattr(player, "_dbus_registered", False)

        async def _unexport() -> None:
            # Tell BlueZ to drop the AVRCP forwarding registration
            # before we unexport the path — otherwise BlueZ keeps the
            # cached pointer and the next forwarded command races a
            # gone object.
            if was_registered and adapter_path is not None:
                try:
                    introspect = await bus.introspect("org.bluez", adapter_path)
                    proxy = bus.get_proxy_object("org.bluez", adapter_path, introspect)
                    media = proxy.get_interface("org.bluez.Media1")
                    await media.call_unregister_player(path)
                except Exception as exc:
                    logger.debug("MPRIS Media1.UnregisterPlayer(%s) failed: %s", path, exc)
            try:
                bus.unexport(path)
            except Exception as exc:
                logger.debug("MPRIS unexport for %s failed: %s", mac, exc)
            try:
                bus.disconnect()
            except Exception:
                pass

        try:
            asyncio.run_coroutine_threadsafe(_unexport(), loop)
        except Exception as exc:
            logger.debug("Failed to schedule MPRIS unexport for %s: %s", mac, exc)

    return _hook


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
    # Gate for the periodic mgmt-socket RSSI refresh.  When False the
    # ``on_rssi_update`` callback is left None so ``_rssi_refresh_tick``
    # short-circuits before acquiring the BT operation lock — the mgmt
    # round-trip is never issued.  Default False so existing tests and
    # any other callers that don't pass the flag stay quiet on RSSI.
    enable_rssi_badge: bool = False
    # ``BLUETOOTH_ADAPTERS`` config — used to look up per-adapter
    # ``device_class`` for the BluetoothManager pre-pair hook. The hook
    # fires a raw HCI Write_Class_Of_Device just before each outbound
    # pair attempt as a Samsung Q-series workaround (bluez/bluez#1025).
    # Default empty list = no override applied.
    bluetooth_adapters: list[dict[str, Any]] = field(default_factory=list)


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
    keep_alive_method = str(device.get("keep_alive_method") or "infrasound")

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
        keep_alive_method=keep_alive_method,
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

        def _on_rssi_update(rssi_dbm: int, _client: Any = client) -> None:
            """Forward a fresh RSSI reading from the BT manager's
            periodic refresh into the client's status pipeline.  The
            timestamp lets the UI grey out the chip when the value
            ages past 90 s (e.g. mgmt socket starts returning EBUSY)."""
            _client._update_status({"rssi_dbm": int(rssi_dbm), "rssi_at_ts": time.time()})

        # When the experimental flag is off, leave the callback
        # unwired so ``BluetoothManager._rssi_refresh_tick`` early-
        # returns before touching the BT operation lock or the mgmt
        # socket — zero overhead for the default-off path.
        rssi_callback = _on_rssi_update if context.enable_rssi_badge else None

        adapter_device_class_hex = _resolve_adapter_device_class(adapter, context.bluetooth_adapters)
        bt_mgr = context.bt_manager_factory(
            mac,
            adapter=adapter,
            device_name=player_name,
            host=client,
            prefer_sbc=context.prefer_sbc,
            check_interval=context.bt_check_interval,
            max_reconnect_fails=context.bt_max_reconnect_fails,
            on_sink_found=_on_sink_found,
            on_connected=_make_mpris_connected_hook(client, mac),
            on_disconnected=_make_mpris_disconnected_hook(mac),
            on_rssi_update=rssi_callback,
            churn_threshold=context.bt_churn_threshold,
            churn_window=context.bt_churn_window,
            enable_a2dp_dance=context.enable_a2dp_sink_recovery_dance,
            enable_pa_module_reload=context.enable_pa_module_reload,
            enable_adapter_auto_recovery=context.enable_adapter_auto_recovery,
            adapter_device_class_hex=adapter_device_class_hex,
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
