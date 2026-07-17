#!/usr/bin/env python3
"""
Sendspin Client with Bluetooth Management
Runs the sendspin CLI player with Bluetooth speaker management
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import socket
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import concurrent.futures

import sendspin_bridge.bridge.state as _state
from sendspin_bridge.bluetooth.dbus import _dbus_get_device_property, _dbus_get_media_transport_snapshot
from sendspin_bridge.bluetooth.manager import BluetoothManager
from sendspin_bridge.bluetooth.vendor_map import vendor_from_modalias
from sendspin_bridge.bridge.exceptions import IPCError
from sendspin_bridge.bridge.orchestrator import BridgeOrchestrator
from sendspin_bridge.config import (
    CONFIG_FILE,
    CONFIG_SCHEMA_VERSION,
    _player_id_from_mac,
    config_lock,
    get_runtime_version,
    save_device_static_delay,
    save_device_volume,
)
from sendspin_bridge.services.audio.latency_calibration import build_calibration_pcm
from sendspin_bridge.services.audio.latency_recommendation import build_latency_recommendation
from sendspin_bridge.services.audio.playback_health import PlaybackHealthMonitor
from sendspin_bridge.services.diagnostics.internal_events import DeviceEventType
from sendspin_bridge.services.diagnostics.sendspin_port_probe import probe_sendspin_port
from sendspin_bridge.services.infrastructure.config_validation import (
    validate_sendspin_server_format,
)
from sendspin_bridge.services.infrastructure.port_bind_probe import DEFAULT_MAX_ATTEMPTS, find_available_bind_port
from sendspin_bridge.services.ipc.ipc_protocol import (
    with_protocol_version,
)
from sendspin_bridge.services.ipc.subprocess_command import SubprocessCommandService
from sendspin_bridge.services.ipc.subprocess_ipc import SubprocessIpcService
from sendspin_bridge.services.ipc.subprocess_stderr import SubprocessStderrService
from sendspin_bridge.services.ipc.subprocess_stop import SubprocessStopService
from sendspin_bridge.services.lifecycle.status_event_builder import StatusEventBuilder
from sendspin_bridge.services.music_assistant.ma_artwork import build_artwork_proxy_url

UTC = timezone.utc

# Maximum consecutive bind failures (all ports in scan range taken) before the
# restart loop halts. At that point the collision is no longer transient and
# further attempts only spam logs — user must intervene (`lsof -i :<port>`).
_MAX_BIND_FAILURES = 5

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# In-memory ring buffer so the web UI can read logs even when docker CLI
# is unavailable (e.g. inside the container itself).
from collections import deque as _deque  # noqa: E402


class _RingLogHandler(logging.Handler):
    """Keeps the last *maxlen* formatted log records in a deque, and
    fans out each new record to subscribed queues for the live log
    stream WebSocket (``/api/logs/stream``).

    The ring + fan-out design lets the WS endpoint deliver an immediate
    snapshot frame on connect (``snapshot()``) followed by per-emit
    append frames pushed through the subscribed queue — replacing the
    pre-rc.3 polling cadence (``GET /api/logs?lines=150`` every 2 s).
    """

    def __init__(self, maxlen: int = 2000) -> None:
        super().__init__()
        self.records: _deque[str] = _deque(maxlen=maxlen)
        # Subscribers are anything with ``put_nowait(line: str)`` —
        # plain stdlib queue.Queue, asyncio.Queue.put_nowait, etc.
        # A single lock guards both ``records`` and ``_subscribers``
        # because emit() touches both atomically and snapshot() must
        # see a consistent ring view (deque iteration under concurrent
        # append from another thread can otherwise raise
        # ``RuntimeError: deque mutated during iteration``).  A separate
        # ``subscribe_with_snapshot`` exposes an atomic
        # take-snapshot-then-register pair so the WS log stream never
        # drops a line in the gap between snapshot and subscribe.
        self._subscribers: list[Any] = []
        self._lock = threading.Lock()

    # Back-compat alias for tests / callers that referenced the old
    # subscribers-only lock; both the records deque and the subscriber
    # list now share the single ``self._lock``.
    @property
    def _subscribers_lock(self) -> threading.Lock:
        return self._lock

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock:
            self.records.append(line)
            subscribers = list(self._subscribers)
        # Fan-out happens outside the lock so one slow subscriber can't
        # block the next emit() call (the lock would otherwise be held
        # for the whole ``put_nowait`` round-trip).  Failures in one
        # subscriber must not block others — a stuck WS client must not
        # stall the global log stream.
        for sub in subscribers:
            try:
                sub.put_nowait(line)
            except Exception:
                pass

    def subscribe(self, q: Any) -> None:
        """Register a queue-like object to receive every subsequent emit."""
        with self._lock:
            self._subscribers.append(q)

    def unsubscribe(self, q: Any) -> None:
        """Drop *q* from the subscriber list; silent no-op if absent."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def subscribe_with_snapshot(self, q: Any | None = None) -> tuple[Any, list[str]]:
        """Atomically register a queue + capture the current ring snapshot.

        The WS log stream relies on this to avoid dropping lines that
        would otherwise land in the gap between ``snapshot()`` and
        ``subscribe()``.  When *q* is ``None``, a fresh stdlib
        ``queue.Queue`` is created and returned alongside the snapshot;
        callers that need a bounded queue should construct it
        themselves and pass it in.
        """
        import queue as _queue

        target = q if q is not None else _queue.Queue()
        with self._lock:
            snapshot = list(self.records)
            self._subscribers.append(target)
        return target, snapshot

    def snapshot(self) -> list[str]:
        """Return a list copy of the current ring contents.  The WS
        endpoint sends this on connect so subscribers see historical
        context before the first new line lands."""
        with self._lock:
            return list(self.records)


_ring_log_handler = _RingLogHandler(maxlen=2000)
_ring_log_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logging.getLogger().addHandler(_ring_log_handler)
_MA_RECONNECT_TIMEOUT_S = 15.0

_IPC_ALLOWED_KEYS = frozenset(
    {
        "playing",
        "connected",
        "server_connected",
        "server_connected_at",
        "connected_server_url",
        "group_id",
        "group_name",
        "audio_format",
        "audio_streaming",
        "volume",
        "muted",
        "sink_muted",
        "reanchoring",
        "reanchor_count",
        "reanchor_count_session",
        "reanchor_count_5m",
        "reanchor_count_30m",
        "last_sync_error_ms",
        "last_reanchor_at",
        "current_track",
        "current_artist",
        "current_album",
        "current_album_artist",
        "artwork_url",
        "track_year",
        "track_number",
        "shuffle",
        "repeat_mode",
        "playback_speed",
        "supported_commands",
        "group_volume",
        "group_muted",
        "state_changed_at",
        "last_error",
        "last_error_at",
        "track_progress_ms",
        "track_duration_ms",
        "ma_reconnecting",
        "static_delay_ms",
        "timing_metrics_available",
        "backend_output_latency_ms",
        "buffered_audio_ms",
        "playback_position_us",
        "dac_samples_recorded",
        "playback_sync_error_ms",
        "clock_synchronized",
        "clock_offset_ms",
        "clock_uncertainty_ms",
        "timing_sampled_at",
        "required_lead_time_ms",
        "min_buffer_ms",
    }
)

_IPC_LOG_METHODS = {
    "debug": logger.debug,
    "info": logger.info,
    "warning": logger.warning,
    "error": logger.error,
    "critical": logger.critical,
}

# ── Keepalive infrasound buffer ───────────────────────────────────────────

_INFRASOUND_RATE = 44100
_INFRASOUND_FREQ = 2  # Hz — below human hearing (20 Hz)
_INFRASOUND_AMP = 100  # approx -50 dB (100 / 32767), inaudible
_INFRASOUND_DUR = 1.0  # seconds
_INFRASOUND_CHANNELS = 2  # stereo

_infrasound_cache: bytes | None = None


async def _probe_port_if_default(host: str, default_port: int) -> int | None:
    """Probe the Sendspin port on *host*, falling back to candidate ports if it's closed.

    Returns the first responding port, or None if nothing responds.  The
    function name reflects its original "only ran when port == default"
    semantics; the always-probe migration in the #291 follow-up kept the
    name to avoid noisy renames across call sites.
    """
    try:
        return await probe_sendspin_port(host, default_port)
    except Exception:
        logger.debug("Port probe for %s failed, using default port %d", host, default_port)
        return None


def _generate_infrasound_burst() -> bytes:
    """Generate 1 s of 2 Hz stereo infrasound at -50 dB.

    Returns a cached PCM buffer (s16le, 44100 Hz, stereo) suitable for
    ``paplay --raw``.  The 2 Hz frequency is below the human hearing
    threshold; the -50 dB amplitude (100/32767) is inaudible, but the
    non-zero PCM data keeps the A2DP transport active -- preventing
    speakers from auto-sleeping.
    """
    global _infrasound_cache
    if _infrasound_cache is not None:
        return _infrasound_cache
    n = int(_INFRASOUND_RATE * _INFRASOUND_DUR)
    buf = bytearray(n * _INFRASOUND_CHANNELS * 2)
    two_pi_f_over_r = 2.0 * math.pi * _INFRASOUND_FREQ / _INFRASOUND_RATE
    for i in range(n):
        val = int(_INFRASOUND_AMP * math.sin(two_pi_f_over_r * i))
        struct.pack_into("<hh", buf, i * 4, val, val)
    _infrasound_cache = bytes(buf)
    return _infrasound_cache


_KEEPALIVE_METHODS = ("infrasound", "silence", "none")


def _generate_keepalive_buffer(method: str) -> bytes:
    """Return the PCM payload for the configured ``keep_alive_method``.

    * ``infrasound`` (default) — the existing 2 Hz subsonic stereo burst.
    * ``silence``  — a same-length all-zero buffer; some speaker firmwares
      treat any non-empty A2DP frame as activity but misbehave on the
      periodic 2 Hz tone (rare; reported on a few older Yandex / JBL units).
    * ``none``     — empty bytes.  ``_send_keepalive_burst`` skips the
      burst entirely; the speaker is allowed to time out naturally.

    Unknown values silently fall back to ``infrasound`` so a typo in the
    per-device config can't disable keepalive without an audible signal.
    """
    if method == "silence":
        ref = _generate_infrasound_burst()
        return b"\x00" * len(ref)
    if method == "none":
        return b""
    return _generate_infrasound_burst()


@dataclass
class SpawnRecord:
    """One Sendspin daemon subprocess spawn — entry created on spawn, fields filled on exit.

    Captured per device to expose *why* a daemon exited (exit code, signal,
    lifetime, last stderr lines) so issue-#291-style debugging doesn't require
    correlating windows of MA logs and bridge logs by hand.
    """

    pid: int
    spawn_at: datetime
    exit_at: datetime | None = None
    exit_code: int | None = None
    signal: int | None = None
    lifetime_s: float | None = None
    stderr_tail: list[str] = field(default_factory=list)
    # ``unexpected`` distinguishes daemon deaths driven by ``stop_sendspin``
    # (graceful release / shutdown — False) from deaths that surprise the
    # parent (True).  Only unexpected deaths drive ``last_error`` updates and
    # repeating-interval pattern detection.
    unexpected: bool = True


