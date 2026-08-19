"""
BluetoothManager — manages Bluetooth speaker connections for sendspin-bt-bridge.

Handles pairing, connecting, disconnecting, audio sink configuration, and
automatic reconnection. Uses D-Bus (dbus-fast) for instant disconnect detection
via PropertiesChanged signals; falls back to bluetoothctl polling if unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import sendspin_bridge.bluetooth.audio as bt_audio
import sendspin_bridge.bluetooth.monitor as bt_monitor
from sendspin_bridge.bluetooth.bluez import Adapter, BluezControl, Outcome, get_bluez, set_bluez
from sendspin_bridge.bluetooth.dbus import (
    A2DP_SINK_UUID,
    AUDIO_SINK_UUIDS,
    _dbus_call_device_method,
    _dbus_connect_profile,
    _dbus_get_adapter_address,
    _dbus_get_device_property,
    _dbus_get_device_uuids,
    _dbus_wait_services_resolved,
)
from sendspin_bridge.bluetooth.pairing import PairOptions, PairSession, PairTimings
from sendspin_bridge.services.bluetooth import COMMON_BT_PAIR_PINS, bt_rssi_mgmt
from sendspin_bridge.services.bluetooth import bt_operation_lock as _bt_op_lock

# v2.63.0-rc.7 — RSSI background refresh restored via the kernel mgmt
# socket (``MGMT_OP_GET_CONN_INFO`` opcode 0x0031), wrapped in
# ``services/bt_rssi_mgmt.py`` using the ``btsocket`` library so we
# don't hand-roll the binary protocol.  rc.3 tried ``scan bredr``
# bursts (no events for connected peers); rc.5 tried ``bluetoothctl
# info`` (no RSSI line for connected peers); both deleted in rc.6.
# This is the only source on Linux that exposes RSSI for an
# established ACL link.  Refresh runs every ``_RSSI_REFRESH_INTERVAL_S``
# seconds via ``run_rssi_refresh_loop``, gated by the shared
# ``bt_operation_lock`` so a pair / scan / reconnect can never starve.


if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.bridge.bt_types import BluetoothManagerHost
    from sendspin_bridge.services.diagnostics.internal_events import DeviceEventType

UTC = timezone.utc

logger = logging.getLogger(__name__)

_bt_executor = ThreadPoolExecutor(max_workers=min(4, os.cpu_count() or 4), thread_name_prefix="bt-blocking")

# Timing constants for BT operations
_PAIRING_SCAN_DURATION = 12  # seconds to scan before pairing
_PAIRING_WAIT_DURATION = 10  # seconds to wait for pairing to complete
_MAX_RECONNECT_DELAY_S = 300.0  # max backoff for reconnect attempts (5 min)
_CONNECT_CHECK_RETRIES = 5  # status checks after connect before giving up
# Minimum time a device must stay auto-released before an external
# connect may reclaim management (issues #349/#350).  Keeps a
# churn-released speaker from flapping management on/off while it is
# still bouncing: each reclaim needs the speaker to hold a link past
# this window.
_AUTO_RECLAIM_QUIET_S = 60.0
# Cadence for live-RSSI refresh via kernel mgmt opcode 0x0031.
# 5 s feels live to the UI (chip updates while you walk past a
# speaker) without exceeding the controller's internal averaging
# window (~1 s), and on N speakers costs N/5 mgmt ops/sec — at most
# tenths of a percent of CPU even on a Pi.  Non-blocking lock acquire
# means a pair / scan / reconnect ladder simply skips the tick
# rather than queueing.  Aligned with a 30 s UI staleness threshold
# (6x safety margin so transient mgmt contention doesn't grey out
# every chip).
_RSSI_REFRESH_INTERVAL_S = 5.0
# After this many consecutive failed connect attempts where BlueZ has no current
# device object, force-remove the stale BlueZ entry so the next reconnect cycle
# can escalate to pair_device (KALLSUP-class loop, #162).
_PAIRED_UNKNOWN_THRESHOLD = 3


def install_dbus_hci_resolver(transport_factory=None) -> None:
    """Give the shared transport the D-Bus ``hciN`` → MAC lookup.

    The transport package is stdlib-only, so it cannot reach BlueZ over
    D-Bus itself; its own ladder is sysfs → injected resolver →
    positional ``bluetoothctl list`` index.  On kernels whose
    ``/sys/class/bluetooth/hciN`` carries no ``address`` file the sysfs
    step yields nothing, and the positional step reads BlueZ registration
    order — which is not kernel numbering, so ``select`` lands on the
    wrong controller (live: a power-cycle aimed at hci0 powered hci1).
    Wiring the resolver in at startup closes that gap for every caller.

    ``transport_factory`` is a test seam; production builds the default
    transport around the resolver.
    """
    factory = transport_factory or (lambda resolver: BluezControl(hci_resolver=resolver))
    set_bluez(factory(_dbus_get_adapter_address))


class BluetoothManager:
    """Manages the Bluetooth connection lifecycle for a single speaker.

    Responsibilities:
    - Pairing and connecting via ``bluetoothctl`` subprocesses
    - Auto-reconnecting on disconnect (exponential backoff, configurable interval)
    - Discovering the PulseAudio/PipeWire sink name for the connected device
    - Real-time disconnect detection via D-Bus (``dbus-fast``), with polling fallback
    - Churn isolation: auto-disabling a device after too many reconnects in a window

    Thread-safety: BT operations are dispatched via ``run_in_executor()`` to avoid
    blocking the asyncio event loop.  The ``BluetoothManagerHost`` protocol methods
    handle thread-safe status mutations internally.
    """

    def __init__(
        self,
        mac_address: str,
        adapter: str = "",
        device_name: str = "",
        host: BluetoothManagerHost | None = None,
        prefer_sbc: bool = False,
        check_interval: int = 10,
        max_reconnect_fails: int = 0,
        on_sink_found: Callable[[str, int | None], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        on_rssi_update: Callable[[int], None] | None = None,
        churn_threshold: int = 0,
        churn_window: float = 300.0,
        enable_a2dp_dance: bool = False,
        enable_pa_module_reload: bool = False,
        enable_adapter_auto_recovery: bool = False,
        adapter_device_class_hex: str = "",
    ):
        self.mac_address = mac_address
        self.adapter = adapter  # "hci0", "hci1", etc. — empty = use default
        self.device_name = device_name or mac_address
        self.host = host
        self.on_sink_found = on_sink_found
        # Connection-state transition callbacks (false→true / true→false).  The
        # owner (services/device_activation.py) wires these to per-device
        # MprisPlayer create/destroy so AVRCP buttons + speaker display follow
        # the link state without mpris_player having to poll BT state itself.
        # Fired exactly once per transition; exceptions are logged and
        # swallowed (must not destabilise the BT state machine).
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        # Fired once per successful periodic RSSI refresh tick with the
        # signed dBm value.  Owner (services/device_activation.py) wires
        # this to ``SendspinClient._update_status`` so the value flows
        # into ``DeviceStatus.rssi_dbm`` and out via SSE.  ``None`` from
        # the wrapper short-circuits before this fires — callback only
        # ever sees fresh ints.
        self.on_rssi_update = on_rssi_update
        self.prefer_sbc = prefer_sbc
        self.connected = False  # GIL-atomic bool; safe for cross-thread reads without lock
        # Serialises ``_apply_connected_state`` so the ``check ==``
        # current then write+fire sequence is atomic across the asyncio
        # D-Bus monitor thread and the BT executor thread.  Without
        # this both could observe the pre-transition state and fire
        # ``on_connected`` twice — duplicate MprisPlayer registrations.
        self._connected_state_lock = threading.Lock()
        self.last_check: float = 0
        self.check_interval = check_interval
        self.max_reconnect_fails = max_reconnect_fails
        # Monotonic timestamp of the last auto-release; gates the
        # auto-reclaim quiet period (issues #349/#350).  None = never
        # auto-released this run (a release persisted from a previous
        # run may reclaim immediately once the speaker connects).
        self._auto_released_at: float | None = None
        # Experimental sink-recovery flags (off by default; enabled per-bridge via config)
        self._enable_a2dp_dance = bool(enable_a2dp_dance)
        self._enable_pa_module_reload = bool(enable_pa_module_reload)
        # EXPERIMENTAL_ADAPTER_AUTO_RECOVERY — gates the last-ditch
        # bluetooth-auto-recovery ladder call in _handle_reconnect_failure.
        # Off by default because a USB reset briefly disconnects
        # every device on the same controller.
        self._enable_adapter_auto_recovery = bool(enable_adapter_auto_recovery)
        # Per-pair-attempt raw HCI Write_Class_Of_Device — re-applies the
        # configured CoD just before outbound Connect so the soundbar's
        # CoD filter (Samsung Q-series, bluez/bluez#1025) sees
        # Major=Computer at the moment it inspects, even if bluetoothd
        # power-cycled the adapter between startup and pair. Pre-parse
        # hex once so the pre-pair hook never emits a WARNING on every
        # pair attempt for a bad value that was already bad at init.
        # No-op when ``adapter_device_class_hex`` is empty.
        hex_raw = str(adapter_device_class_hex or "").strip()
        if hex_raw:
            try:
                from sendspin_bridge.services.bluetooth.bt_class_of_device import parse_class_hex as _parse_class_hex

                parsed = _parse_class_hex(hex_raw)
            except Exception:
                parsed = None
            if parsed is None:
                logger.warning(
                    "CoD override: device_class=%r is not a valid 6-hex-digit value — override disabled for this manager",
                    hex_raw,
                )
            self._cod_override_int: int | None = parsed
        else:
            self._cod_override_int = None

        # Resolve adapter name to MAC for reliable 'select' in bridged D-Bus setups.
        # In LXC containers, 'select hci0' fails ("Controller hci0 not available");
        # selecting by MAC address works because D-Bus objects use MACs, not hciN names.
        self._adapter_select = self._resolve_adapter_select(adapter) if adapter else ""
        self.management_enabled: bool = True  # False = released; monitor loop skips reconnect
        self._running: bool = True  # False = shutdown; monitor loops exit
        self.paired: bool | None = None
        self._connect_lock = threading.Lock()  # prevents concurrent connect_device() calls
        self._cancel_reconnect = threading.Event()
        self._standby_wake_event: asyncio.Event | None = None  # set by _wake_from_standby to unblock monitor
        self._reconnect_timestamps: list[float] = []  # monotonic timestamps of recent reconnects
        # Counts consecutive connect_device() failures where BlueZ has no
        # current device object (is_device_paired() returns None). After
        # _PAIRED_UNKNOWN_THRESHOLD consecutive observations we force-remove
        # the stale BlueZ entry so the next reconnect can escalate to
        # pair_device (KALLSUP-class loop, #162).
        self._paired_unknown_count = 0
        # v2.70.0-rc.2 (#260, #263) — true once this bridge session has
        # observed a successful Connected=True transition for this device.
        # Drives the never_paired signal: while False AND BlueZ has no
        # paired record, the device is in the "configured but never
        # paired" state that warrants a different banner + the
        # auto-disable threshold (#263). Re-enabling a previously
        # auto-disabled device resets this back to False so the new
        # session starts clean.
        self._has_ever_paired_since_start: bool = False
        # Remaining attempts at the A2DP recovery dance (disconnect→connect)
        # within the current reconnect cycle. Reset to 1 on a fresh cycle;
        # decremented when the dance runs. Guards against loops when the
        # upstream BlueZ 5.86 regression (bluez/bluez#1922) leaves no A2DP sink
        # exposed no matter how many times we retry.
        self._a2dp_dance_remaining = 1
        # Guard churn tracking because reconnect decisions can be touched from
        # multiple execution contexts (polling loop, D-Bus reconnect path).
        self._reconnect_lock = threading.Lock()
        self._CHURN_WINDOW = churn_window  # seconds; 0 threshold = disabled
        self._CHURN_THRESHOLD = churn_threshold  # 0 = disabled

        # Resolve effective adapter MAC for display (handles empty/default adapter case)
        if self._adapter_select:
            self.effective_adapter_mac = self._adapter_select
        else:
            self.effective_adapter_mac = self._detect_default_adapter_mac()

        self.adapter_hci_name = self._resolve_adapter_hci_name()
        # D-Bus device path: /org/bluez/<adapter>/dev_XX_XX_XX_XX_XX_XX
        _mac_dbus = self.mac_address.upper().replace(":", "_")
        self._dbus_device_path: str | None = None
        if self.adapter_hci_name:
            self._dbus_device_path = f"/org/bluez/{self.adapter_hci_name}/dev_{_mac_dbus}"
        else:
            logger.warning(
                "[%s] Could not resolve Bluetooth adapter to hciN for MAC %s (configured adapter=%s, effective adapter=%s); "
                "D-Bus monitoring is disabled and bluetoothctl polling fallback will be used",
                self.device_name,
                self.mac_address,
                self.adapter or "default",
                self.effective_adapter_mac or "unknown",
            )
        self.battery_level: int | None = None

    def shutdown(self) -> None:
        """Signal all monitor loops to exit."""
        self._running = False

    def cancel_reconnect(self) -> None:
        """Request cancellation of any in-flight reconnect attempt."""
        self.management_enabled = False
        self._cancel_reconnect.set()
        if self.host and self.host.get_status_value("reconnecting"):
            self.host.update_status({"reconnecting": False, "reconnect_attempt": 0})

    def allow_reconnect(self) -> None:
        """Clear reconnect cancellation so monitor loops may reconnect again."""
        self._cancel_reconnect.clear()
        self.management_enabled = True

    def signal_standby_wake(self) -> None:
        """Unblock bt_monitor's standby sleep so it reconnects immediately."""
        evt = self._standby_wake_event
        if evt is not None:
            evt.set()

    def _reconnect_cancelled(self) -> bool:
        return self._cancel_reconnect.is_set() or not self.management_enabled

    def _wait_with_cancel(self, duration: float, *, step: float = 0.2) -> bool:
        """Sleep in small chunks so release can cancel reconnect promptly."""
        deadline = time.monotonic() + duration
        while True:
            if self._reconnect_cancelled():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(step, remaining))

    def _abort_connect_if_cancelled(self) -> bool:
        """Abort a connect attempt and disconnect if release landed mid-flight."""
        if not self._reconnect_cancelled():
            return False
        logger.info("[%s] Reconnect cancelled — aborting active connect attempt", self.device_name)
        try:
            if self.is_device_connected():
                self.disconnect_device()
        except Exception as exc:
            logger.debug("[%s] Disconnect during reconnect cancellation failed: %s", self.device_name, exc)
        self._apply_connected_state(False)
        return True

    def _detect_default_adapter_mac(self) -> str:
        """Return the MAC of the default Bluetooth controller, or empty string."""
        info = get_bluez().show()
        if info.outcome is not Outcome.OK or not info.present:
            return ""
        return info.mac

    def _maybe_apply_cod_override_pre_pair(self) -> None:
        """Re-apply ``device_class`` to the resolved adapter just before pair.

        No-op unless a valid ``device_class`` was set at ``__init__``
        (pre-validated to an int there). Calls ``set_device_class``
        directly so failures are logged at WARNING only once — not on
        every pair attempt for an already-known bad hex value (which
        was already warned at init time).
        """
        if self._cod_override_int is None:
            return
        hci_name = self.adapter_hci_name or ""
        if not hci_name.startswith("hci"):
            logger.debug(
                "[%s] Pre-pair CoD: no resolved hciN for adapter — skipping",
                self.device_name,
            )
            return
        try:
            adapter_index = int(hci_name[3:])
        except ValueError:
            logger.debug(
                "[%s] Pre-pair CoD: malformed adapter hci name %r — skipping",
                self.device_name,
                hci_name,
            )
            return
        try:
            from sendspin_bridge.services.bluetooth.bt_class_of_device import set_device_class

            set_device_class(adapter_index, self._cod_override_int)
        except Exception as exc:
            logger.debug(
                "[%s] Pre-pair CoD apply failed (non-fatal): %s",
                self.device_name,
                exc,
            )

    def _resolve_adapter_hci_name(self) -> str:
        """Return hciN name for the effective adapter MAC (e.g. 'hci0'), or empty string."""
        if self.adapter.startswith("hci"):
            return self.adapter  # Already have it from config
        effective = (self.effective_adapter_mac or "").upper()
        if not effective:
            return ""
        # Prefer sysfs lookup — it maps MACs to hciN names without relying on
        # the ordering of 'bluetoothctl list' output which may not match hciN indices.
        mac_norm = effective.replace(":", "").lower()
        bt_sysfs = Path("/sys/class/bluetooth")
        try:
            for hci in sorted(bt_sysfs.iterdir()):
                addr_file = hci / "address"
                if addr_file.exists():
                    addr = addr_file.read_text().strip().replace(":", "").lower()
                    if addr == mac_norm:
                        return hci.name
        except Exception as exc:
            logger.debug("sysfs adapter lookup failed: %s", exc)
        # Fallback: count adapter positions in the controller list (fragile,
        # but last resort) — the transport/parser live in bluetooth.bluez.
        for idx, ref in enumerate(get_bluez().list_adapters()):
            if ref.mac.upper() == effective:
                return f"hci{idx}"
        return ""

    def _resolve_adapter_select(self, adapter: str) -> str:
        """Resolve hciN to adapter MAC address for bluetoothctl 'select'.
        Falls back to the original name if resolution fails."""
        if not adapter or not adapter.startswith("hci"):
            return adapter  # Already a MAC or empty string
        try:
            idx = int(adapter[3:])  # N from hciN
        except ValueError:
            return adapter
        # BlueZ's D-Bus object path for an adapter is always /org/bluez/<hciN>,
        # which matches the kernel hci index unambiguously. Prefer this over
        # counting lines in `bluetoothctl list`, whose ordering reflects
        # BlueZ's adapter-registration order and is NOT guaranteed to match
        # ascending hci-index order (e.g. after hot-plugging a second
        # adapter, the newly-plugged one can be listed first).
        dbus_addr = _dbus_get_adapter_address(adapter)
        if dbus_addr:
            logger.info("Resolved adapter %s → %s", adapter, dbus_addr)
            return dbus_addr.upper()
        macs = [ref.mac.upper() for ref in get_bluez().list_adapters()]
        if idx < len(macs):
            logger.info("Resolved adapter %s → %s", adapter, macs[idx])
            return macs[idx]
        return adapter  # Fall back to hciN name

    def check_bluetooth_available(self) -> bool:
        """Check if Bluetooth is available on the system"""
        try:
            if self.adapter:
                # Check the specific adapter (scoped via select)
                info = get_bluez().show(self._bluez_adapter())
                return info.outcome is Outcome.OK and info.present
            # Default: check for any controller
            info = get_bluez().show()
            return info.outcome is Outcome.OK and info.present and not info.no_default
        except Exception as e:  # defensive — the transport reports via Outcome
            logger.error("Bluetooth not available: %s", e)
            return False

    def _bluez_adapter(self) -> Adapter:
        """The configured adapter as a BluezControl scope directive."""
        if self._adapter_select:
            return Adapter.select(self._adapter_select)
        return Adapter.DEFAULT

    def is_device_paired(self) -> bool | None:
        """Check if device is paired via D-Bus; falls back to bluetoothctl.

        Returns ``None`` when BlueZ cannot currently resolve the device object.
        That state is common immediately after disconnect/restart for some
        speakers and must not be treated as a hard "not paired" signal.
        """
        val = _dbus_get_device_property(self._dbus_device_path, "Paired")
        if val is not None:
            return bool(val)
        info = get_bluez().device_info(self.mac_address, self._bluez_adapter())
        if info.paired is not None:
            return info.paired
        if info.outcome not in (Outcome.TIMEOUT, Outcome.UNAVAILABLE) and not info.present:
            logger.info(
                "[%s] Pairing state unknown: BlueZ has no current device object for %s",
                self.device_name,
                self.mac_address,
            )
        return None

    def is_device_connected(self) -> bool:
        """Check if device is currently connected via D-Bus; falls back to bluetoothctl."""
        try:
            val = _dbus_get_device_property(self._dbus_device_path, "Connected")
            if val is not None:
                is_connected = bool(val)
            else:
                # D-Bus unavailable — fall back to bluetoothctl
                info = get_bluez().device_info(self.mac_address, self._bluez_adapter())
                is_connected = info.outcome is Outcome.OK and info.connected is True

            if is_connected != self.connected:
                if is_connected:
                    logger.info("✓ BT device %s (%s) connected", self.device_name, self.mac_address)
                else:
                    logger.warning("✗ BT device %s (%s) disconnected", self.device_name, self.mac_address)
            self._apply_connected_state(is_connected)
            return self.connected
        except Exception as e:
            logger.warning("Error checking Bluetooth connection: %s", e)
            self._apply_connected_state(False)
            return False

    def pair_device(self) -> bool:
        """Pair with the Bluetooth device.

        The device MUST be in pairing/discoverable mode when this runs.
        The choreography — discovery, SSP auto-confirm, the PIN ladder,
        trust after a confirmed pair — is :class:`PairSession`, shared
        with the manual pair and reset-and-reconnect flows.  What belongs
        to this path is the device state around it: the Class-of-Device
        override, the post-pair audio-profile checks, and the failure
        fingerprint the recovery card reads.
        """
        mac = self.mac_address
        if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac):
            logger.error("Invalid MAC address format: %s", mac)
            return False

        logger.info("Pairing with %s...", mac)
        # Clear any pair-failure fingerprint left by a previous attempt
        # before we run the new one — otherwise a stale
        # ``samsung_cod_filter`` from last time can outlive a different
        # failure shape (or even a successful re-pair before the
        # next ``ok`` branch runs) and keep the recovery card lit.  Any
        # match this attempt produces will overwrite these back below.
        if self.host is not None:
            try:
                self.host.update_status(
                    {
                        "pair_failure_kind": None,
                        "pair_failure_adapter_mac": None,
                        "pair_failure_at": None,
                    }
                )
            except Exception as exc:
                logger.debug("[%s] pair_failure clear-on-entry failed: %s", self.device_name, exc)
        if self._reconnect_cancelled():
            logger.info("[%s] Pairing skipped because reconnect was cancelled", self.device_name)
            return False

        try:
            outcome = PairSession(
                get_bluez(),
                adapter=self._bluez_adapter(),
                mac=mac,
                options=PairOptions(
                    pins=COMMON_BT_PAIR_PINS,
                    timings=PairTimings(
                        scan_window_s=_PAIRING_SCAN_DURATION,
                        pair_wait_s=_PAIRING_WAIT_DURATION,
                    ),
                ),
                cancel=self._reconnect_cancelled,
                # Re-apply the per-adapter Class of Device override right
                # before the outbound BR/EDR Connect — Samsung Q-series
                # soundbars filter incoming connections by initiator CoD
                # (bluez/bluez#1025) and bluetoothd may have reset CoD on
                # the ``power on`` the session opens with.
                on_before_pair=self._maybe_apply_cod_override_pre_pair,
                label=self.device_name,
            ).run()
        except (OSError, subprocess.SubprocessError) as e:
            logger.error("Pair error: %s", e)
            return False

        if outcome.cancelled:
            return False

        ok = outcome.success
        self.paired = ok
        if ok:
            logger.info("Pairing successful")
            self._check_audio_profiles_after_pair()
            # Explicit A2DP Sink registration right after pair — narrows
            # the window where BlueZ 5.86's dual-role auto-negotiation
            # (bluez/bluez#1922) can settle on the wrong profile before
            # _connect_device_inner gets its turn. Best-effort: helper
            # logs AlreadyConnected silently and swallows errors, so a
            # failing hint here must not flip the pair result to False.
            try:
                self._force_a2dp_sink_profile()
            except Exception as exc:
                logger.debug("[%s] post-pair A2DP Sink hint raised: %s", self.device_name, exc)
            return True

        logger.warning("Pairing may have failed: %s", outcome.reason)
        # Fingerprint the failure for downstream operator guidance.
        # Right now only the Samsung Q-series Class-of-Device filter
        # quirk (bluez/bluez#1025) is recognised; ``classify_pair_failure``
        # returns ``None`` for everything else so the recovery card
        # only fires when we have a confident, actionable diagnosis.
        if outcome.failure_kind and self.host is not None:
            try:
                self.host.update_status(
                    {
                        "pair_failure_kind": outcome.failure_kind,
                        "pair_failure_adapter_mac": self.effective_adapter_mac or "",
                        "pair_failure_at": datetime.now(tz=UTC).isoformat(),
                    }
                )
            except Exception as exc:
                logger.debug("[%s] pair_failure_kind status push failed: %s", self.device_name, exc)
        return False

    def _check_audio_profiles_after_pair(self) -> None:
        """Log/surface a warning when a freshly-paired device advertises no audio profiles.

        We still keep the bond (some speakers refuse to be paired twice), but
        the operator benefits from an explicit status signal: trying to
        configure audio for a non-audio BLE-only device will always fail and
        the UI can show a targeted "this device doesn't advertise audio
        profiles" banner instead of a generic sink-not-found error.
        """
        try:
            uuids = {u.lower() for u in _dbus_get_device_uuids(self._dbus_device_path)}
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("[%s] Post-pair UUID read failed: %s", self.device_name, exc)
            return
        if not uuids:
            # D-Bus unavailable or device object gone — nothing actionable.
            return
        if uuids & AUDIO_SINK_UUIDS:
            return
        logger.warning(
            "[%s] Device advertises no audio-sink profiles; A2DP/HFP unavailable. UUIDs=%s",
            self.device_name,
            sorted(uuids),
        )
        if self.host is not None:
            try:
                self.host.update_status(
                    {
                        "last_error": "no_audio_profiles_advertised",
                        "last_error_at": datetime.now(tz=UTC).isoformat(),
                    }
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug("[%s] Post-pair status update failed: %s", self.device_name, exc)

    def trust_device(self) -> bool:
        """Trust the Bluetooth device"""
        return get_bluez().trust(self.mac_address, self._bluez_adapter()).outcome is Outcome.OK

    def configure_bluetooth_audio(self) -> bool:
        """Configure host's PipeWire/PulseAudio to use the Bluetooth device as audio output"""
        if self._reconnect_cancelled():
            return False
        return bt_audio.configure_bluetooth_audio(
            mac_address=self.mac_address,
            prefer_sbc=self.prefer_sbc,
            on_sink_found=self.on_sink_found,
            host=self.host,
            wait_with_cancel=self._wait_with_cancel,
            device_path=self._dbus_device_path,
            logger=logger,
        )

    def connect_device(self) -> bool:
        """Connect to the Bluetooth device"""
        if self._reconnect_cancelled():
            logger.info("[%s] Connect skipped because reconnect was cancelled", self.device_name)
            return False
        if not self._connect_lock.acquire(blocking=False):
            logger.debug("[%s] connect_device already in progress, waiting...", self.device_name)
            with self._connect_lock:  # wait for ongoing call to finish
                pass
            if self._abort_connect_if_cancelled():
                return False
            return self.is_device_connected()
        # Fresh top-level connect cycle gets one A2DP recovery dance credit.
        self._a2dp_dance_remaining = 1
        try:
            return self._connect_device_inner()
        finally:
            self._connect_lock.release()

    def _connect_device_inner(self) -> bool:
        """Connect to the Bluetooth device (called with _connect_lock held)"""
        if self._abort_connect_if_cancelled():
            return False
        # First check if already connected
        if self.is_device_connected():
            logger.info("Device already connected")
            self._apply_connected_state(True)
            self.paired = self.is_device_paired()
            self._paired_unknown_count = 0
            if self._abort_connect_if_cancelled():
                return False
            # Ensure audio is configured
            self.configure_bluetooth_audio()
            return not self._abort_connect_if_cancelled()

        logger.info("Connecting to %s...", self.mac_address)

        # Ensure paired and trusted (pair_device also runs trust)
        self.paired = self.is_device_paired()
        if self.paired is False:
            logger.info("Device not paired, attempting to pair...")
            if not self.pair_device():
                return False
            # #263 — pair_device() succeeded, so BlueZ now has a record for this
            # MAC even if the immediate connect-check below times out. Clear
            # never_paired here so the recovery banner doesn't stay on
            # "has never been paired" for the gap between pair-success and
            # the next observed Connected=True transition.
            self._clear_never_paired_evidence()
        elif self.paired is None:
            logger.info("Pairing state unknown, trying to reconnect before re-pairing")
        if self._abort_connect_if_cancelled():
            return False

        # Power on bluetooth
        get_bluez().power(True, self._bluez_adapter())
        if not self._wait_with_cancel(1):
            return False

        # Try to connect
        connect_result = get_bluez().connect(self.mac_address, self._bluez_adapter())
        if self._abort_connect_if_cancelled():
            return False

        # Wait for connection to establish
        for _i in range(_CONNECT_CHECK_RETRIES):
            if not self._wait_with_cancel(1):
                return False
            if self.is_device_connected():
                logger.info("Successfully connected to Bluetooth speaker")
                self._apply_connected_state(True)
                self.paired = True
                self._paired_unknown_count = 0
                if self._abort_connect_if_cancelled():
                    return False
                # Wait (up to 10s) for BlueZ to finish SDP resolution before
                # poking audio. Prevents all downstream profile/sink work from
                # racing an uninitialized Device1. Non-blocking: we proceed
                # even on timeout — this is a timing hint, not a hard gate.
                resolved = _dbus_wait_services_resolved(
                    self._dbus_device_path,
                    is_connected_check=self.is_device_connected,
                    wait_with_cancel=self._wait_with_cancel,
                    timeout=10.0,
                )
                if resolved is False:
                    if self._reconnect_cancelled():
                        return False
                    logger.warning(
                        "[%s] ServicesResolved did not reach True within 10s — proceeding anyway",
                        self.device_name,
                    )
                if self._abort_connect_if_cancelled():
                    return False
                # Workaround for bluez/bluez#1922 (5.86 dual-role A2DP regression):
                # after the generic Connect() succeeds, also ask BlueZ explicitly
                # for A2DP Sink. On an unaffected stack this is a cheap no-op
                # ("AlreadyConnected"); on the buggy path it can force the sink
                # profile to register where auto-select failed.
                self._force_a2dp_sink_profile()
                if self._abort_connect_if_cancelled():
                    return False
                # Configure audio routing. If no sink appears (same regression
                # class) try one disconnect→reconnect dance before surrendering
                # — some users report the profile registers on the 2nd connect.
                # The dance is experimental/opt-in because on some headless setups
                # it hurts more than it helps (see #174 / forum #78).
                sink_ok = self.configure_bluetooth_audio()
                if (
                    not sink_ok
                    and self._enable_a2dp_dance
                    and self._a2dp_dance_remaining > 0
                    and not self._abort_connect_if_cancelled()
                ):
                    self._a2dp_dance_remaining -= 1
                    if self._a2dp_recovery_dance():
                        sink_ok = self.configure_bluetooth_audio()
                # Last resort: reload module-bluez5-discover to force PA to
                # re-publish the bluez_card/bluez_sink hierarchy. Gated on its
                # own experimental flag because it briefly drops every other
                # active BT sink on the bridge.
                if (
                    not sink_ok
                    and self._enable_pa_module_reload
                    and not self._abort_connect_if_cancelled()
                    and self._reload_pa_bluez5_module()
                ):
                    sink_ok = self.configure_bluetooth_audio()
                return not self._abort_connect_if_cancelled()

        excerpt = connect_result.summary
        if excerpt:
            # #302 — surface the underlying BlueZ error (page-timeout,
            # br-connection-already-active, profile-unavailable, …) so the
            # operator can distinguish "speaker is off" from "speaker is
            # already paired with another host" without a follow-up bug
            # report.  Kept on a single line; the `is_actionable_warning…`
            # gate in services/diagnostics/log_analysis.py still treats
            # the canonical prefix as non-issue, so this doesn't change
            # how the line is bucketed in diagnostics.
            logger.warning(
                "Failed to connect (not connected after %d status checks): %s",
                _CONNECT_CHECK_RETRIES,
                excerpt,
            )
        else:
            logger.warning(
                "Failed to connect (not connected after %d status checks)",
                _CONNECT_CHECK_RETRIES,
            )
        if self.paired is None:
            self._paired_unknown_count += 1
            if self._paired_unknown_count >= _PAIRED_UNKNOWN_THRESHOLD:
                self._purge_stale_bluez_entry()
                self._paired_unknown_count = 0
        return False

    def _purge_stale_bluez_entry(self) -> None:
        """Force-remove a stale BlueZ device entry so the next reconnect can re-pair.

        Called from connect_device() after _PAIRED_UNKNOWN_THRESHOLD consecutive
        failed attempts where BlueZ has no current device object. Without this
        the monitor loop spins on `Failed to connect` forever (#162). Surfacing
        an actionable status lets the operator know the device must be in
        pairing mode for the next attempt to succeed.
        """
        logger.warning(
            "[%s] BlueZ has no record of %s after %d failed attempts — purging "
            "stale cache entry. Put device in pairing mode to re-pair.",
            self.device_name,
            self.mac_address,
            _PAIRED_UNKNOWN_THRESHOLD,
        )
        try:
            get_bluez().remove(self.mac_address, self._bluez_adapter())
        except Exception as e:  # defensive — the transport reports via Outcome
            logger.debug("[%s] Stale BlueZ purge failed (non-fatal): %s", self.device_name, e)
        if self.host:
            try:
                now_iso = datetime.now(tz=UTC).isoformat()
                # Copilot review on PR #290 — preserve the FIRST flip timestamp
                # across repeated purge cycles. Without this, the value churns
                # on every purge (~30 s cadence) and diagnostics can't tell
                # how long the device has been in the never-paired state.
                prior_since = self.host.get_status_value("never_paired_since")
                first_seen = prior_since if isinstance(prior_since, str) and prior_since else now_iso
                self.host.update_status(
                    {
                        "last_error": (
                            "Bluetooth speaker unreachable: BlueZ has no record of this device. "
                            "Put device in pairing mode and reconnect."
                        ),
                        "last_error_at": now_iso,
                        # #260, #263 — surface the "never paired in this
                        # bridge session" signal so the recovery banner can
                        # switch to "has never been paired" + Start pairing
                        # remediation, the device card can show the
                        # Start pairing button, and _handle_reconnect_failure
                        # can gate auto-disable on it.
                        "never_paired": True,
                        "never_paired_since": first_seen,
                    }
                )
            except Exception as exc:
                logger.debug("[%s] Failed to surface purge status: %s", self.device_name, exc)

    def disconnect_device(self) -> bool:
        """Disconnect from the Bluetooth device via D-Bus; falls back to bluetoothctl."""
        if _dbus_call_device_method(self._dbus_device_path, "Disconnect"):
            self._apply_connected_state(False)
            return True
        result = get_bluez().disconnect(self.mac_address, self._bluez_adapter())
        if result.outcome is Outcome.OK:
            self._apply_connected_state(False)
            return True
        return False

    def _apply_connected_state(self, value: bool) -> None:
        """Single setter for ``self.connected`` that bookkeeps callbacks.

        Replaces every ``self.connected = X`` / ``mgr.connected = X``
        site across the codebase as of v2.63.0-rc.5 — direct
        assignments bypassed ``_fire_connection_transition``, leaving
        ``on_connected`` (which wires per-device MprisPlayer
        registration) silent on the D-Bus PropertiesChanged path.
        Symptom: physical AVRCP buttons on connected speakers had no
        effect because no MprisPlayer was registered.

        Idempotent: a no-op when *value* matches the cached state, so
        the rapid-fire D-Bus polling cycles don't spam the callback.

        Thread-safe: the check + write are serialised under
        ``_connected_state_lock`` so the asyncio D-Bus monitor thread
        and the BT executor thread can't both pass the check, both
        write True, and both fire ``on_connected`` (would surface as
        duplicate MprisPlayer D-Bus exports).  The callback itself
        runs OUTSIDE the lock so a slow callback can't block a
        concurrent disconnect handler from updating state.
        """
        with self._connected_state_lock:
            if value == self.connected:
                return
            self.connected = value
        # #260, #263 — a successful Connected=True transition is the canonical
        # "this device exists in BlueZ and works" signal. Flip the session
        # flag and clear the never_paired status push so the recovery banner
        # returns to its normal state and the auto-disable gate stops firing.
        if value:
            self._clear_never_paired_evidence()
        self._fire_connection_transition(value)

    def _clear_never_paired_evidence(self) -> None:
        """Mark this session as having had a working BlueZ record and clear the
        never_paired UI signal (#260, #263).

        Called from two places:

        - ``_apply_connected_state(True)`` on the canonical Connected=True
          transition (most common path).
        - The pair-success branch in ``_connect_device_inner`` for the edge
          case where ``pair_device()`` writes a BlueZ record but the
          immediate connect-check loop times out without observing
          Connected=True. Without this clear, the never_paired recovery
          banner would persist as stale UI until the next reconnect cycle
          actually wins the connect check — a 30+ second window of
          misleading remediation guidance.
        """
        self._has_ever_paired_since_start = True
        if self.host is None:
            return
        try:
            self.host.update_status({"never_paired": False, "never_paired_since": None})
        except Exception as exc:
            logger.debug(
                "[%s] Failed to clear never_paired status: %s",
                self.device_name,
                exc,
            )

    def _fire_connection_transition(self, now_connected: bool) -> None:
        """Invoke on_connected / on_disconnected exactly once per transition.

        Wired by services/device_activation.py to MprisPlayer create/destroy.
        Callback exceptions must NOT destabilise the BT state machine — log
        and continue.  The split between on_connected and on_disconnected
        keeps each closure focused on a single direction.
        """
        cb = self.on_connected if now_connected else self.on_disconnected
        if cb is None:
            return
        try:
            cb()
        except Exception as exc:
            direction = "on_connected" if now_connected else "on_disconnected"
            logger.warning(
                "[%s] %s callback raised: %s",
                self.device_name,
                direction,
                exc,
            )

    def _a2dp_recovery_dance(self) -> bool:
        """Disconnect → wait → reconnect to nudge BlueZ into registering A2DP Sink.

        Workaround for bluez/bluez#1922 class of issues where the first connect
        after boot leaves the sink profile unregistered. Multiple upstream
        reports confirm a second connect often succeeds. Returns ``True`` when
        the device is re-established as connected; ``False`` otherwise.

        This method deliberately uses the low-level bluetoothctl and D-Bus
        helpers directly — calling ``connect_device`` would recurse and hit
        the ``_connect_lock`` we're already holding.
        """
        logger.warning(
            "[%s] No A2DP sink after connect — attempting disconnect/reconnect dance (bluez/bluez#1922 workaround)",
            self.device_name,
        )
        # Disconnect — prefer D-Bus, fall back to bluetoothctl.  The
        # _apply_connected_state setter handles the on_disconnected fire
        # so the MprisPlayer D-Bus path is torn down before reconnect
        # re-creates it; otherwise the dance would leave a dangling
        # object on the bus and the reconnect's on_connected fire would
        # clash with it.
        if not _dbus_call_device_method(self._dbus_device_path, "Disconnect"):
            get_bluez().disconnect(self.mac_address, self._bluez_adapter())
        self._apply_connected_state(False)
        # Short settle period — BlueZ needs a moment to tear down ACL state.
        if not self._wait_with_cancel(2):
            return False
        if self._abort_connect_if_cancelled():
            return False
        # Reconnect and re-issue the explicit A2DP Sink profile request.
        get_bluez().connect(self.mac_address, self._bluez_adapter())
        for _i in range(_CONNECT_CHECK_RETRIES):
            if not self._wait_with_cancel(1):
                return False
            if self.is_device_connected():
                self._apply_connected_state(True)
                self._force_a2dp_sink_profile()
                return True
        logger.warning("[%s] A2DP recovery dance did not restore the link", self.device_name)
        return False

    def _reload_pa_bluez5_module(self) -> bool:
        """Reload PulseAudio ``module-bluez5-discover`` as a last-resort sink recovery.

        Only invoked when the experimental flag is on and previous sink
        recovery attempts have failed. Globally throttled in
        ``services.pulse.areload_bluez5_discover_module``.
        """
        try:
            from sendspin_bridge.services.audio.pulse import reload_bluez5_discover_module
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("[%s] PA module reload import failed: %s", self.device_name, exc)
            return False
        try:
            reloaded = bool(reload_bluez5_discover_module())
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("[%s] PA module reload errored: %s", self.device_name, exc)
            return False
        if reloaded:
            logger.warning(
                "[%s] Reloaded module-bluez5-discover as sink-recovery last resort",
                self.device_name,
            )
        return reloaded

    def _force_a2dp_sink_profile(self) -> bool:
        """Explicitly tell BlueZ to connect the A2DP Sink profile for this device.

        Best-effort workaround for bluez/bluez#1922 (5.86 dual-role regression)
        where the generic Connect() leaves the sink profile unregistered.
        Return value is advisory — this is a hint to BlueZ, not a hard
        requirement for connect to be considered successful. Benign
        ``AlreadyConnected`` errors on a healthy stack stay silent; any other
        failure is logged at info level as a potential bluez/bluez#1922 signal.
        """
        ok, reason = _dbus_connect_profile(self._dbus_device_path, A2DP_SINK_UUID)
        if ok:
            logger.debug("[%s] A2DP Sink profile explicitly connected", self.device_name)
            return True
        # "AlreadyConnected" on a healthy stack is normal — don't alarm the log.
        if reason and "AlreadyConnected" not in reason:
            logger.info(
                "[%s] A2DP Sink ConnectProfile hint failed: %s (may indicate bluez/bluez#1922)",
                self.device_name,
                reason,
            )
        return False

    def _reconnect_delay(self, attempt: int) -> float:
        """Exponential backoff delay after a failed reconnect attempt.

        Attempts 1-3 use check_interval; doubles every attempt thereafter,
        capped at 5 minutes. Reduces BT radio activity (and audio disruption
        on other devices sharing the same adapter) as failure count grows.
        """
        return min(self.check_interval * (2 ** max(0, attempt - 3)), _MAX_RECONNECT_DELAY_S)

    def _record_reconnect(self) -> None:
        """Record a BT reconnect event for churn detection."""
        now = time.monotonic()
        with self._reconnect_lock:
            self._reconnect_timestamps.append(now)
            # Prune old entries while still under the same lock so callers
            # never observe a partially updated churn window.
            cutoff = now - self._CHURN_WINDOW
            self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]

    def _check_reconnect_churn(self) -> bool:
        """Auto-release device if too many reconnects in the time window.

        Returns True if management was released. Released when threshold <= 0.
        """
        if self._CHURN_THRESHOLD <= 0:
            return False
        now = time.monotonic()
        with self._reconnect_lock:
            cutoff = now - self._CHURN_WINDOW
            self._reconnect_timestamps = [t for t in self._reconnect_timestamps if t > cutoff]
            reconnect_count = len(self._reconnect_timestamps)
            if reconnect_count < self._CHURN_THRESHOLD:
                return False

        logger.warning(
            "[%s] BT churn detected: %d reconnects in %.0fs — auto-releasing to protect group",
            self.device_name,
            reconnect_count,
            self._CHURN_WINDOW,
        )
        self.management_enabled = False
        self._auto_released_at = time.monotonic()
        if self.host:
            self.host.bt_management_enabled = False
            self.host.update_status(
                {
                    "bt_management_enabled": False,
                    "bt_released_by": "auto",
                    "reconnecting": False,
                    "last_error": f"Auto-released: {reconnect_count} reconnects in {int(self._CHURN_WINDOW)}s",
                    "last_error_at": datetime.now(tz=UTC).isoformat(),
                }
            )
        try:
            from sendspin_bridge.services.bluetooth import persist_device_released

            persist_device_released(self.device_name, True, released_by="auto")
        except Exception as _e:
            logger.debug("persist_device_released failed: %s", _e)
        return True

    def _handle_reconnect_failure(self, attempt: int) -> bool:
        """Release BT management after too many consecutive failed reconnects.

        Returns True if management was released (caller should stop reconnecting).
        Side-effects: sets self.management_enabled=False, updates client status,
        calls persist_device_released().
        """
        # Also check time-windowed churn (many successful-then-failed cycles)
        if self._check_reconnect_churn():
            return True
        if self.max_reconnect_fails <= 0 or attempt < self.max_reconnect_fails:
            return False
        # Last-ditch adapter recovery before either auto-disable or auto-release.
        # Gated by the compatibility flag because a USB reset is disruptive.
        # Needs both the resolved adapter MAC and an hci index to hand to
        # the bluetooth-auto-recovery library.
        if self._enable_adapter_auto_recovery and self._try_adapter_auto_recovery():
            logger.info(
                "[%s] adapter recovery succeeded after %d failed reconnects — keeping management enabled",
                self.device_name,
                attempt,
            )
            return False
        # v2.70.0-rc.2 (#263) — Never-paired auto-disable.
        # After adapter recovery has had its shot, if BlueZ still has no
        # record of this device AND we never observed a successful
        # Connected=True transition in this bridge session, the device is
        # most likely misconfigured or out of range from the start. Stop
        # the reconnect storm by flipping enabled=False; the recovery banner
        # then surfaces a "Re-enable" action so the operator can retry
        # pairing on their schedule.
        if self.paired is None and not self._has_ever_paired_since_start:
            self._auto_disable_never_paired(attempt)
            return True
        logger.warning(
            "[%s] %d consecutive failed reconnects (threshold=%d) — auto-releasing BT management",
            self.device_name,
            attempt,
            self.max_reconnect_fails,
        )
        self.management_enabled = False
        self._auto_released_at = time.monotonic()
        if self.host:
            self.host.bt_management_enabled = False
            self.host.update_status(
                {
                    "bt_management_enabled": False,
                    "bt_released_by": "auto",
                    "reconnecting": False,
                    "last_error": f"Auto-released after {attempt} reconnect attempts",
                    "last_error_at": datetime.now(tz=UTC).isoformat(),
                }
            )
        try:
            from sendspin_bridge.services.bluetooth import persist_device_released

            persist_device_released(self.device_name, True, released_by="auto")
        except Exception as _e:
            logger.debug("persist_device_released failed: %s", _e)
        return True

    def maybe_auto_reclaim(self, connected: bool | None = None) -> bool:
        """Reclaim BT management after an auto-release once the speaker
        re-establishes the link on its own (issues #349/#350).

        The speaker initiating a connection is the churn-safe signal: a
        device stuck in a reconnect loop never presents a stable
        ``Connected=yes`` link.  Manual (operator) releases are never
        reclaimed automatically — only ``bt_released_by == "auto"``.
        ``_AUTO_RECLAIM_QUIET_S`` damps flapping after a churn release.

        ``connected`` overrides ``self.connected`` for the polling
        monitor, which passes its live poll result (the cached attribute
        is only maintained by the D-Bus PropertiesChanged path).

        Returns True when management was reclaimed; the caller is
        responsible for configuring audio and starting the player.
        """
        if self.management_enabled or not self._running:
            return False
        host = self.host
        if host is None or host.get_status_value("bt_released_by") != "auto":
            return False
        if connected is None:
            connected = self.connected
        if not connected:
            return False
        released_at = self._auto_released_at
        if released_at is not None and time.monotonic() - released_at < _AUTO_RECLAIM_QUIET_S:
            return False
        logger.info(
            "[%s] Speaker reconnected on its own after auto-release — reclaiming BT management",
            self.device_name,
        )
        with self._reconnect_lock:
            # Fresh churn window: the reconnects that led to the release
            # must not count against the reclaimed session.
            self._reconnect_timestamps = []
        self._auto_released_at = None
        self.allow_reconnect()
        host.bt_management_enabled = True
        host.update_status(
            {
                "bt_management_enabled": True,
                "bt_released_by": None,
                "reconnecting": False,
                "reconnect_attempt": 0,
                "last_error": None,
            }
        )
        from sendspin_bridge.services.diagnostics.internal_events import DeviceEventType

        self._publish_client_event(
            DeviceEventType.BT_MANAGEMENT_RECLAIMED,
            message="Speaker reconnected on its own — BT management reclaimed",
        )
        try:
            from sendspin_bridge.services.bluetooth import persist_device_released

            persist_device_released(self.device_name, False)
        except Exception as _e:
            logger.debug("persist_device_released failed: %s", _e)
        return True

    def _auto_disable_never_paired(self, attempt: int) -> None:
        """Flip enabled=False on a never-paired device that has exhausted
        BT_MAX_RECONNECT_FAILS attempts (#263).

        The flip is persisted via ``persist_device_enabled`` so config.json
        and the HA addon's options.json stay in sync — without this, an
        addon restart would silently re-enable the device and the loop
        would resume.

        We ALSO flip ``management_enabled`` to False and cancel the
        reconnect cycle. The polling/D-Bus monitor loops gate on
        ``mgr.management_enabled`` as the canonical "stop reconnecting"
        signal; without that flip the loop would tick on every
        ``check_interval`` and re-fire ``_handle_reconnect_failure``,
        re-emitting the warning and rewriting the same config bytes
        (Copilot review on PR #290). The recovery banner discriminates
        between this state and the regular auto-released state by
        checking ``enabled=False AND never_paired=True`` *before* the
        management-released branch, so the operator sees the
        Re-enable card rather than the Reclaim card.
        """
        logger.warning(
            "[%s] %d consecutive failed reconnects on a never-paired device "
            "(BlueZ has no record) — auto-disabling. Re-enable from the "
            "recovery banner after putting the speaker in pairing mode.",
            self.device_name,
            attempt,
        )
        now_iso = datetime.now(tz=UTC).isoformat()
        if self.host is not None:
            try:
                self.host.update_status(
                    {
                        "enabled": False,
                        "bt_management_enabled": False,
                        "reconnecting": False,
                        "last_error": (
                            f"Auto-disabled after {attempt} failed pairing attempts — this device has never been paired"
                        ),
                        "last_error_at": now_iso,
                    }
                )
                self.host.bt_management_enabled = False
            except Exception as exc:
                logger.debug("[%s] Failed to surface auto-disable status: %s", self.device_name, exc)
        # Stop the monitor loops from continuing to tick on this device.
        self.management_enabled = False
        try:
            self.cancel_reconnect()
        except Exception as exc:
            logger.debug("[%s] cancel_reconnect failed on auto-disable: %s", self.device_name, exc)
        try:
            from sendspin_bridge.services.bluetooth import persist_device_enabled

            persist_device_enabled(self.device_name, False)
        except Exception as exc:
            logger.debug("[%s] persist_device_enabled failed: %s", self.device_name, exc)

    def _try_adapter_auto_recovery(self) -> bool:
        """Run the bluetooth-auto-recovery ladder on this device's
        adapter. Returns True iff recovery succeeded. Short-circuits
        when the adapter was never resolved (no MAC, no hci index) —
        the library needs both to do its job.

        Uses the *resolved* adapter fields (``effective_adapter_mac``
        and ``adapter_hci_name``) rather than the raw config values,
        so devices using the default controller (where the user left
        ``adapter`` empty) are still covered — __init__ resolves both
        fields from sysfs / ``bluetoothctl list`` in that case.
        """
        adapter_mac = self.effective_adapter_mac
        hci_name = self.adapter_hci_name
        if not adapter_mac or not hci_name:
            return False
        m = re.match(r"^hci(\d+)$", hci_name)
        if not m:
            return False
        hci_index = int(m.group(1))
        try:
            from sendspin_bridge.services.bluetooth.adapter_recovery import recover_adapter_blocking
        except Exception as _e:
            logger.debug("[%s] adapter_recovery module unavailable: %s", self.device_name, _e)
            return False
        if self.host is not None:
            self.host.update_status(
                {
                    "adapter_recovery_last_at": datetime.now(tz=UTC).isoformat(),
                    "adapter_recovery_adapter": adapter_mac,
                    "adapter_recovery_stage": "running",
                    "adapter_recovery_result": None,
                    "adapter_recovery_failure_reason": None,
                }
            )
        try:
            recovered = bool(recover_adapter_blocking(hci_index=hci_index, adapter_mac=adapter_mac))
        except Exception as e:
            logger.warning("[%s] adapter auto-recovery raised: %s", self.device_name, e)
            if self.host is not None:
                self.host.update_status(
                    {
                        "adapter_recovery_stage": "completed",
                        "adapter_recovery_result": "error",
                        "adapter_recovery_failure_reason": str(e),
                    }
                )
            return False
        if self.host is not None:
            self.host.update_status(
                {
                    "adapter_recovery_stage": "completed",
                    "adapter_recovery_result": "success" if recovered else "failed",
                    "adapter_recovery_failure_reason": None if recovered else "Recovery backend reported failure.",
                }
            )
        return recovered

    def _publish_client_event(
        self,
        event_type: DeviceEventType,
        *,
        level: str = "info",
        message: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if not self.host:
            return
        import sendspin_bridge.bridge.state as _state

        _state.publish_device_event(
            getattr(self.host, "player_id", "") or self.device_name,
            event_type,
            level=level,
            message=message,
            details=details,
        )

    def _resolve_adapter_index(self) -> int:
        """Return the integer controller index the kernel mgmt socket uses.

        ``hci0`` → 0, ``hci3`` → 3.  Returns ``-1`` for any unresolved
        or non-conforming ``adapter_hci_name`` (LXC without
        /sys/class/bluetooth, partial recovery, garbage from the
        bluetoothctl-list fallback).  Callers must treat -1 as "skip
        the mgmt read" — addressing controller 0xFFFF would emit a
        confusing ENODEV in the logs every refresh tick.
        """
        if not self.adapter_hci_name:
            return -1
        m = re.match(r"^hci(\d+)$", self.adapter_hci_name)
        if not m:
            return -1
        return int(m.group(1))

    async def _rssi_refresh_tick(self) -> None:
        """One iteration of the periodic RSSI refresh.

        Short-circuits if the link is down or another BT operation is
        in flight; otherwise issues ``MGMT_OP_GET_CONN_INFO`` in the
        BT executor (the syscall blocks while bluetoothd round-trips
        to the controller) and forwards a fresh int to
        ``on_rssi_update``.  ``None`` from the wrapper means "no fresh
        value, keep last known" — never propagated upward.

        Exceptions from the wrapper or callback are caught here so a
        single bad tick can't tear down the long-lived refresh task.
        """
        if not self.connected:
            return
        # Skip the lock acquire + executor dispatch when the result has
        # nowhere to land or the wrapper would short-circuit anyway —
        # avoids brief contention with concurrent pair / scan attempts
        # every 30 s for nothing.
        if self.on_rssi_update is None:
            return
        adapter_index = self._resolve_adapter_index()
        if adapter_index < 0:
            return
        if not _bt_op_lock.try_acquire_bt_operation():
            return
        try:
            mac = self.mac_address
            loop = asyncio.get_running_loop()
            try:
                rssi = await loop.run_in_executor(_bt_executor, bt_rssi_mgmt.read_conn_info, adapter_index, mac)
            except Exception:
                logger.exception("[%s] RSSI mgmt read raised unexpectedly", self.device_name)
                rssi = None
        finally:
            _bt_op_lock.release_bt_operation()

        if rssi is None:
            return
        try:
            self.on_rssi_update(rssi)
        except Exception:
            logger.exception("[%s] on_rssi_update callback raised", self.device_name)

    async def run_rssi_refresh_loop(self, interval: float = _RSSI_REFRESH_INTERVAL_S) -> None:
        """Drive ``_rssi_refresh_tick`` on a fixed cadence until shutdown.

        Owner spawns this as a background asyncio task per active
        ``BluetoothManager`` from ``services/device_activation.py``.
        Exits cleanly when ``self._running`` flips false (shutdown
        path) or the task is cancelled.
        """
        try:
            while self._running:
                await asyncio.sleep(interval)
                if not self._running:
                    return
                await self._rssi_refresh_tick()
        except asyncio.CancelledError:
            raise

    async def monitor_and_reconnect(self):
        """Continuously monitor BT connection and reconnect if needed.

        Delegates to ``bt_monitor.monitor_and_reconnect()``.
        """
        await bt_monitor.monitor_and_reconnect(self)