@dataclass
class DeviceStatus:
    """Typed status container for a single Sendspin device.

    **Why both dataclass AND dict interface?**

    The subprocess (``daemon_process.py``) emits status updates as JSON dicts,
    and Flask routes historically read ``status["key"]`` everywhere.  Switching
    all callers to attribute access at once was impractical, so this class
    provides a transitional dict-compatible interface (``__getitem__``,
    ``get``, ``update``, ``copy``, ``__contains__``) on top of typed fields.

    Benefits of the hybrid approach:
    - **Type safety at definition time:** typos in field names are caught by
      IDE / mypy, unlike bare dicts.
    - **Controlled mutation:** only declared fields can be set — prevents
      unbounded growth from unexpected subprocess keys.
    - **Backward compat:** existing ``status["key"]`` / ``status.get(...)``
      callers work without modification.

    Long-term, callers should migrate to attribute access (``status.playing``).
    """

    connected: bool = False
    playing: bool = False
    bluetooth_available: bool = False
    bluetooth_connected: bool = False
    bluetooth_connected_at: str | None = None
    server_connected: bool = False
    server_connected_at: str | None = None
    current_track: str | None = None
    current_artist: str | None = None
    volume: int = 100
    muted: bool = False
    sink_muted: bool = False
    audio_format: str | None = None
    bt_transport_path: str | None = None
    bt_transport_state: str | None = None
    bt_codec_id: int | None = None
    bt_codec_name: str | None = None
    bt_reported_delay_ms: float | None = None
    bt_delay_reporting_supported: bool = False
    bt_delay_updated_at: str | None = None
    timing_metrics_available: bool = False
    backend_output_latency_ms: float | None = None
    buffered_audio_ms: float | None = None
    playback_position_us: int | None = None
    dac_samples_recorded: int = 0
    playback_sync_error_ms: float | None = None
    clock_synchronized: bool = False
    clock_offset_ms: float | None = None
    clock_uncertainty_ms: float | None = None
    timing_sampled_at: str | None = None
    required_lead_time_ms: int = 250
    min_buffer_ms: int = 250
    static_delay_ms: float = 0.0
    static_delay_source: str = "default"
    static_delay_calibrated_at: str | None = None
    static_delay_codec: str | None = None
    suggested_static_delay_ms: int | None = None
    latency_suggestion_source: str = "unavailable"
    latency_suggestion_confidence: str = "none"
    latency_suggestion_explanation: str = ""
    latency_suggestion_revision: str | None = None
    latency_double_count_risk: bool = False
    reanchor_count: int = 0
    reanchor_count_session: int = 0
    reanchor_count_5m: int = 0
    reanchor_count_30m: int = 0
    last_sync_error_ms: float | None = None
    last_reanchor_at: float | None = None
    reanchoring: bool = False
    audio_streaming: bool = False
    state_changed_at: str | None = None
    ip_address: str = ""
    hostname: str = ""
    last_error: str | None = None
    last_error_at: str | None = None
    uptime_start: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    reconnecting: bool = False
    ma_reconnecting: bool = False
    reconnect_attempt: int = 0
    buffering: bool = False
    stopping: bool = False
    bt_management_enabled: bool = True
    bt_released_by: str | None = None
    battery_level: int | None = None
    group_name: str | None = None
    group_id: str | None = None
    connected_server_url: str | None = None
    track_progress_ms: int | None = None
    track_duration_ms: int | None = None
    playback_speed: int | None = None
    current_album: str | None = None
    current_album_artist: str | None = None
    artwork_url: str | None = None
    track_year: int | None = None
    track_number: int | None = None
    shuffle: bool | None = None
    repeat_mode: str | None = None
    supported_commands: list | None = None
    group_volume: int | None = None
    group_muted: bool | None = None
    bt_standby: bool = False
    bt_standby_since: str | None = None
    bt_waking: bool = False
    bt_power_save: bool = False
    # v2.63.0-rc.2 — last observed BT signal strength (dBm) + capture
    # timestamp.  Currently populated only via the user-triggered scan
    # path (``routes/api_bt.py:_extract_rssi_from_info`` /
    # ``_parse_scan_output``) — the periodic background refresh task that
    # keeps these warm for connected device cards is deferred to
    # v2.63.0-rc.3.  The ``static/app.js:_renderRssiChip`` helper already
    # honours ``rssi_at_ts`` staleness (>90 s → grey) so wiring the
    # background path later is a drop-in: keep these fields, no UI
    # changes required.  ``None`` for both means no reading has been
    # captured yet (or the device is fundamentally headless to RSSI on
    # this adapter / BlueZ build).
    rssi_dbm: int | None = None
    rssi_at_ts: float | None = None
    idle_mode: str = "default"
    port_collision: bool = False
    active_listen_port: int | None = None
    reloading: bool = False
    listen_port: int | None = None
    # v2.65.1 — last-pair classifier output for the operator-guidance
    # builder.  ``None`` = no recognised fingerprint (most failures);
    # ``"samsung_cod_filter"`` = the Q-series Class-of-Device filter
    # quirk (bluez/bluez#1025) — see services/bluetooth.classify_pair_failure.
    # The adapter MAC is captured alongside so the recovery card can name
    # the controller the operator should adjust in Settings → Bluetooth.
    pair_failure_kind: str | None = None
    pair_failure_adapter_mac: str | None = None
    pair_failure_at: str | None = None

    # v2.70.0-rc.2 — "never paired since bridge start" signal driving the
    # recovery banner branch (#260), the Start pairing button (#261), the
    # bug-report classifier (#262), and the auto-disable threshold (#263).
    # BluetoothManager flips this to True from `_purge_stale_bluez_entry`
    # after _PAIRED_UNKNOWN_THRESHOLD consecutive observations of
    # `paired is None`. A successful pair or first observed Connected=True
    # clears it back to False. ``never_paired_since`` carries the ISO
    # timestamp of the first flip for diagnostics.
    never_paired: bool = False
    never_paired_since: str | None = None

    # Mean lifetime (seconds) when the last 3 unexpected daemon exits landed
    # within ±1s of each other.  Populated by SendspinClient after each death.
    # ``None`` clears the corresponding operator-guidance banner.  This is the
    # primary signal for the issue #291 class of bug — daemon connects to a
    # broken endpoint, library times out at a fixed interval, exits silently
    # → consistent N-second loop visible here instead of being hidden in logs.
    daemon_recurring_lifetime_s: float | None = None

    # ── Dict-compatible interface ──────────────────────────────────────────

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __setitem__(self, key: str, value) -> None:
        if key in self._field_names:
            setattr(self, key, value)
        else:
            logger.debug("DeviceStatus: unknown key ignored: %s", key)

    _field_names: frozenset = field(default=frozenset(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Cache field names once for fast __contains__ / __setitem__ lookups
        object.__setattr__(self, "_field_names", frozenset(f.name for f in fields(self) if f.name != "_field_names"))

    def __contains__(self, key: object) -> bool:
        return key in self._field_names if isinstance(key, str) else False

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if k in self._field_names:
                setattr(self, k, v)
            else:
                logger.debug("DeviceStatus: unknown key ignored: %s", k)

    def copy(self) -> dict[str, object]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_field_names"}


def _normalize_device_mac(mac: object) -> str:
    """Return a canonical MAC string for config/runtime comparisons."""
    return mac.strip().upper() if isinstance(mac, str) else ""


def _filter_duplicate_bluetooth_devices(devices: list[dict]) -> list[dict]:
    """Keep the first occurrence of each configured MAC and log duplicates loudly."""
    unique_devices: list[dict] = []
    first_player_by_mac: dict[str, str] = {}

    for device in devices:
        normalized = dict(device)
        mac = _normalize_device_mac(normalized.get("mac"))
        if mac:
            normalized["mac"] = mac
            if mac in first_player_by_mac:
                logger.error(
                    "Duplicate Bluetooth MAC %s for player '%s' — skipping duplicate; first occurrence belongs to '%s'",
                    mac,
                    normalized.get("player_name") or mac,
                    first_player_by_mac[mac],
                )
                continue
            first_player_by_mac[mac] = normalized.get("player_name") or mac
        unique_devices.append(normalized)

    return unique_devices


def _load_saved_device_volume(mac: str) -> int | None:
    """Read LAST_VOLUMES from config so the UI has a volume before BT reconnects."""
    try:
        with config_lock, open(CONFIG_FILE) as config_file:
            saved_config = json.load(config_file)
        saved_volume = saved_config.get("LAST_VOLUMES", {}).get(mac)
        if isinstance(saved_volume, int) and 0 <= saved_volume <= 100:
            return saved_volume
    except Exception as exc:
        logger.debug("pre-fill saved volume failed: %s", exc)
    return None


class SendspinClient:
    """Per-device orchestrator for a single Bluetooth speaker.

    Manages the full lifecycle of a Sendspin subprocess: spawning it with the
    correct ``PULSE_SINK`` environment variable, reading JSON-line status from
    its stdout, sending commands (volume, stop, reconnect) via its stdin, and
    tearing it down gracefully on disconnect or shutdown.

    Thread-safety: status mutations go through ``_update_status()`` which
    acquires ``_status_lock``.  Flask routes, the asyncio event loop, and
    D-Bus callbacks all read/write status through this single gate.
    """

    def __init__(
        self,
        player_name: str,
        server_host: str,
        server_port: int,
        bt_manager: BluetoothManager | None = None,
        listen_port: int = 8928,
        static_delay_ms: float | None = None,
        listen_host: str | None = None,
        effective_bridge: str = "",
        preferred_format: str | None = "flac:44100:16:2",
        keepalive_enabled: bool = False,
        keepalive_interval: int = 30,
        idle_disconnect_minutes: int = 0,
        idle_mode: str = "default",
        power_save_delay_minutes: int = 1,
        keep_alive_method: str = "infrasound",
        required_lead_time_ms: int = 250,
        min_buffer_ms: int = 250,
        static_delay_source: str = "default",
        static_delay_calibrated_at: str | None = None,
        static_delay_codec: str | None = None,
    ):
        self.player_name = player_name
        self.server_host = server_host
        self.server_port = server_port
        self.bt_manager = bt_manager
        self.listen_port = listen_port  # port sendspin daemon listens on
        self.listen_host = listen_host  # explicit IP for WebSocket URL display (None = auto-detect)
        self.static_delay_ms = static_delay_ms  # per-device delay override (None = use env var)
        self.preferred_format = preferred_format  # preferred audio format string (e.g. "flac:44100:16:2")
        self._effective_bridge = effective_bridge  # bridge instance label for MA device info
        self.idle_mode = idle_mode  # default | power_save | auto_disconnect | keep_alive
        self.keepalive_enabled = idle_mode == "keep_alive" or keepalive_enabled
        self.keepalive_interval = max(30, keepalive_interval)  # seconds between keepalive bursts
        # v2.63.0-rc.2 — payload selector: "infrasound" (default) | "silence" | "none"
        self.keep_alive_method = keep_alive_method if keep_alive_method in _KEEPALIVE_METHODS else "infrasound"
        self.idle_disconnect_minutes = idle_disconnect_minutes  # 0 = disabled
        self.power_save_delay_minutes = max(0, power_save_delay_minutes)
        self.required_lead_time_ms = max(0, min(30000, int(required_lead_time_ms)))
        self.min_buffer_ms = max(0, min(30000, int(min_buffer_ms)))

        # Status tracking
        self.status = DeviceStatus(
            bluetooth_available=bt_manager.check_bluetooth_available() if bt_manager else False,
            ip_address=listen_host or self.get_ip_address(),
            hostname=socket.gethostname(),
            idle_mode=idle_mode,
            required_lead_time_ms=self.required_lead_time_ms,
            min_buffer_ms=self.min_buffer_ms,
            static_delay_ms=float(static_delay_ms or 0.0),
            static_delay_source=static_delay_source,
            static_delay_calibrated_at=static_delay_calibrated_at,
            static_delay_codec=static_delay_codec,
        )

        self._status_lock = threading.Lock()
        self.running = False
        # Compute player_id: stable UUID5 from MAC (preferred) or player name
        _mac = bt_manager.mac_address if bt_manager else None
        safe_id = "".join(c if c.isalnum() or c == "-" else "-" for c in player_name.lower()).strip("-")
        self._safe_id = safe_id
        self.player_id: str = (
            _player_id_from_mac(_mac)
            if _mac
            else str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_DNS, player_name.lower()))
        )
        self.bt_management_enabled: bool = True
        self.bluetooth_sink_name: str | None = None  # Store Bluetooth sink name for volume sync
        self.connected_server_url: str = ""  # actual resolved ws:// URL (populated after connect)
        self._seen_ipc_protocol_warnings: set[str] = set()
        self._daemon_proc: asyncio.subprocess.Process | None = None
        self._daemon_task: asyncio.Task | None = None  # stdout reader task
        self._stderr_task: asyncio.Task | None = None  # stderr reader task
        self._monitor_task: asyncio.Task | None = None
        self._ma_reconnect_task: asyncio.Task | None = None
        self._restart_delay: float = 1.0  # exponential backoff for unexpected daemon restarts
        self._start_sendspin_lock: asyncio.Lock | None = None  # set in run(), guards concurrent starts
        self._start_sendspin_requests = 0
        self._start_sendspin_processed = 0
        self._playback_health = PlaybackHealthMonitor()
        self._ipc_service = SubprocessIpcService(
            player_name=player_name,
            protocol_warning_cache=self._seen_ipc_protocol_warnings,
            status_updater=self._update_status,
            log_methods=_IPC_LOG_METHODS,
            logger_=logger,
            allowed_keys=_IPC_ALLOWED_KEYS,
        )
        self._command_service = SubprocessCommandService(logger_=logger)
        self._pending_reconnect_unmute_sync = False
        self._sink_mute_watchdog_task: asyncio.Task | concurrent.futures.Future | None = None
        self._stderr_service = SubprocessStderrService(
            player_name=player_name,
            update_status=self._update_status,
            logger_=logger,
        )
        self._stop_service = SubprocessStopService(logger_=logger)
        # Debounced, off-loop persistence of daemon-driven volume / static-delay
        # changes.  ``_read_subprocess_output`` runs on the main asyncio loop;
        # writing config.json (fsync under a lock) inline there would block every
        # device's IPC on an MA volume ramp.  Keyed by field name so volume and
        # delay debounce independently.
        self._persist_timers: dict[str, threading.Timer] = {}
        self._persist_timers_lock = threading.Lock()
        self._idle_timer_lock = threading.Lock()
        self._idle_timer_task: asyncio.Task | concurrent.futures.Future | None = None
        self._power_save_timer_task: asyncio.Task | concurrent.futures.Future | None = None
        self._sink_monitor: object | None = None  # set by main() after SinkMonitor.start()
        self._bind_failures: int = 0  # consecutive failures of find_available_bind_port
        self._restart_halted: bool = False  # once True, restart loop skips spawning
        # Ring of recent daemon spawns/exits.  Populated on spawn (entry created)
        # and on death (exit fields filled).  Used for the diagnostics report
        # and for the repeating-interval pattern detector (#291 follow-up).
        self._spawn_history: deque[SpawnRecord] = deque(maxlen=10)
        self._timing_history: deque[dict[str, object]] = deque(maxlen=360)
        self._current_spawn: SpawnRecord | None = None
        # Set by ``stop_sendspin`` immediately before signalling the daemon so
        # the death-handler can flag the corresponding SpawnRecord as
        # ``unexpected=False`` and suppress the user-facing "daemon exited"
        # banner.  Cleared after the death is processed.
        self._explicit_stop_pending: bool = False

    @property
    def _playing_since(self) -> float | None:
        return self._playback_health.playing_since

    @_playing_since.setter
    def _playing_since(self, value: float | None) -> None:
        self._playback_health.playing_since = value

    @property
    def _zombie_restart_count(self) -> int:
        return self._playback_health.restart_count

    @_zombie_restart_count.setter
    def _zombie_restart_count(self, value: int) -> None:
        self._playback_health.restart_count = value

    @property
    def _has_streamed(self) -> bool:
        return self._playback_health.has_streamed

    @_has_streamed.setter
    def _has_streamed(self, value: bool) -> None:
        self._playback_health.has_streamed = value

    def _event_device_id(self) -> str:
        """Return the stable event-history key for this device."""
        return self.player_id or f"sendspin-{self._safe_id}"

    def _build_status_events(
        self,
        previous: dict[str, object],
        current: dict[str, object],
        updates: dict,
    ) -> list[dict[str, object]]:
        """Translate meaningful status transitions into structured device events."""
        return StatusEventBuilder.build(previous, current, updates)

    def _update_status(self, updates: dict) -> None:
        """Thread-safe update of self.status; notifies SSE listeners.

        CRITICAL: Thread safety — acquired by Flask WSGI threads, asyncio event loop,
        and D-Bus callback thread. Callbacks are invoked OUTSIDE the lock to avoid deadlocks.
        """
        recorded_events: list[dict[str, object]] = []
        if "artwork_url" in updates:
            raw_art = updates.get("artwork_url")
            if isinstance(raw_art, str) and raw_art.strip():
                updates["artwork_url"] = build_artwork_proxy_url(raw_art)
        with self._status_lock:
            previous = self.status.copy()
            self._playback_health.observe_status_update(
                previous_playing=bool(self.status.get("playing")),
                updates=updates,
                now=time.monotonic(),
            )
            self.status.update(updates)
            if updates.get("timing_sampled_at"):
                self._timing_history.append(
                    {
                        key: self.status.get(key)
                        for key in (
                            "timing_sampled_at",
                            "backend_output_latency_ms",
                            "buffered_audio_ms",
                            "playback_sync_error_ms",
                            "clock_uncertainty_ms",
                            "reanchor_count",
                        )
                    }
                )
            recorded_events = self._build_status_events(previous, self.status.copy(), updates)
        for event in recorded_events:
            _state.publish_device_event(
                self._event_device_id(),
                str(event["event_type"]),
                level=str(event["level"]),
                message=str(event["message"]),
                details=event["details"] if isinstance(event["details"], dict) else None,
            )
        _state.notify_status_changed()
        # ── Idle disconnect timer ─────────────────────────────────────
        # Daemon-reported playback flags always participate in idle timer
        # management, regardless of SinkMonitor availability.  PipeWire's
        # PA compatibility layer may not emit sink state change events for
        # BT sinks (#120), so the SinkMonitor alone is insufficient.
        #
        # SinkMonitor callbacks (_on_sink_active / _on_sink_idle) still
        # fire and provide the fastest response on systems where PA events
        # are reliable.  Daemon flags act as a dual authority — overlapping
        # cancel/start calls are harmless (_start_idle_timer cancels first).
        _idle_mode = getattr(self, "idle_mode", "default")
        if _idle_mode in ("auto_disconnect", "power_save"):
            if "playing" in updates or "audio_streaming" in updates:
                was_active = previous.get("playing", False) or previous.get("audio_streaming", False)
                now_active = self.status.get("playing", False) or self.status.get("audio_streaming", False)
                if not was_active and now_active:
                    self._cancel_idle_timer()
                    if _idle_mode == "power_save":
                        self._cancel_power_save_timer()
                        if self.status.get("bt_power_save"):
                            asyncio.ensure_future(self._exit_power_save())
                elif was_active and not now_active and not self.status.get("bt_standby"):
                    if _idle_mode == "auto_disconnect":
                        self._start_idle_timer()
                    elif _idle_mode == "power_save":
                        self._start_power_save_timer()
            # Server-connected fallback: only when SinkMonitor is not
            # running (otherwise the timer was already started by
            # SinkMonitor.register → on_idle at registration time).
            if not self._sink_monitor_active():
                if (
                    "server_connected" in updates
                    and self.status.get("server_connected") is True
                    and not self.status.get("audio_streaming")
                    and not self.status.get("playing")
                    and not self.status.get("bt_standby")
                ):
                    if _idle_mode == "auto_disconnect":
                        self._start_idle_timer()
                    elif _idle_mode == "power_save":
                        self._start_power_save_timer()
        # ── Sink mute watchdog ────────────────────────────────────────
        if "sink_muted" in updates:
            if self.status.get("sink_muted") and not self.status.get("muted"):
                self._start_sink_mute_watchdog()
            else:
                self._cancel_sink_mute_watchdog()

    # ── Idle disconnect timer ────────────────────────────────────────────

    def _sink_monitor_active(self) -> bool:
        """Return True when a PA sink monitor is running for this bridge.

        Checks whether the monitor loop is live.  The per-device sink
        registration may happen later (once BT audio is configured), but
        the fallback daemon-flag timer must be suppressed as soon as the
        monitor is running — registration fires the correct callback
        immediately when it arrives.
        """
        sm = getattr(self, "_sink_monitor", None)
        return sm is not None and sm.available

    def _on_sink_active(self) -> None:
        """Called by SinkMonitor when PA sink enters ``running``.

        Cancels any pending idle/power-save timer — audio is flowing.
        """
        mode = getattr(self, "idle_mode", "default")
        if mode == "auto_disconnect":
            logger.debug("[%s] PA sink -> running -- cancelling idle timer", self.player_name)
            self._cancel_idle_timer()
        elif mode == "power_save":
            logger.debug("[%s] PA sink -> running -- cancelling power-save timer", self.player_name)
            self._cancel_power_save_timer()
            if self.status.get("bt_power_save"):
                asyncio.ensure_future(self._exit_power_save())

    def _on_sink_idle(self) -> None:
        """Called by SinkMonitor when PA sink leaves ``running``.

        Starts the appropriate idle timer based on idle_mode.
        """
        mode = getattr(self, "idle_mode", "default")
        if self.status.get("bt_standby"):
            return
        if mode == "auto_disconnect":
            logger.debug(
                "[%s] PA sink -> idle -- starting idle timer (%d min)",
                self.player_name,
                self.idle_disconnect_minutes,
            )
            self._start_idle_timer()
        elif mode == "power_save":
            logger.debug(
                "[%s] PA sink -> idle -- starting power-save timer (%d min)",
                self.player_name,
                self.power_save_delay_minutes,
            )
            self._start_power_save_timer()

    def _start_idle_timer(self) -> None:
        """Start (or restart) the idle disconnect timer.

        Thread-safe: protected by ``_idle_timer_lock`` to avoid leaked timers
        when called concurrently from the asyncio event loop (sink monitor
        callbacks) and Flask/Waitress threads (``_update_status`` fallback).
        """
        timeout = self.idle_disconnect_minutes * 60

        async def _idle_timeout() -> None:
            try:
                await asyncio.sleep(timeout)
                # Safety guard: re-check conditions that may have changed
                if self.status.get("bt_standby") or self.status.get("bt_waking"):
                    logger.debug("[%s] Idle timer fired but device is standby/waking — skipping", self.player_name)
                    return
                if getattr(self, "idle_mode", "default") != "auto_disconnect":
                    return
                # Check cached PA sink state as a safety net — if the sink
                # monitor missed a "running" event (e.g. brief PA disconnect),
                # this prevents a false standby while audio is actually flowing.
                sm = getattr(self, "_sink_monitor", None)
                sink = getattr(self, "bluetooth_sink_name", None)
                if sm and sink and getattr(sm, "_sink_states", {}).get(sink) == "running":
                    logger.info(
                        "[%s] Idle timer fired but PA sink is running — suppressing standby",
                        self.player_name,
                    )
                    return
                # Secondary safety net: daemon status flags.  These can
                # lag behind PA state on reconnect, but if the daemon says
                # playing=True we should never enter standby.
                if self.status.get("playing") or self.status.get("audio_streaming"):
                    logger.info(
                        "[%s] Idle timer fired but daemon reports active playback "
                        "(playing=%s audio_streaming=%s) — suppressing standby",
                        self.player_name,
                        self.status.get("playing"),
                        self.status.get("audio_streaming"),
                    )
                    return
                logger.info(
                    "[%s] Idle for %d min — entering standby "
                    "(sink_monitor=%s, sink=%s, sink_state=%s, playing=%s, streaming=%s)",
                    self.player_name,
                    self.idle_disconnect_minutes,
                    "active" if sm and getattr(sm, "available", False) else "inactive",
                    sink or "none",
                    getattr(sm, "_sink_states", {}).get(sink, "unknown") if sm and sink else "n/a",
                    self.status.get("playing"),
                    self.status.get("audio_streaming"),
                )
                await self._enter_standby()
            except asyncio.CancelledError:
                return

        with self._idle_timer_lock:
            self._cancel_idle_timer_unlocked()
            loop = _state.get_main_loop()
            if loop and loop.is_running():
                self._idle_timer_task = asyncio.run_coroutine_threadsafe(_idle_timeout(), loop)
            else:
                try:
                    self._idle_timer_task = asyncio.ensure_future(_idle_timeout())
                except RuntimeError:
                    pass

    def _cancel_idle_timer(self) -> None:
        """Cancel any pending idle disconnect timer (thread-safe)."""
        with self._idle_timer_lock:
            self._cancel_idle_timer_unlocked()

    def _cancel_idle_timer_unlocked(self) -> None:
        """Cancel idle timer — must be called with ``_idle_timer_lock`` held."""
        task = self._idle_timer_task
        if task is None:
            return
        self._idle_timer_task = None
        if hasattr(task, "cancel"):
            task.cancel()

    # ── Power save timer ──────────────────────────────────────────────────

    def _start_power_save_timer(self) -> None:
        """Schedule PA sink suspend after ``power_save_delay_minutes``."""
        delay = getattr(self, "power_save_delay_minutes", 1) * 60

        async def _ps_timeout() -> None:
            try:
                await asyncio.sleep(delay)
                if self.status.get("bt_standby") or self.status.get("bt_waking"):
                    return
                if self.status.get("playing") or self.status.get("audio_streaming"):
                    return
                if getattr(self, "idle_mode", "default") != "power_save":
                    return
                await self._enter_power_save()
            except asyncio.CancelledError:
                return

        with self._idle_timer_lock:
            self._cancel_power_save_timer_unlocked()
            loop = _state.get_main_loop()
            if loop and loop.is_running():
                self._power_save_timer_task = asyncio.run_coroutine_threadsafe(_ps_timeout(), loop)
            else:
                try:
                    self._power_save_timer_task = asyncio.ensure_future(_ps_timeout())
                except RuntimeError:
                    pass

    def _cancel_power_save_timer(self) -> None:
        """Cancel pending power-save suspend timer (thread-safe)."""
        with self._idle_timer_lock:
            self._cancel_power_save_timer_unlocked()

    def _cancel_power_save_timer_unlocked(self) -> None:
        task = self._power_save_timer_task
        if task is None:
            return
        self._power_save_timer_task = None
        if hasattr(task, "cancel"):
            task.cancel()

    async def _enter_power_save(self) -> None:
        """Suspend the PA sink to release A2DP transport (BT stays connected)."""
        if self.status.get("bt_power_save"):
            return
        sink = self.bluetooth_sink_name
        if not sink:
            return
        from sendspin_bridge.services.audio.pulse import asuspend_sink

        ok = await asuspend_sink(sink, True)
        if ok:
            self._update_status({"bt_power_save": True})
            logger.info("[%s] Entered power-save (PA sink suspended)", self.player_name)
        else:
            logger.warning("[%s] Failed to suspend PA sink for power-save", self.player_name)

    async def _exit_power_save(self) -> None:
        """Resume the PA sink (re-open A2DP transport)."""
        if not self.status.get("bt_power_save"):
            return
        sink = self.bluetooth_sink_name
        if not sink:
            self._update_status({"bt_power_save": False})
            return
        from sendspin_bridge.services.audio.pulse import asuspend_sink

        ok = await asuspend_sink(sink, False)
        self._update_status({"bt_power_save": False})
        if ok:
            logger.info("[%s] Exited power-save (PA sink resumed)", self.player_name)
        else:
            logger.warning("[%s] Failed to resume PA sink from power-save", self.player_name)

    # ---- Sink mute watchdog (safety net) ----

    def _start_sink_mute_watchdog(self) -> None:
        """Schedule auto-unmute if sink stays muted without user intent after 30s."""

        async def _watchdog() -> None:
            try:
                await asyncio.sleep(30)
                with self._status_lock:
                    sink_muted = self.status.get("sink_muted")
                    app_muted = self.status.get("muted")
                    bt_connected = self.status.get("bluetooth_connected")
                if not sink_muted or app_muted or not bt_connected:
                    return
                sink = self.bluetooth_sink_name
                if not sink:
                    return
                from sendspin_bridge.services.audio.pulse import aset_sink_mute

                ok = await aset_sink_mute(sink, False)
                if ok:
                    self._update_status({"sink_muted": False})
                    logger.info("[%s] Auto-unmuted sink %s (safety net)", self.player_name, sink)
                else:
                    logger.warning("[%s] Safety-net auto-unmute failed for %s", self.player_name, sink)
            except asyncio.CancelledError:
                return

        with self._idle_timer_lock:
            self._cancel_sink_mute_watchdog_unlocked()
            loop = _state.get_main_loop()
            if loop and loop.is_running():
                self._sink_mute_watchdog_task = asyncio.run_coroutine_threadsafe(_watchdog(), loop)
            else:
                try:
                    self._sink_mute_watchdog_task = asyncio.ensure_future(_watchdog())
                except RuntimeError:
                    pass

    def _cancel_sink_mute_watchdog(self) -> None:
        with self._idle_timer_lock:
            self._cancel_sink_mute_watchdog_unlocked()

    def _cancel_sink_mute_watchdog_unlocked(self) -> None:
        task = self._sink_mute_watchdog_task
        if task is None:
            return
        self._sink_mute_watchdog_task = None
        if hasattr(task, "cancel"):
            task.cancel()

    async def _enter_standby(self) -> None:
        """Disconnect BT and park daemon on null sink to let the speaker save power.

        Phase 2 behavior: daemon stays alive on a PA null sink so the player
        remains visible in MA.  When MA sends play, the bridge auto-reconnects BT.
        """
        if self.status.get("bt_standby"):
            return
        self._update_status(
            {
                "bt_standby": True,
                "bt_standby_since": datetime.now(tz=UTC).isoformat(),
                "bt_released_by": "idle_timeout",
            }
        )
        # Move daemon streams to null sink instead of killing daemon
        daemon_pid = self._daemon_proc.pid if self._daemon_proc else None
        if daemon_pid:
            from sendspin_bridge.services.audio.pulse import STANDBY_SINK_NAME, aensure_null_sink, amove_pid_sink_inputs

            if await aensure_null_sink():
                # Redirect PULSE_SINK so new streams go to null sink (not missing BT sink)
                try:
                    await self._send_subprocess_command({"cmd": "set_standby", "sink": STANDBY_SINK_NAME})
                except IPCError as exc:
                    logger.debug("[%s] set_standby IPC failed (daemon may have exited): %s", self.player_name, exc)
                moved = await amove_pid_sink_inputs(daemon_pid, STANDBY_SINK_NAME)
                logger.info("[%s] Moved %d stream(s) to null sink", self.player_name, moved)
            else:
                logger.warning("[%s] Could not create null sink — falling back to daemon stop", self.player_name)
                await self.stop_sendspin()

        # Disconnect BT to save speaker battery
        if self.bt_manager:
            try:
                self.bt_manager.disconnect_device()
            except Exception as exc:
                logger.warning("[%s] BT disconnect on standby failed: %s", self.player_name, exc)
        _state.publish_device_event(
            self._event_device_id(),
            DeviceEventType.BLUETOOTH_STANDBY_ENTERED,
            message="Speaker entered standby after idle timeout",
            details={"idle_minutes": self.idle_disconnect_minutes},
        )
        logger.info("[%s] Entered standby (BT disconnected, daemon on null sink)", self.player_name)

    async def _wake_from_standby(self) -> None:
        """Begin BT reconnect while keeping daemon alive on null sink.

        Sets ``bt_waking=True`` so bt_monitor reconnects BT without killing
        the daemon.  ``bt_standby`` stays True until ``_reroute_to_bt_sink()``
        successfully moves streams to the BT sink.

        Starts BT reconnect directly via ``run_in_executor`` for minimal
        latency — bt_monitor still handles the post-connect flow.
        """
        if not self.status.get("bt_standby"):
            return

        self._update_status(
            {
                "bt_waking": True,
                "bt_released_by": None,
            }
        )
        if self.bt_manager:
            self.bt_manager.allow_reconnect()
            self.bt_manager.signal_standby_wake()
            # Kick off BT connect directly — don't wait for bt_monitor's loop.
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, self.bt_manager.connect_device)
        _state.publish_device_event(
            self._event_device_id(),
            DeviceEventType.BLUETOOTH_STANDBY_EXITED,
            message="Speaker waking from standby",
        )
        logger.info("[%s] Waking from standby — direct BT reconnect started", self.player_name)

    async def _on_standby_play_detected(self) -> None:
        """Auto-wake: MA started playback while in standby — reconnect BT.

        Called from ``_read_subprocess_output()`` when ``playing=True`` arrives
        while ``bt_standby=True``.  The daemon is alive on the null sink;
        audio streams there silently until BT reconnects and streams are moved.
        """
        if not self.status.get("bt_standby") or self.status.get("bt_waking"):
            return
        logger.info("[%s] Play detected during standby — auto-waking", self.player_name)
        await self._wake_from_standby()

    async def _reroute_to_bt_sink(self) -> bool:
        """After BT reconnect, move streams from null sink to the BT sink and reanchor.

        Called from ``_start_sendspin_inner()`` when daemon is still alive after
        standby wake.  Clears ``bt_standby`` / ``bt_waking`` on success.

        Returns ``True`` if streams were rerouted, ``False`` if no streams
        existed (ALSA errors during standby may have destroyed them).
        """
        daemon_pid = self._daemon_proc.pid if self._daemon_proc else None
        if not daemon_pid or not self.bluetooth_sink_name:
            return False
        from sendspin_bridge.services.audio.pulse import amove_pid_sink_inputs

        # Restore PULSE_SINK to BT sink before rerouting so future streams target it
        try:
            await self._send_subprocess_command({"cmd": "set_standby"})
        except IPCError as exc:
            logger.debug("[%s] set_standby clear IPC failed: %s", self.player_name, exc)
        moved = await amove_pid_sink_inputs(daemon_pid, self.bluetooth_sink_name)
        # Clear standby state regardless of streams moved
        self._update_status(
            {
                "bt_standby": False,
                "bt_standby_since": None,
                "bt_waking": False,
            }
        )
        # SinkMonitor may have fired on_idle while bt_standby was still True
        # (race window).  Re-arm the idle timer now that standby is cleared.
        sm = getattr(self, "_sink_monitor", None)
        sink = getattr(self, "bluetooth_sink_name", None)
        if sm and sink and getattr(sm, "_sink_states", {}).get(sink) != "running":
            self._on_sink_idle()
        if moved > 0:
            logger.info("[%s] Rerouted %d stream(s) to BT sink %s", self.player_name, moved, self.bluetooth_sink_name)
            try:
                await self._send_subprocess_command({"cmd": "reconnect", "delay": 1.0})
            except IPCError as exc:
                logger.debug("[%s] reanchor IPC failed after wake: %s", self.player_name, exc)
            else:
                logger.info("[%s] Sent reanchor after wake", self.player_name)
            return True

        # No streams survived (ALSA errors destroyed them) — trigger MA
        # reconnect inside the daemon.  This is much faster than a full
        # subprocess restart because it skips process spawn + mDNS registration.
        logger.info("[%s] No streams to reroute — sending MA reconnect to daemon", self.player_name)
        try:
            await self._send_subprocess_command({"cmd": "reconnect", "delay": 0.5})
        except IPCError as exc:
            logger.debug("[%s] MA reconnect IPC failed: %s", self.player_name, exc)
        return True

    def _cancel_ma_reconnect_task(self) -> None:
        task = self._ma_reconnect_task
        if task is not None and not task.done():
            task.cancel()
        self._ma_reconnect_task = None

    def _clear_ma_reconnecting(self) -> None:
        self._cancel_ma_reconnect_task()
        if self.get_status_value("ma_reconnecting", False):
            self._update_status({"ma_reconnecting": False})

    def _schedule_ma_reconnect_timeout(self) -> None:
        self._cancel_ma_reconnect_task()

        async def _timeout_clear() -> None:
            try:
                await asyncio.sleep(_MA_RECONNECT_TIMEOUT_S)
                if self.get_status_value("ma_reconnecting", False):
                    self._update_status({"ma_reconnecting": False})
            except asyncio.CancelledError:
                return

        self._ma_reconnect_task = asyncio.create_task(_timeout_clear())

    def _mark_ma_reconnecting(self) -> None:
        if not self.get_status_value("ma_reconnecting", False):
            self._update_status({"ma_reconnecting": True})
        self._schedule_ma_reconnect_timeout()

    # ── BluetoothManagerHost protocol implementation ──────────────────

    def update_status(self, updates: dict) -> None:
        """Public status update entry point (BluetoothManagerHost protocol)."""
        self._update_status(updates)

    def get_status_value(self, key: str, default=None):
        """Thread-safe single-value read (BluetoothManagerHost protocol)."""
        with self._status_lock:
            return self.status.get(key, default)

    def is_subprocess_running(self) -> bool:
        """Check if daemon subprocess is alive (BluetoothManagerHost protocol)."""
        return self.is_running()

    # ── Spawn history & pattern detection (issue #291 follow-up) ───────────

    def _detect_repeating_lifetime(self, tolerance_s: float = 1.0) -> float | None:
        """Return mean lifetime when the last 3 unexpected deaths landed within ±tolerance_s.

        Returns ``None`` when fewer than 3 unexpected deaths are recorded or
        when their lifetimes spread beyond *tolerance_s*.  A non-``None``
        return value indicates the daemon is hitting a deterministic timeout
        (e.g. WebSocket open_timeout, handshake deadline, unreachable-host
        retry budget) — actionable for the operator.
        """
        completed = [r.lifetime_s for r in self._spawn_history if r.lifetime_s is not None and r.unexpected]
        if len(completed) < 3:
            return None
        last3 = completed[-3:]
        if all(abs(x - last3[0]) <= tolerance_s for x in last3):
            return sum(last3) / 3
        return None

    def recent_spawn_records(self, n: int = 5) -> list[dict[str, Any]]:
        """Return a JSON-serializable view of the last *n* spawn records.

        Oldest first within the window.  Consumed by the diagnostics report
        block so operators see daemon lifetime patterns directly in the
        bundle they upload to GitHub issues.
        """
        records = list(self._spawn_history)[-n:]
        return [
            {
                "pid": r.pid,
                "spawn_at": r.spawn_at.isoformat(),
                "exit_at": r.exit_at.isoformat() if r.exit_at else None,
                "lifetime_s": r.lifetime_s,
                "exit_code": r.exit_code,
                "signal": r.signal,
                "unexpected": r.unexpected,
                "stderr_tail": list(r.stderr_tail),
            }
            for r in records
        ]

    async def stop_subprocess(self) -> None:
        """Stop the daemon subprocess (BluetoothManagerHost protocol)."""
        await self.stop_sendspin()

    async def start_subprocess(self) -> None:
        """Start the daemon subprocess (BluetoothManagerHost protocol)."""
        await self.start_sendspin()

    async def send_subprocess_command(self, cmd: dict) -> None:
        """Send command to daemon stdin (BluetoothManagerHost protocol).

        Best-effort: IPC errors are logged at DEBUG and swallowed so BT-monitor
        callers (``services/bluetooth/monitor.py``) don't crash their tasks
        when a daemon happens to be exiting concurrently.
        """
        try:
            await self._send_subprocess_command(cmd)
        except IPCError as exc:
            logger.debug("[%s] %s IPC failed: %s", self.player_name, cmd.get("cmd"), exc)

    def get_subprocess_pid(self) -> int | None:
        """Return daemon subprocess PID if alive (BluetoothManagerHost protocol)."""
        proc = self._daemon_proc
        if proc is not None and proc.returncode is None:
            return proc.pid
        return None

    def get_ip_address(self) -> str:
        """Get the primary IP address of this machine"""
        from sendspin_bridge.config import get_local_ip

        return get_local_ip() or "unknown"

    async def _status_monitor_loop(self):
        """Periodic status monitoring loop (BT state + daemon health)."""
        logger.debug("Status monitoring loop started")
        while self.running:
            try:
                if self.bt_manager:
                    # Read cached connected flag — monitor_and_reconnect() polls
                    # is_device_connected() on its own schedule and keeps this up-to-date,
                    # so we avoid a redundant bluetoothctl subprocess here.
                    bt_connected = self.bt_manager.connected
                    if bt_connected != self.status["bluetooth_connected"]:
                        self._update_status(
                            {
                                "bluetooth_connected": bt_connected,
                                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                            }
                        )

                # Check daemon subprocess health
                if self._daemon_proc is not None:
                    if self._daemon_proc.returncode is not None:
                        # Subprocess exited — capture exit context (#291).
                        exit_code = self._daemon_proc.returncode
                        sig = -exit_code if exit_code is not None and exit_code < 0 else None
                        tail: list[str] = []
                        try:
                            tail = self._stderr_service.tail() if self._stderr_service else []
                        except Exception:
                            tail = []
                        was_unexpected = not self._explicit_stop_pending
                        if self._current_spawn is not None:
                            now_ts = datetime.now(tz=UTC)
                            self._current_spawn.exit_at = now_ts
                            self._current_spawn.exit_code = exit_code
                            self._current_spawn.signal = sig
                            self._current_spawn.lifetime_s = (now_ts - self._current_spawn.spawn_at).total_seconds()
                            self._current_spawn.stderr_tail = list(tail)
                            self._current_spawn.unexpected = was_unexpected
                            spawn_pid = self._current_spawn.pid
                            lifetime = self._current_spawn.lifetime_s
                        else:
                            spawn_pid = 0
                            lifetime = None
                        self._current_spawn = None
                        self._explicit_stop_pending = False

                        status_updates: dict[str, Any] = {
                            "server_connected": False,
                            "connected": False,
                            "group_name": None,
                            "group_id": None,
                        }
                        # Surface exit context as last_error so the device card
                        # and diagnostics report show *something* even when the
                        # daemon exits cleanly (no stderr traceback, no IPC
                        # error envelope) — the issue #291 silent-exit pattern.
                        if was_unexpected and lifetime is not None:
                            tail_snippet = " | ".join(tail[-3:]) if tail else "<no stderr>"
                            status_updates["last_error"] = (
                                f"Sendspin daemon exited after {lifetime:.1f}s "
                                f"(code={exit_code}, signal={sig}); tail: {tail_snippet}"
                            )
                            status_updates["last_error_at"] = datetime.now(tz=UTC).isoformat()
                        self._update_status(status_updates)

                        recurring = self._detect_repeating_lifetime()
                        if recurring is not None:
                            logger.warning(
                                "[%s] Daemon dies at consistent ~%.1fs intervals over the last 3 spawns "
                                "— likely a connection-handshake timeout. Check SENDSPIN_SERVER / "
                                "SENDSPIN_PORT and that Music Assistant's Sendspin provider is enabled.",
                                self.player_name,
                                recurring,
                            )
                            self._update_status({"daemon_recurring_lifetime_s": recurring})
                        elif self.status.get("daemon_recurring_lifetime_s") is not None:
                            self._update_status({"daemon_recurring_lifetime_s": None})

                        self._clear_ma_reconnecting()
                        self._daemon_proc = None
                        # Don't restart if BT is disconnected — monitor_and_reconnect
                        # will call start_sendspin() once BT reconnects.
                        if self._restart_halted:
                            await asyncio.sleep(self._restart_delay)
                        elif not self.bt_manager or self.bt_manager.connected:
                            logger.warning(
                                "[%s] Daemon subprocess exited (PID %d): code=%s signal=%s "
                                "lifetime=%s; restarting in %.0fs",
                                self.player_name,
                                spawn_pid,
                                exit_code,
                                sig,
                                f"{lifetime:.1f}s" if lifetime is not None else "unknown",
                                self._restart_delay,
                            )
                            await asyncio.sleep(self._restart_delay)
                            self._restart_delay = min(self._restart_delay * 2, 30.0)
                            await self.start_sendspin()
                        else:
                            self._restart_delay = 1.0  # reset when BT drives the restart
                            logger.info("Daemon subprocess stopped; waiting for BT to reconnect")
                    else:
                        # Daemon alive — reset backoff + bind-failure state
                        self._restart_delay = 1.0
                        if self._bind_failures:
                            self._bind_failures = 0
                        if self._restart_halted:
                            self._restart_halted = False

                # Zombie playback watchdog: playing=True but no audio data for too long
                self._check_zombie_playback()

                await asyncio.sleep(5)
            except Exception as e:
                logger.error("Error updating status: %s", e)
                await asyncio.sleep(5)

    def _check_zombie_playback(self) -> None:
        """Detect zombie state (playing=True, streaming=False) and schedule restart.

        Only triggers when audio has NEVER arrived in the current play session.
        If audio was streaming before within the same ongoing play session
        (re-anchor, group resync, track change),
        PA buffers keep playing — this is normal, not a zombie.
        """
        need_restart = False
        elapsed = 0.0
        restart_count = 0
        with self._status_lock:
            need_restart, elapsed, restart_count = self._playback_health.check_zombie_playback(
                is_playing=bool(self.status.get("playing")),
                is_streaming=bool(self.status.get("audio_streaming")),
                daemon_alive=self._daemon_proc is not None and self._daemon_proc.returncode is None,
                now=time.monotonic(),
            )

        if not need_restart:
            return

        logger.warning(
            "[%s] Zombie playback detected: playing=True but no audio for %.0fs "
            "(restart %d/%d) — restarting subprocess",
            self.player_name,
            elapsed,
            restart_count,
            self._playback_health.max_zombie_restarts,
        )
        # Schedule restart on the event loop (we're called from an async context)
        asyncio.create_task(self._zombie_restart())

    async def _zombie_restart(self) -> None:
        """Restart subprocess to recover from zombie playback."""
        await self.stop_sendspin()
        await asyncio.sleep(1)
        await self.start_sendspin()

    async def start_sendspin(self) -> None:
        """Start the sendspin daemon as an isolated subprocess with PULSE_SINK routing."""
        lock = self._start_sendspin_lock
        if lock is None:
            await self._start_sendspin_inner()
            return
        self._start_sendspin_requests += 1
        if lock.locked():
            logger.debug("[%s] start_sendspin already in progress, queueing follow-up run", self.player_name)
            return
        async with lock:
            while self._start_sendspin_processed < self._start_sendspin_requests:
                self._start_sendspin_processed = self._start_sendspin_requests
                await self._start_sendspin_inner()

    async def _start_sendspin_inner(self) -> None:
        """Spawn daemon_process.py subprocess with PULSE_SINK in its environment."""
        try:
            # Pre-flight gate (#291): refuse to spawn when SENDSPIN_SERVER is
            # malformed (scheme prefix, embedded port, slashes, whitespace).
            # The web-UI form rejects these values up front and the migration
            # check warns on stored ones, but a raw config.json edit or
            # addon-options.json edit could still slip through.  Without this
            # gate we'd build `ws://http://host:port:port/sendspin` and the
            # daemon would silently exit at the websockets open_timeout (~10s)
            # with no traceback — exactly the issue #291 scenario.
            server_issue = validate_sendspin_server_format(self.server_host)
            if server_issue is not None:
                logger.error(
                    "[%s] %s — refusing to spawn daemon until the value is fixed.",
                    self.player_name,
                    server_issue.message,
                )
                self._update_status(
                    {
                        "last_error": server_issue.message,
                        "last_error_at": datetime.now(tz=UTC).isoformat(),
                        "server_connected": False,
                    }
                )
                return

            # Configure BT audio sink if not yet done (offloaded to executor
            # because configure_bluetooth_audio is a blocking call that sleeps
            # during sink discovery retries — up to ~18 s).
            if self.bt_manager and self.bt_manager.connected and not self.bluetooth_sink_name:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.bt_manager.configure_bluetooth_audio)

            # Phase 2 standby wake: daemon is alive on null sink — reroute streams
            # to the BT sink instead of spawning a new subprocess.
            if self.is_running() and self.bluetooth_sink_name:
                logger.info(
                    "[%s] Daemon already running — rerouting to BT sink %s",
                    self.player_name,
                    self.bluetooth_sink_name,
                )
                if await self._reroute_to_bt_sink():
                    return
                # Reroute failed (ALSA errors during standby destroyed PA
                # streams) — fall through to full daemon restart.
                logger.info("[%s] Reroute found 0 streams — full daemon restart", self.player_name)

            # Stop any existing subprocess first
            await self.stop_sendspin()

            # Reset play-session tracking for new subprocess
            self._playback_health.reset_for_new_subprocess()

            client_id = self.player_id

            if self.static_delay_ms is not None:
                static_delay_ms = self.static_delay_ms
            else:
                raw_delay_env = os.environ.get("SENDSPIN_STATIC_DELAY_MS", "0")
                try:
                    static_delay_ms = float(raw_delay_env)
                except (TypeError, ValueError):
                    logger.warning(
                        "[%s] Invalid SENDSPIN_STATIC_DELAY_MS=%r; using 0",
                        self.player_name,
                        raw_delay_env,
                    )
                    static_delay_ms = 0.0
                if not math.isfinite(static_delay_ms):
                    static_delay_ms = 0.0
                elif static_delay_ms < 0 or static_delay_ms > 5000:
                    clamped = max(0.0, min(5000.0, static_delay_ms))
                    logger.warning(
                        "[%s] SENDSPIN_STATIC_DELAY_MS=%s out of range; clamped to %.0f",
                        self.player_name,
                        raw_delay_env,
                        clamped,
                    )
                    static_delay_ms = clamped

            server_url: str | None = None
            if self.server_host and self.server_host.lower() not in ("auto", "discover", ""):
                effective_port = self.server_port
                # Always probe (not just when port == DEFAULT). MA's actual
                # Sendspin port is 8927 and the bridge's default flipped from
                # 9000 to 8927 in the issue #291 follow-up.  Users still on
                # an explicit `SENDSPIN_PORT: 9000` would otherwise dial a
                # closed port forever.  The probe returns the configured port
                # immediately if it responds (~10 ms cost in the happy path)
                # and walks the candidate ladder only on failure.
                probed = await _probe_port_if_default(self.server_host, self.server_port)
                if probed is not None and probed != self.server_port:
                    logger.warning(
                        "[%s] Sendspin port %d did not respond, but port %d did — using %d",
                        self.player_name,
                        self.server_port,
                        probed,
                        probed,
                    )
                    effective_port = probed
                server_url = f"ws://{self.server_host}:{effective_port}/sendspin"
                logger.info(
                    "Starting Sendspin player '%s' connecting to %s (port %s)",
                    self.player_name,
                    server_url,
                    self.listen_port,
                )
            else:
                logger.info(
                    "Starting Sendspin player '%s' with auto-discovery (port %s)", self.player_name, self.listen_port
                )

            # Host-side bind preflight: aiosendspin's ClientListener (aiohttp
            # TCPSite) does not auto-shift on EADDRINUSE, so a stale process
            # holding our port would crash the daemon on every restart cycle.
            # Probe the wildcard interface ("0.0.0.0") because the daemon
            # subprocess receives only listen_port in params — its listener
            # binds wildcard by default, so probing a specific listen_host
            # would miss collisions on other interfaces.
            requested_port = int(self.listen_port)
            available_port = find_available_bind_port(requested_port, host="0.0.0.0", max_attempts=DEFAULT_MAX_ATTEMPTS)
            if available_port is None:
                self._bind_failures += 1
                hint = (
                    f"Cannot bind any port in range "
                    f"{requested_port}-{requested_port + DEFAULT_MAX_ATTEMPTS - 1}. "
                    f"Run 'lsof -i :{requested_port}' on the host to find the owner."
                )
                logger.error("[%s] %s", self.player_name, hint)
                self._update_status(
                    {
                        "last_error": hint,
                        "last_error_at": datetime.now(tz=UTC).isoformat(),
                        "port_collision": True,
                    }
                )
                if self._bind_failures >= _MAX_BIND_FAILURES:
                    logger.error(
                        "[%s] %d consecutive bind failures — halting restart loop",
                        self.player_name,
                        self._bind_failures,
                    )
                    self._restart_halted = True
                return
            if available_port != requested_port:
                logger.info(
                    "[%s] listen_port %d unavailable — auto-shifted to %d",
                    self.player_name,
                    requested_port,
                    available_port,
                )
                self._update_status({"port_collision": True, "active_listen_port": available_port})
            else:
                # Clean start — clear any stale collision flag from a prior cycle
                # so the UI does not show a permanent "port_collision" indicator.
                if self.status.get("port_collision") or self.status.get("active_listen_port") is not None:
                    self._update_status({"port_collision": False, "active_listen_port": None})
            self.listen_port = available_port

            # Pull per-device BT identity from BlueZ so the daemon can advertise
            # the real speaker name + vendor in client/hello.device_info instead
            # of the generic "Sendspin BT Bridge vX" / hostname pair (#237 follow-up).
            # Empty strings on read failure → daemon falls back to bridge identity.
            bt_product_name = ""
            bt_manufacturer = ""
            bt_dbus_path = getattr(self.bt_manager, "_dbus_device_path", None) if self.bt_manager else None
            if bt_dbus_path:
                # These are synchronous D-Bus round-trips (dbus-python); run all
                # three off the event loop in a single executor hop so a slow BlueZ
                # can't stall every other device's IPC during a spawn.
                def _read_bt_identity(path: str) -> tuple[str, str]:
                    # Alias is user-renamable in HAOS BT UI / bluetoothctl; prefer it.
                    name = _dbus_get_device_property(path, "Alias") or _dbus_get_device_property(path, "Name") or ""
                    manufacturer = vendor_from_modalias(_dbus_get_device_property(path, "Modalias"))
                    return name, manufacturer

                loop = asyncio.get_running_loop()
                bt_product_name, bt_manufacturer = await loop.run_in_executor(None, _read_bt_identity, bt_dbus_path)

            params = json.dumps(
                with_protocol_version(
                    {
                        "player_name": self.player_name,
                        "client_id": str(client_id),
                        "listen_port": self.listen_port,
                        "url": server_url,
                        "static_delay_ms": static_delay_ms,
                        "required_lead_time_ms": self.required_lead_time_ms,
                        "min_buffer_ms": self.min_buffer_ms,
                        "bluetooth_sink_name": self.bluetooth_sink_name,
                        "bluetooth_device_path": bt_dbus_path,
                        "volume": self.status.get("volume", 100),
                        "muted": bool(self.status.get("muted", False)),
                        "settings_dir": f"/tmp/sendspin-{self._safe_id}",
                        "preferred_format": self.preferred_format,
                        "config_schema_version": CONFIG_SCHEMA_VERSION,
                        "bt_product_name": bt_product_name,
                        "bt_manufacturer": bt_manufacturer,
                    }
                )
            )

            # Build subprocess environment: inherit everything + PULSE_SINK for routing
            # CRITICAL: Audio routing — PULSE_SINK determines which BT speaker this
            # subprocess sends audio to. Wrong value = audio to wrong speaker or silence.
            env = os.environ.copy()
            if self.bluetooth_sink_name:
                env["PULSE_SINK"] = self.bluetooth_sink_name
                logger.info("[%s] Subprocess PULSE_SINK=%s", self.player_name, self.bluetooth_sink_name)
            # Unique application.name so PA module-stream-restore does not confuse
            # streams across subprocesses (all share the same python3 binary name).
            env["PULSE_PROP_application.name"] = f"sendspin-{self.player_id}"

            self._daemon_proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "sendspin_bridge.services.ipc.daemon_process",
                params,
                stdout=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__)),
                limit=1024
                * 1024,  # 1 MB readline buffer — leaves headroom for occasional fat status frames (track metadata + queue context)
            )
            self._update_status({"playing": False})
            self._clear_ma_reconnecting()
            self._pending_reconnect_unmute_sync = True

            # Start async tasks to consume subprocess stdout and stderr
            self._daemon_task = asyncio.create_task(self._read_subprocess_output())
            self._stderr_task = asyncio.create_task(self._read_subprocess_stderr())

            def _on_reader_done(t: asyncio.Task) -> None:
                if not t.cancelled() and t.exception():
                    logger.error("[%s] stdout reader error: %s", self.player_name, t.exception())

            self._daemon_task.add_done_callback(_on_reader_done)
            self._stderr_task.add_done_callback(_on_reader_done)
            logger.info("Sendspin daemon subprocess started (PID %s) for '%s'", self._daemon_proc.pid, self.player_name)
            # Open a spawn-history entry — the death handler fills the rest.
            self._current_spawn = SpawnRecord(
                pid=int(self._daemon_proc.pid),
                spawn_at=datetime.now(tz=UTC),
            )
            self._spawn_history.append(self._current_spawn)

        except Exception as e:
            logger.error("Failed to start Sendspin daemon subprocess: %s", e)
            self._update_status({"last_error": str(e), "server_connected": False})

    _STDOUT_IDLE_TIMEOUT_SECS: float = 120.0
    _PERSIST_DEBOUNCE_SECS: float = 1.0

    def _schedule_persist(self, key: str, fn, mac: str, value: int) -> None:
        """Debounce a daemon-driven config write and run it off the loop.

        Mirrors ``routes/api._schedule_volume_persist``: a ``threading.Timer``
        (its own thread, not the asyncio loop) fires ``fn(mac, value)`` after a
        quiet window, and a newer value cancels the pending one.  This keeps the
        synchronous ``config.json`` fsync off the event-loop thread and coalesces
        an MA volume ramp into a single write.
        """
        with self._persist_timers_lock:
            old = self._persist_timers.pop(key, None)
            if old is not None:
                old.cancel()
            timer = threading.Timer(self._PERSIST_DEBOUNCE_SECS, self._run_persist, args=(key, fn, mac, value))
            timer.daemon = True
            self._persist_timers[key] = timer
            timer.start()

    def _run_persist(self, key: str, fn, mac: str, value: int) -> None:
        # Runs on the Timer's own thread — the fsync never touches the loop.
        # Stale entries are replaced by the next ``_schedule_persist`` and
        # cleared by ``_cancel_persist_timers``, so no cleanup is needed here.
        try:
            fn(mac, value)
        except Exception:  # pragma: no cover - best-effort persistence
            logger.exception("[%s] failed to persist %s=%s", self.player_name, key, value)

    def _cancel_persist_timers(self) -> None:
        """Cancel any pending debounced writes (called on stop/teardown)."""
        with self._persist_timers_lock:
            for timer in self._persist_timers.values():
                timer.cancel()
            self._persist_timers.clear()

    async def _read_subprocess_output(self) -> None:
        """Read JSON lines from daemon subprocess stdout and merge into self.status.

        ``stdout.readline()`` is wrapped in ``asyncio.wait_for`` so that a stalled
        daemon (e.g. subprocess alive but not writing) does not leave this reader
        blocked forever.  On timeout, we log at DEBUG and keep polling — only a
        real EOF (empty line) or a dead subprocess ends the loop.
        """
        if self._daemon_proc is None or self._daemon_proc.stdout is None:
            return
        stdout = self._daemon_proc.stdout
        while True:
            try:
                line = await asyncio.wait_for(stdout.readline(), timeout=self._STDOUT_IDLE_TIMEOUT_SECS)
            except TimeoutError:
                if self._daemon_proc.returncode is not None:
                    return
                logger.debug(
                    "[%s] subprocess idle (no stdout for %.0fs)",
                    self.player_name,
                    self._STDOUT_IDLE_TIMEOUT_SECS,
                )
                continue
            if not line:
                return
            msg = self._ipc_service.parse_line(line)
            if msg is None:
                continue
            if msg.get("type") == "status":
                # handle_message applies updates atomically via _update_status;
                # use the returned updates dict to detect volume changes without
                # separate lock acquisitions that could race.
                updates = self._ipc_service.handle_message(msg)
                if updates:
                    if updates.get("server_connected") is True:
                        self._clear_ma_reconnecting()
                    # Auto-wake: MA started playback while daemon is on null sink
                    if updates.get("playing") is True and self.status.get("bt_standby"):
                        asyncio.ensure_future(self._on_standby_play_detected())
                    new_volume = updates.get("volume")
                    _mac = self.bt_manager.mac_address if self.bt_manager else None
                    if isinstance(new_volume, int) and _mac:
                        self._schedule_persist("volume", save_device_volume, _mac, new_volume)
                    # MA-driven static_delay_ms changes flow through the daemon's
                    # status mirror (BridgeDaemon._handle_server_command). Persist
                    # to BLUETOOTH_DEVICES[i].static_delay_ms so the value
                    # survives restart and the bridge UI repaints from config.
                    new_delay = updates.get("static_delay_ms")
                    if isinstance(new_delay, int) and _mac:
                        self._schedule_persist("static_delay", save_device_static_delay, _mac, new_delay)
                        # Keep the parent-side cache in sync so a subsequent
                        # warm_restart doesn't re-spawn the subprocess with a
                        # stale ctor value.
                        self.static_delay_ms = float(new_delay)
                    # Sync unmute to MA after reconnect (#132) and after
                    # initial spawn (the daemon mutes the PA sink during
                    # startup to hide format-probe noise; MA polls
                    # volume_controller.get_state() in that window and
                    # records volume_muted=True even though the bridge
                    # never *intended* to be muted).  ``force=True``
                    # bypasses the "local status says unmuted" early-exit
                    # because that local flag doesn't reflect MA's view
                    # at this point.  Only fires once per (re)spawn so
                    # explicit user mute commands aren't overridden (#155).
                    if updates.get("sink_muted") is False and self._pending_reconnect_unmute_sync:
                        self._pending_reconnect_unmute_sync = False
                        await self._sync_unmute_to_ma(force=True)
            else:
                self._ipc_service.handle_message(msg)

    async def _sync_unmute_to_ma(self, *, force: bool = False) -> None:
        """Sync PA sink unmute to MA after the daemon's startup-mute window.

        Two related cases:

        1. **BT reconnect (#132)** — daemon unmutes the PA sink after a
           reconnect; MA may still hold muted=True from the previous
           session. Forward the unmute so the two stay in sync.
        2. **Initial daemon spawn (#user-report)** — daemon mutes the PA
           sink during startup to hide format-probe and routing glitches
           (``services/daemon_process.py:685``). MA's first
           ``volume_controller.get_state()`` poll happens during that
           window and reads ``(100, True)``, so MA records
           ``player.volume_muted=True`` in its state.  ~15 s later the
           startup-unmute watcher releases the PA sink mute, but the
           daemon's local ``status["muted"]`` was always ``False`` —
           hence the early-exit below would treat it as "already in sync"
           and never push the unmute back to MA.  HA's MA UI then keeps
           the volume slider greyed out and the player labelled muted
           even though audio is playing normally.

        The ``force=True`` path is for case #2: caller (the
        ``_pending_reconnect_unmute_sync`` branch in
        ``_read_subprocess_output``) knows we just observed the daemon
        going from sink-muted to sink-unmuted on initial spawn — push
        unconditionally because the local ``status["muted"]`` flag does
        not reflect MA's view at that moment.
        """
        from sendspin_bridge.services.music_assistant.ma_runtime_state import is_ma_connected

        if not is_ma_connected():
            return
        if not force and not self.status.get("muted"):
            return  # already in sync
        pid = self.player_id
        if not pid:
            return
        try:
            from sendspin_bridge.services.music_assistant.ma_monitor import send_player_cmd

            ok = await send_player_cmd("players/cmd/volume_mute", {"player_id": pid, "muted": False})
            if ok:
                logger.info("[%s] Synced unmute to MA after startup/reconnect", self.player_name)
                self._update_status({"muted": False})
        except Exception:
            logger.debug("[%s] Failed to sync unmute to MA", self.player_name, exc_info=True)

    async def _read_subprocess_stderr(self) -> None:
        """Forward daemon subprocess stderr lines with severity matching their content."""
        if self._daemon_proc is None or self._daemon_proc.stderr is None:
            return
        await self._stderr_service.read_stream(self._daemon_proc.stderr)

    def _handle_subprocess_stderr_line(self, line: str) -> None:
        """Compatibility proxy for stderr classification tests and legacy call sites."""
        self._stderr_service.handle_line(line)

    async def _send_subprocess_command(self, cmd: dict) -> None:
        """Write a JSON command to the daemon subprocess stdin."""
        await self._command_service.send(self._daemon_proc, cmd)

    async def send_reconnect(self) -> None:
        """Trigger the sendspin subprocess to reconnect to MA server.

        This causes the subprocess to send a fresh client_hello with the
        current bridge version and hostname, updating stale device_info in MA.
        Only call when the player is not actively playing.

        A 3-second delay is inserted after disconnect so that MA has time to
        process ClientRemovedEvent and unregister the old player before the
        new client_hello arrives (workaround for MA using register() instead
        of register_or_update() — see music-assistant/support#5049).
        """
        proc = self._daemon_proc
        if proc is None or proc.returncode is not None or not self.status.get("server_connected"):
            return
        self._mark_ma_reconnecting()
        try:
            await self._send_subprocess_command({"cmd": "reconnect", "delay": 3.0})
        except IPCError as exc:
            logger.debug("[%s] MA reconnect IPC failed: %s", self.player_name, exc)
            self._clear_ma_reconnecting()

    async def send_transport_command(self, action: str, value: object = None) -> bool:
        """Send a native Sendspin transport command to the daemon subprocess.

        Returns True if the command was dispatched, False otherwise.
        """
        if self._daemon_proc is None or self._daemon_proc.returncode is not None:
            return False
        cmd: dict = {"cmd": "transport", "action": action}
        if value is not None:
            cmd["value"] = value
        try:
            await self._send_subprocess_command(cmd)
        except IPCError as exc:
            logger.debug("[%s] transport %s IPC failed: %s", self.player_name, action, exc)
            return False
        return True

    # ── Reconfigure (hot-apply / warm-restart) ────────────────────────────

    # Parent-only fields that take effect by mutating self.<field> — they do
    # not require an IPC round-trip nor a subprocess restart.
    _RECONFIG_PARENT_ONLY_FIELDS: tuple[str, ...] = (
        "idle_mode",
        "idle_disconnect_minutes",
        "power_save_delay_minutes",
        "keepalive_enabled",
        "keepalive_interval",
        "room_id",
        "room_name",
    )

    async def apply_hot_config(self, fields_payload: dict[str, object]) -> list[str]:
        """Apply hot-update fields to this client without restarting the subprocess.

        Returns the list of fields that were actually applied (useful for the
        UI summary).  Unknown fields are silently ignored — the caller is
        expected to pass only keys classified as HOT_APPLY by
        :mod:`services.config_diff`.

        Per-key transactional: for fields that round-trip through the daemon
        (currently only ``static_delay_ms``), the IPC command is sent first;
        parent-side state (``self.<field>``) is committed only after the IPC
        succeeded.  When the daemon is alive but the write fails, the field
        is dropped from the returned list and parent-state stays consistent
        with what the daemon actually saw.  Parent-only fields apply
        unconditionally.
        """
        applied: list[str] = []

        for key, value in fields_payload.items():
            if key in ("static_delay_ms", "required_lead_time_ms", "min_buffer_ms"):
                try:
                    delay_ms = float(value) if value is not None else 0.0  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    logger.warning("[%s] Ignoring invalid %s: %r", self.player_name, key, value)
                    continue
                if self.is_running():
                    try:
                        await self._send_subprocess_command({"cmd": f"set_{key}", "value": delay_ms})
                    except IPCError as exc:
                        logger.warning(
                            "[%s] hot-apply %s IPC failed: %s — parent-state unchanged",
                            self.player_name,
                            key,
                            exc,
                        )
                        continue
                if key == "static_delay_ms":
                    self.static_delay_ms = delay_ms
                else:
                    setattr(self, key, round(delay_ms))
                self._update_status({key: round(delay_ms)})
                applied.append(key)
            elif key == "keepalive_interval":
                try:
                    interval = max(30, int(value) if value is not None else 30)  # type: ignore[call-overload]
                except (TypeError, ValueError):
                    logger.warning("[%s] Ignoring invalid keepalive_interval: %r", self.player_name, value)
                    continue
                self.keepalive_interval = interval
                applied.append(key)
            elif key == "keepalive_enabled":
                # Honour the explicit flag OR'd with the current idle_mode so
                # enabling keep_alive via either knob has the same effect.
                self.keepalive_enabled = bool(value) or self.idle_mode == "keep_alive"
                applied.append(key)
            elif key == "idle_mode":
                new_mode = str(value or "default")
                self.idle_mode = new_mode
                # idle_mode is the authoritative source for keepalive — switching
                # away from keep_alive must turn keepalive off (unless the same
                # payload also sets the legacy keepalive_enabled flag).
                explicit_keepalive = bool(fields_payload.get("keepalive_enabled", False))
                self.keepalive_enabled = new_mode == "keep_alive" or explicit_keepalive
                self._update_status({"idle_mode": new_mode})
                applied.append(key)
            elif key in self._RECONFIG_PARENT_ONLY_FIELDS:
                if hasattr(self, key):
                    setattr(self, key, value)
                    applied.append(key)

        return applied

    async def warm_restart(self, new_device: dict[str, object]) -> None:
        """Stop the subprocess and re-spawn it with the new device config.

        Updates the parent-side fields (``player_name``, ``listen_port``,
        ``listen_host``, ``preferred_format``, ``static_delay_ms``,
        ``idle_mode``, idle timers, etc.) from ``new_device`` before the
        restart so the fresh subprocess boots with the new parameters.
        """
        self._update_status({"reloading": True})
        try:
            self._apply_warm_restart_fields(new_device)
            await self.stop_sendspin()
            if self.running:
                await self._start_sendspin_inner()
        finally:
            self._update_status({"reloading": False})

    def _apply_warm_restart_fields(self, device: dict[str, object]) -> None:
        """Mutate self.<field> from the new device config before respawn."""
        if "player_name" in device:
            new_name = str(device["player_name"] or self.player_name)
            # The effective bridge suffix must be preserved on rename.
            suffix = f" @ {self._effective_bridge}" if self._effective_bridge else ""
            if suffix and not new_name.endswith(suffix):
                new_name = f"{new_name}{suffix}"
            self.player_name = new_name
        if "listen_port" in device and device["listen_port"] is not None:
            try:
                self.listen_port = int(device["listen_port"])  # type: ignore[call-overload]
            except (TypeError, ValueError):
                pass
        if "listen_host" in device:
            host_val = device.get("listen_host")
            self.listen_host = str(host_val) if host_val else None
        if "preferred_format" in device:
            fmt_val = device.get("preferred_format")
            self.preferred_format = str(fmt_val) if fmt_val else None
        if "static_delay_ms" in device:
            raw = device.get("static_delay_ms")
            try:
                self.static_delay_ms = float(raw) if raw is not None else None  # type: ignore[arg-type]
            except (TypeError, ValueError):
                self.static_delay_ms = None
        for hot_key in self._RECONFIG_PARENT_ONLY_FIELDS:
            if hot_key in device and hasattr(self, hot_key):
                setattr(self, hot_key, device[hot_key])
        # Re-derive keepalive_enabled from the new idle_mode + explicit legacy
        # flag, mirroring SendspinClient.__init__.  Without this, a warm restart
        # that changes idle_mode away from keep_alive would leave keepalive
        # permanently on (the setattr loop copies the stale parent-side value).
        if "idle_mode" in device or "keepalive_enabled" in device:
            mode_for_keepalive = str(device.get("idle_mode", self.idle_mode) or "default")
            explicit_flag = bool(device.get("keepalive_enabled", False))
            self.keepalive_enabled = mode_for_keepalive == "keep_alive" or explicit_flag
        # Refresh status mirror so the UI reflects renames / idle mode change
        # immediately, without waiting for the subprocess to emit.
        self._update_status(
            {
                "idle_mode": self.idle_mode,
                "listen_port": self.listen_port,
            }
        )

    # ── Keepalive ─────────────────────────────────────────────────────────

    async def _keepalive_loop(self) -> None:
        """Periodically send an infrasound burst to the BT sink to prevent speaker auto-disconnect."""
        try:
            # Stagger startup across devices to avoid simultaneous paplay bursts
            await asyncio.sleep(random.uniform(0, self.keepalive_interval))
            while self.running:
                await asyncio.sleep(self.keepalive_interval)
                if (
                    self.bt_manager
                    and self.bt_manager.connected
                    and self.bluetooth_sink_name
                    and not self.status.get("audio_streaming")
                    and not self.status.get("bt_standby")
                ):
                    await self._send_keepalive_burst()
        except asyncio.CancelledError:
            return

    async def _transport_telemetry_loop(self) -> None:
        """Poll optional BlueZ transport telemetry without blocking the loop."""
        if not self.bt_manager:
            return
        while self.running:
            path = getattr(self.bt_manager, "_dbus_device_path", None)
            if path:
                snapshot = await asyncio.get_running_loop().run_in_executor(
                    None, _dbus_get_media_transport_snapshot, path
                )
                recommendation = build_latency_recommendation(
                    reported_bt_delay_ms=snapshot.delay_ms,
                    codec_name=(
                        snapshot.codec_name
                        or ("sbc" if getattr(self.bt_manager, "prefer_sbc", False) else None)
                        or self.status.get("bt_codec_name")
                    ),
                    calibrated_delay_ms=(
                        self.status.get("static_delay_ms")
                        if self.status.get("static_delay_source") in {"microphone_calibration", "manual_calibration"}
                        else None
                    ),
                    calibration_source=self.status.get("static_delay_source"),
                )
                revision = f"{snapshot.path}:{snapshot.delay_tenths_ms}:{snapshot.codec_id}"
                backend_latency = self.status.get("backend_output_latency_ms")
                double_count_risk = bool(
                    snapshot.delay_ms is not None
                    and isinstance(backend_latency, (int, float))
                    and backend_latency >= snapshot.delay_ms * 0.75
                )
                explanation = recommendation.explanation
                if double_count_risk:
                    explanation += (
                        " The audio backend reports a similar latency; verify by ear to avoid double compensation."
                    )
                self._update_status(
                    {
                        "bt_transport_path": snapshot.path,
                        "bt_transport_state": snapshot.state,
                        "bt_codec_id": snapshot.codec_id,
                        "bt_codec_name": snapshot.codec_name,
                        "bt_reported_delay_ms": snapshot.delay_ms,
                        "bt_delay_reporting_supported": snapshot.delay_supported,
                        "bt_delay_updated_at": snapshot.updated_at,
                        "suggested_static_delay_ms": recommendation.value_ms,
                        "latency_suggestion_source": recommendation.source,
                        "latency_suggestion_confidence": recommendation.confidence,
                        "latency_suggestion_explanation": explanation,
                        "latency_suggestion_revision": revision,
                        "latency_double_count_risk": double_count_risk,
                    }
                )
            await asyncio.sleep(5.0 if self.status.get("audio_streaming") else 15.0)

    async def _send_keepalive_burst(self) -> None:
        """Write the configured keepalive payload to the BT PulseAudio sink via paplay.

        Payload selection follows ``self.keep_alive_method``:
        ``infrasound`` (default 2 Hz subsonic), ``silence`` (zero PCM,
        same length), or ``none`` (skip).  See :func:`_generate_keepalive_buffer`.
        """
        buf = _generate_keepalive_buffer(self.keep_alive_method)
        if not buf:
            return  # method == "none" — let the speaker time out naturally
        try:
            proc = await asyncio.create_subprocess_exec(
                "paplay",
                f"--device={self.bluetooth_sink_name}",
                "--raw",
                "--format=s16le",
                "--rate=44100",
                "--channels=2",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if proc.stdin:
                proc.stdin.write(buf)
                await proc.stdin.drain()
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            logger.debug("[%s] Keepalive burst sent to %s", self.player_name, self.bluetooth_sink_name)
        except Exception as exc:
            logger.debug("[%s] Keepalive burst failed: %s", self.player_name, exc)

    async def play_calibration_tone(self) -> bool:
        """Play a bounded calibration chirp directly on this device's BT sink."""
        if not self.bluetooth_sink_name or not self.status.get("bluetooth_connected"):
            return False
        try:
            logger.info(
                "[%s] Starting calibration chirp on %s",
                self.player_name,
                self.bluetooth_sink_name,
            )
            proc = await asyncio.create_subprocess_exec(
                "paplay",
                f"--device={self.bluetooth_sink_name}",
                "--raw",
                "--format=s16le",
                "--rate=48000",
                "--channels=2",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(
                proc.communicate(build_calibration_pcm(duration_seconds=2)),
                timeout=12.0,
            )
            if proc.returncode == 0:
                logger.info("[%s] Calibration chirp completed", self.player_name)
                return True
            detail = stderr.decode(errors="replace").strip() if stderr else "no stderr"
            logger.warning(
                "[%s] Calibration chirp exited with code %s: %s",
                self.player_name,
                proc.returncode,
                detail,
            )
            return False
        except Exception as exc:
            logger.warning("[%s] Calibration tone failed: %s", self.player_name, exc)
            return False

    async def stop_sendspin(self) -> None:
        """Stop the daemon subprocess gracefully."""
        self._cancel_persist_timers()
        # Flag the upcoming death as expected so the death-handler does NOT
        # populate ``last_error`` or emit the "exited unexpectedly" log.  Only
        # set when there's actually a daemon to stop — otherwise a no-op call
        # would shadow a genuine unexpected death from a later spawn.
        if self._daemon_proc is not None and getattr(self._daemon_proc, "returncode", None) is None:
            self._explicit_stop_pending = True
        cleared_tasks = await self._stop_service.stop_process(
            self._daemon_proc,
            send_stop=self._send_subprocess_command,
            player_name=self.player_name,
            reader_tasks={
                "_daemon_task": self._daemon_task,
                "_stderr_task": self._stderr_task,
            },
        )
        if cleared_tasks:
            self._daemon_task = cleared_tasks["_daemon_task"]
            self._stderr_task = cleared_tasks["_stderr_task"]
        else:
            self._daemon_task = None
            self._stderr_task = None
        self._daemon_proc = None
        self._clear_ma_reconnecting()

        self._update_status(
            {
                "server_connected": False,
                "connected": False,
                "playing": False,
                "audio_streaming": False,
                "current_track": None,
                "current_artist": None,
                "audio_format": None,
                "reanchoring": False,
                "group_name": None,
                "group_id": None,
            }
        )

    def is_running(self) -> bool:
        """Return True if the daemon subprocess is alive."""
        return self._daemon_proc is not None and self._daemon_proc.returncode is None

    def snapshot(self) -> dict:
        """Return all client attributes for status reporting under a single lock.

        Captures mutable state atomically so that ``build_device_snapshot``
        does not suffer TOCTOU races from reading attributes across multiple
        lock acquisitions.
        """
        bt_mgr = self.bt_manager
        with self._status_lock:
            return {
                "status": self.status.copy(),
                "bluetooth_sink_name": self.bluetooth_sink_name,
                "bt_management_enabled": self.bt_management_enabled,
                "connected_server_url": self.connected_server_url,
                "is_running": self._daemon_proc is not None and self._daemon_proc.returncode is None,
                "player_name": self.player_name,
                "player_id": self.player_id,
                "listen_port": self.listen_port,
                "server_host": self.server_host,
                "server_port": self.server_port,
                "static_delay_ms": self.static_delay_ms,
                "required_lead_time_ms": self.required_lead_time_ms,
                "min_buffer_ms": self.min_buffer_ms,
                "bt_manager": bt_mgr,
                "bluetooth_mac": bt_mgr.mac_address if bt_mgr else None,
                "effective_adapter_mac": getattr(bt_mgr, "effective_adapter_mac", None) if bt_mgr else None,
                "adapter": getattr(bt_mgr, "adapter", None) if bt_mgr else None,
                "adapter_hci_name": getattr(bt_mgr, "adapter_hci_name", "") if bt_mgr else "",
                "battery_level": getattr(bt_mgr, "battery_level", None) if bt_mgr else None,
                "paired": getattr(bt_mgr, "paired", None) if bt_mgr else None,
                "max_reconnect_fails": int(getattr(bt_mgr, "max_reconnect_fails", 0) or 0) if bt_mgr else 0,
            }

    def timing_history_snapshot(self) -> list[dict[str, object]]:
        """Return the bounded in-memory timing history."""
        with self._status_lock:
            return [dict(sample) for sample in self._timing_history]

    async def run(self) -> None:
        """Main run loop — connects BT, starts subprocess, monitors health."""
        self.running = True
        self._start_sendspin_lock = asyncio.Lock()
        self._start_sendspin_requests = 0
        self._start_sendspin_processed = 0

        # Start Sendspin player: immediately if no BT device, deferred if BT configured
        if not self.bt_management_enabled:
            logger.info("[%s] BT management disabled — skipping sendspin startup", self.player_name)
        elif not self.bt_manager:
            # No BT device configured — start on default audio immediately
            await self.start_sendspin()
        else:
            # BT device configured — defer daemon start until BT actually connects
            logger.info("[%s] Waiting for BT connection before starting player", self.player_name)

        # Start background tasks
        tasks = [asyncio.create_task(self._status_monitor_loop())]
        if self.keepalive_enabled:
            tasks.append(asyncio.create_task(self._keepalive_loop()))

        # Handle Bluetooth connection in background if configured
        logger.info("Bluetooth manager present: %s", self.bt_manager is not None)
        if self.bt_manager:
            logger.info("Starting Bluetooth connection task...")

            async def connect_bluetooth_async():
                """Connect Bluetooth in background without blocking"""
                logger.info("Bluetooth async task started, waiting 2 seconds...")
                await asyncio.sleep(2)  # Let sendspin start first
                if not self.bt_management_enabled:
                    return
                logger.info("Connecting Bluetooth speaker...")
                try:
                    # Run in thread pool to avoid blocking
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.bt_manager.connect_device)
                    bt_now = self.bt_manager.is_device_connected()
                    if bt_now != self.status["bluetooth_connected"]:
                        self._update_status(
                            {
                                "bluetooth_connected": bt_now,
                                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                            }
                        )
                    # Restart daemon with correct BT audio device now that sink is known.
                    # At start_sendspin() time bluetooth_sink_name was None (BT not yet
                    # connected), so the daemon was bound to the default audio device.
                    # Re-starting here ensures each player routes audio to its own BT sink.
                    # NOTE: bluetooth_sink_name is set by _on_sink_found() which runs
                    # synchronously inside connect_device() → configure_bluetooth_audio(),
                    # so it is guaranteed to be set before run_in_executor returns.
                    if bt_now and self.bluetooth_sink_name:
                        logger.info(
                            "[%s] BT connected with sink %s — starting player",
                            self.player_name,
                            self.bluetooth_sink_name,
                        )
                        await self.start_sendspin()
                except Exception as e:
                    logger.error("Error connecting Bluetooth: %s", e)

            tasks.append(asyncio.create_task(connect_bluetooth_async()))
            mon_task = asyncio.create_task(self.bt_manager.monitor_and_reconnect())

            def _on_monitor_done(t):
                if not t.cancelled() and t.exception():
                    logger.error("[%s] monitor_and_reconnect task DIED: %s", self.player_name, t.exception())

            mon_task.add_done_callback(_on_monitor_done)
            tasks.append(mon_task)

            # v2.63.0-rc.7: live RSSI for connected BR/EDR peers via the
            # kernel mgmt socket.  Spawned alongside monitor_and_reconnect
            # so the same shutdown cancel() loop tears it down.  Failures
            # in the wrapper are swallowed inside the tick — the
            # done-callback only fires for surprises (e.g. an asyncio
            # internal error) so we know to investigate.
            rssi_task = asyncio.create_task(self.bt_manager.run_rssi_refresh_loop())

            def _on_rssi_done(t):
                if not t.cancelled() and t.exception():
                    logger.error(
                        "[%s] run_rssi_refresh_loop task DIED: %s",
                        self.player_name,
                        t.exception(),
                    )

            rssi_task.add_done_callback(_on_rssi_done)
            tasks.append(rssi_task)
            tasks.append(asyncio.create_task(self._transport_telemetry_loop()))

        try:
            # Keep running
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Client shutting down...")
        finally:
            # Cleanup
            for task in tasks:
                task.cancel()
            await self.stop_sendspin()

    async def stop(self) -> None:
        """Stop the client and its subprocess."""
        self.running = False

    def set_bt_management_enabled(self, enabled: bool) -> None:
        """Release (enabled=False) or reclaim (enabled=True) the BT adapter."""
        self.bt_management_enabled = enabled
        self._update_status(
            {
                "bt_management_enabled": enabled,
                "bt_released_by": None if enabled else "user",
            }
        )
        if self.bt_manager:
            if enabled:
                self.bt_manager.allow_reconnect()
            else:
                self.bt_manager.cancel_reconnect()
        if not enabled:
            # Stop daemon via asyncio event loop (subprocess objects are not thread-safe)
            if self.is_running() and self._daemon_proc:
                logger.info("[%s] BT released — stopping sendspin daemon", self.player_name)
                loop = _state.get_main_loop()
                if loop and loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(self.stop_sendspin(), loop)
                    try:
                        fut.result(timeout=5.0)
                    except (TimeoutError, asyncio.CancelledError, RuntimeError) as exc:
                        logger.debug("[%s] stop_sendspin timed out on BT release: %s", self.player_name, exc)
                else:
                    # Fallback: direct os.kill is safe from any thread
                    try:
                        self._daemon_proc.kill()
                    except Exception as exc:
                        logger.debug("daemon proc kill on BT release failed: %s", exc)
            # Disconnect BT device (synchronous subprocess call, safe from any thread)
            if self.bt_manager:
                try:
                    self.bt_manager.disconnect_device()
                except Exception as e:
                    logger.warning("[%s] Disconnect on release failed: %s", self.player_name, e)
            logger.info("[%s] BT adapter released to host", self.player_name)
        else:
            logger.info("[%s] BT adapter reclaimed — monitor will reconnect", self.player_name)


async def main():
    """Main entry point"""
    orchestrator = BridgeOrchestrator()
    bootstrap = await orchestrator.initialize_runtime()
    # ``initialize_runtime`` calls ``BreadcrumbStore.init_boot`` which
    # rotates the previous run's ``boot.json`` to ``boot.prev.json``.
    # Logging the warning *before* rotation would describe a run two
    # restarts ago, not the immediately prior one — so we wait until
    # rotation has happened.
    _log_previous_run_summary(orchestrator)

    try:
        from sendspin_bridge.services.bluetooth import persist_device_enabled as _persist_enabled
    except ImportError:
        _persist_enabled = None

    try:
        await orchestrator.run_bridge_lifecycle(
            bootstrap,
            version=get_runtime_version(),
            client_factory=SendspinClient,
            bt_manager_factory=BluetoothManager,
            filter_devices_fn=_filter_duplicate_bluetooth_devices,
            load_saved_volume_fn=_load_saved_device_volume,
            persist_enabled_fn=_persist_enabled,
        )
    except asyncio.CancelledError:
        logger.info("Client shutting down...")


def _log_previous_run_summary(orchestrator: BridgeOrchestrator) -> None:
    """Emit one WARNING line if the previous run ended ungracefully.

    Reads ``boot.prev.json`` / ``exit.prev.json`` (rotated by
    ``init_boot`` later in the startup path) and surfaces a single line
    into the ring buffer + stdout.  Best-effort; any exception is
    swallowed.
    """
    try:
        prev = orchestrator.breadcrumbs.read_previous()
    except Exception:
        logger.debug("breadcrumbs: read_previous failed", exc_info=True)
        return
    if prev is None or prev.exit_kind in (None, "graceful"):
        return
    logger.warning(
        "Previous run ended ungracefully: kind=%s last_phase=%s last_phase_status=%s "
        "exit_code=%s exit_signal=%s started_at=%s bridge_version=%s",
        prev.exit_kind,
        prev.last_phase,
        prev.last_phase_status,
        prev.exit_code,
        prev.exit_signal,
        prev.started_at,
        prev.bridge_version,
    )


def _handle_cli_short_circuits() -> None:
    """Handle CLI flags that should exit before the full async runtime starts."""
    import sys

    if "--version" in sys.argv or "-V" in sys.argv:
        print(get_runtime_version())
        sys.exit(0)


if __name__ == "__main__":
    _handle_cli_short_circuits()
    asyncio.run(main())
