"""Subprocess entry point for a single BridgeDaemon instance.

Each Bluetooth speaker runs this module in its own subprocess so that
PortAudio/libpulse creates a dedicated PA context per speaker.  The parent
process sets ``PULSE_SINK`` in the subprocess environment before exec, so
every audio stream opened by that subprocess is routed to the correct
Bluetooth sink from the very first sample — no ``move-sink-input`` required.

Protocol (stdin/stdout, line-delimited JSON):

  subprocess → parent (stdout):
    {"type": "status", "playing": false, "connected": false, ...}  # full status on change
    {"type": "log", "level": "info", "msg": "..."}                 # forwarded log lines

  parent → subprocess (stdin):
    {"cmd": "set_volume", "value": 75}
    {"cmd": "stop"}

The subprocess exits with code 0 on clean stop, non-zero on error.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from sendspin_bridge.services.infrastructure.call_kwargs import filter_supported_call_kwargs
from sendspin_bridge.services.ipc.commands import InvalidCommand, UnknownCommand, decode_command
from sendspin_bridge.services.ipc.ipc_protocol import (
    IPC_PROTOCOL_VERSION,
    IPC_PROTOCOL_VERSION_KEY,
    build_error_envelope,
    build_log_envelope,
    build_status_envelope,
    is_compatible_protocol_version,
    parse_command_envelope,
    with_protocol_version,
)
from sendspin_bridge.services.ipc.shutdown import shut_down_tasks
from sendspin_bridge.services.ipc.status_store import StatusStore

# ---------------------------------------------------------------------------
# Minimal JSON-line log handler (forwarded to parent via stdout)
# ---------------------------------------------------------------------------

_LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Pattern that audio.py logs when re-anchoring is triggered
_REANCHOR_MSG = "re-anchoring"
_SYNC_ERROR_PREFIX = "Sync error "
# Seconds after the last re-anchor log before we auto-clear the reanchoring flag.
# sendspin logs "re-anchoring" AFTER it has already restarted the stream, so the
# bridge_daemon on_stream_event("start") guard fires too early to clear the flag.
_REANCHOR_AUTO_CLEAR_S = 5.0

logger = logging.getLogger(__name__)
_reanchor_times: deque[float] = deque(maxlen=1000)


def _patch_status(status, updates: dict) -> None:
    """Apply several status keys as one update.

    The store makes this indivisible; plain dicts (built directly by tests)
    fall back to a plain update.
    """
    patch = getattr(status, "patch", None)
    if callable(patch):
        patch(updates)
    else:
        status.update(updates)


def _record_reanchor_status(status: StatusStore | dict, *, sync_error_ms: float | None = None) -> None:
    """Update rolling re-anchor status from a structured or log fallback event."""
    now_mono = time.monotonic()
    _reanchor_times.append(now_mono)
    while _reanchor_times and now_mono - _reanchor_times[0] > 1800:
        _reanchor_times.popleft()
    count = status.get("reanchor_count", 0) + 1
    updates = {
        "reanchor_count": count,
        "reanchor_count_session": count,
        "reanchor_count_5m": sum(1 for value in _reanchor_times if now_mono - value <= 300),
        "reanchor_count_30m": len(_reanchor_times),
        "reanchoring": True,
        "last_reanchor_monotonic": now_mono,
        "last_reanchor_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if sync_error_ms is not None:
        updates["last_sync_error_ms"] = round(abs(float(sync_error_ms)), 3)
    _patch_status(status, updates)


def _observe_structured_reanchor(audio_handler: object, status: StatusStore | dict) -> bool:
    """Count a new AudioPlayer re-anchor marker without parsing log wording."""
    try:
        marker = int(getattr(audio_handler, "_last_reanchor_loop_time_us", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    previous = getattr(audio_handler, "_bridge_observed_reanchor_marker_us", None)
    try:
        audio_handler._bridge_observed_reanchor_marker_us = marker  # type: ignore[attr-defined]
    except Exception:
        return False
    if marker <= 0 or marker == previous:
        return False
    try:
        sync_error_ms = float(getattr(audio_handler, "_sync_error_filtered_us", 0.0)) / 1000.0
    except (TypeError, ValueError, OverflowError):
        sync_error_ms = None
    _record_reanchor_status(status, sync_error_ms=sync_error_ms)
    return True


class _JsonLineHandler(logging.Handler):
    """Emit log records as versioned JSON lines on stdout."""

    def __init__(self) -> None:
        super().__init__()
        self._status: StatusStore | dict | None = None
        self._on_status_change: object = None

    def set_status(self, status: StatusStore | dict, on_status_change) -> None:
        """Attach the shared status dict so re-anchor events update it."""
        self._status = status
        self._on_status_change = on_status_change

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            # Detect re-anchor log message from sendspin/audio.py
            if self._status is not None and _REANCHOR_MSG in msg:
                # Extract sync error value if present: "Sync error 123.4 ms too large; re-anchoring"
                sync_error_ms = None
                if _SYNC_ERROR_PREFIX in msg:
                    try:
                        after = msg.split(_SYNC_ERROR_PREFIX, 1)[1]
                        sync_error_ms = float(after.split()[0])
                    except (IndexError, ValueError):
                        pass  # best-effort parse inside log handler
                _record_reanchor_status(self._status, sync_error_ms=sync_error_ms)
                if callable(self._on_status_change):
                    try:
                        self._on_status_change()
                    except Exception:
                        pass  # cannot log inside log handler
            line = json.dumps(build_log_envelope(level=record.levelname.lower(), name=record.name, msg=msg))
            _write_line(line)
        except Exception:
            pass  # cannot log inside log handler


_json_handler = _JsonLineHandler()


def _setup_logging() -> None:
    root = logging.getLogger()
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(_json_handler)


# ---------------------------------------------------------------------------
# Status emission
# ---------------------------------------------------------------------------

_last_status_json: str = ""
#: Guards the emission de-duplication below.  The status itself is owned by
#: a :class:`StatusStore` and needs no lock here.
_emit_dedup_lock = threading.Lock()

#: How long the daemon may take to shut down on its own before it is cancelled.
_SHUTDOWN_GRACE_S = 3.0
# Serialises EVERY write to stdout.  Status emissions (asyncio callbacks), log
# lines (any thread) and error envelopes otherwise race and interleave partial
# lines, corrupting the parent's JSON-line parser.
_stdout_lock = threading.Lock()
_background_tasks: set[asyncio.Task] = set()


def _log_background_task_result(task: asyncio.Future, label: str) -> None:
    """Log a background-task failure without treating cancellation as an error."""
    if task.cancelled():
        return
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return
    if error is not None:
        logger.debug("%s error: %s", label, error)


def _write_line(payload: str) -> None:
    """Write one JSON line to stdout atomically w.r.t. other writers."""
    with _stdout_lock:
        print(payload, flush=True)


def _snapshot_status(status) -> dict:
    """Return a private copy of the live status.

    A :class:`StatusStore` answers with a whole state under its own lock.
    This used to retry five times on ``dictionary changed size during
    iteration``, which ``dict(some_dict)`` cannot actually raise under the
    GIL — the loop only ever protected a non-dict mapping, and the tear that
    did happen (a group of keys written one at a time) it could not have
    caught.  ``patch`` prevents that one at the source.  Plain dicts are
    still accepted for the tests that build one directly.
    """
    snapshot = getattr(status, "snapshot", None)
    if callable(snapshot):
        return snapshot()
    return dict(status)


def _filter_supported_daemon_args_kwargs(daemon_args_cls, kwargs: dict[str, object]) -> dict[str, object]:
    """Keep only kwargs supported by the installed sendspin DaemonArgs signature."""
    return filter_supported_call_kwargs(daemon_args_cls, kwargs)


def _select_audio_output_device(devices: list, *, target_sink: str | None = None):
    """Choose an output that honors the per-process PulseAudio target sink."""
    if target_sink:
        pulse_device = next(
            (device for device in devices if str(getattr(device, "name", "")).strip().lower() == "pulse"),
            None,
        )
        if pulse_device is not None:
            return pulse_device
    return next((device for device in devices if getattr(device, "is_default", False)), None) or (
        devices[0] if devices else None
    )


def _str_default(obj) -> str:
    """JSON default: convert non-serialisable values to their string repr."""
    return str(obj)


def _emit_status(status: StatusStore | dict) -> None:
    """Serialize status dict and write to stdout as a single JSON line.

    De-duplicates: if the serialized payload is identical to the last emission,
    the write is skipped to avoid flooding the parent with no-op updates.
    """
    global _last_status_json
    # Serialise a private snapshot so a concurrent daemon mutation can't change
    # the dict's size mid-``json.dumps``.
    payload = json.dumps(build_status_envelope(_snapshot_status(status)), default=_str_default, sort_keys=True)
    with _emit_dedup_lock:
        if payload == _last_status_json:
            return
        _last_status_json = payload
    _write_line(payload)


def _emit_error(error_code: str, message: str, *, details: dict[str, object] | None = None) -> None:
    """Serialize a structured error envelope for the parent process."""
    payload_details = dict(details or {})
    payload_details.setdefault("at", datetime.now(tz=timezone.utc).isoformat())
    payload = json.dumps(
        build_error_envelope(error_code, message, details=payload_details),
        default=_str_default,
        sort_keys=True,
    )
    _write_line(payload)


# ---------------------------------------------------------------------------
# stdin command reader
# ---------------------------------------------------------------------------


async def _reanchor_watcher(status: StatusStore | dict, on_status_change, stop_event: asyncio.Event) -> None:
    """Periodically clear the reanchoring flag once it has been set for long enough.

    sendspin logs "re-anchoring" AFTER restarting the stream, so the
    bridge_daemon on_stream_event("start") guard fires too early to clear it.
    This watcher checks every second; if no new re-anchor has occurred for
    _REANCHOR_AUTO_CLEAR_S seconds it clears the flag and notifies.
    """
    while not stop_event.is_set():
        await asyncio.sleep(1.0)
        if status.get("reanchoring") and status.get("last_reanchor_monotonic") is not None:
            age = time.monotonic() - status["last_reanchor_monotonic"]
            if age >= _REANCHOR_AUTO_CLEAR_S:
                status["reanchoring"] = False
                if callable(on_status_change):
                    try:
                        on_status_change()
                    except Exception as exc:
                        logger.debug("reanchor auto-clear callback failed: %s", exc)


async def _timing_telemetry_watcher(
    daemon,
    status: StatusStore | dict,
    on_status_change,
    stop_event: asyncio.Event,
    *,
    poll_interval: float = 1.0,
) -> None:
    """Publish bounded-rate Sendspin timing metrics through status IPC."""
    from sendspin_bridge.services.audio.timing_telemetry import collect_timing_snapshot

    next_metrics_at = 0.0
    failure_reported = False
    while not stop_event.is_set():
        try:
            audio_handler = getattr(daemon, "_audio_handler", None)
            client = getattr(daemon, "_client", None)
            changed = False
            if audio_handler is not None:
                changed = _observe_structured_reanchor(audio_handler, status)
            now = time.monotonic()
            if audio_handler is not None and client is not None and now >= next_metrics_at:
                snapshot = collect_timing_snapshot(audio_handler, client)
                _patch_status(status, snapshot)
                changed = True
                next_metrics_at = now + (5.0 if status.get("playing") else 20.0)
            if changed and callable(on_status_change):
                on_status_change()
            failure_reported = False
        except Exception as exc:
            log = logger.debug if failure_reported else logger.warning
            log("Timing telemetry update failed; playback will continue: %s", exc)
            failure_reported = True
        await asyncio.sleep(poll_interval)


async def _startup_sink_routing_watcher(
    status: StatusStore | dict, sink_name: str, stop_event: asyncio.Event, player_name: str
) -> None:
    """Correct PA sink routing whenever this daemon starts an audio stream.

    PulseAudio may ignore ``PULSE_SINK`` and route our sink-input to the
    default sink instead (module-stream-restore / default-sink override).
    On every ``audio_streaming`` rising edge, move this process' sink-inputs
    back to the intended sink.  The watcher must remain alive because a first
    stream can start long after the daemon, and WirePlumber can restore a
    stale target again for a later stream.
    """
    _logger = logging.getLogger(__name__)
    from sendspin_bridge.services.audio.pulse import amove_pid_sink_inputs

    was_streaming = False
    while not stop_event.is_set():
        await asyncio.sleep(0.5)
        if stop_event.is_set():
            break
        is_streaming = bool(status.get("audio_streaming"))
        if is_streaming and not was_streaming:
            try:
                moved = await amove_pid_sink_inputs(os.getpid(), sink_name)
                if moved:
                    _logger.info("[%s] Corrected %d sink-input(s) → %s", player_name, moved, sink_name)
            except Exception as exc:
                _logger.debug("[%s] Sink routing correction failed: %s", player_name, exc)
        was_streaming = is_streaming


async def _read_commands(daemon_ref: list, stop_event: asyncio.Event, *, bt_sink_name: str | None = None) -> None:
    """Read JSON commands from stdin and dispatch them."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while not stop_event.is_set():
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        except TimeoutError:
            continue
        if not line:
            break
        try:
            cmd = parse_command_envelope(json.loads(line.decode().strip()))
        except (json.JSONDecodeError, ValueError):
            continue
        if cmd is None:
            continue

        protocol_version = cmd.protocol_version
        if cmd.raw.get(IPC_PROTOCOL_VERSION_KEY) is not None and protocol_version != IPC_PROTOCOL_VERSION:
            logger.warning(
                "Received IPC command with protocol_version=%r; attempting compatible parse",
                cmd.raw.get(IPC_PROTOCOL_VERSION_KEY),
            )

        # Decoding is where a message becomes a command: the clamps travel
        # with the value, and a verb this build cannot honour is reported
        # rather than falling off the end of the dispatch ladder in silence.
        try:
            decode_command(cmd.raw)
        except UnknownCommand as exc:
            logger.warning("Rejecting IPC command: %s", exc)
            _emit_error("unknown_command", str(exc), details={"cmd": cmd.cmd})
            continue
        except InvalidCommand as exc:
            logger.warning("Rejecting IPC command: %s", exc)
            _emit_error("invalid_command", str(exc), details={"cmd": cmd.cmd})
            continue

        if cmd.cmd == "stop":
            stop_event.set()
        elif cmd.cmd in ("pause", "play"):
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and daemon._client and daemon._client.connected:
                from aiosendspin.models.types import MediaCommand

                mc = MediaCommand.PAUSE if cmd.cmd == "pause" else MediaCommand.PLAY
                _task = asyncio.ensure_future(daemon._client.send_group_command(mc))
                _background_tasks.add(_task)
                _task.add_done_callback(_background_tasks.discard)
                _task.add_done_callback(lambda t: _log_background_task_result(t, "send_group_command"))
        elif cmd.cmd == "set_volume":
            daemon = daemon_ref[0] if daemon_ref else None
            value = cmd.payload.get("value")
            if daemon and value is not None:
                try:
                    vol = max(0, min(100, int(value)))
                except (ValueError, TypeError):
                    logger.warning("Invalid volume value: %s", value)
                    continue
                daemon._bridge_status["volume"] = vol
                daemon._notify()
        elif cmd.cmd == "set_mute":
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and "muted" in cmd.payload:
                daemon._bridge_status["muted"] = bool(cmd.payload["muted"])
                daemon._notify()
        elif cmd.cmd == "reconnect":
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and getattr(daemon, "_client", None):
                delay = float(cmd.payload.get("delay", 0))

                async def _delayed_reconnect(_d=daemon, _delay=delay):
                    await _d._client.disconnect()
                    if _delay > 0:
                        # Give MA time to process ClientRemovedEvent and unregister
                        # the old player before the auto-reconnect sends a new client_hello
                        await asyncio.sleep(_delay)

                _reconnect_task = asyncio.ensure_future(_delayed_reconnect())
                _background_tasks.add(_reconnect_task)
                _reconnect_task.add_done_callback(_background_tasks.discard)
                _reconnect_task.add_done_callback(lambda t: _log_background_task_result(t, "reconnect"))
        elif cmd.cmd == "set_log_level":
            level_name = str(cmd.payload.get("level", "INFO")).upper()
            if level_name not in _VALID_LOG_LEVELS:
                logger.warning("Invalid log level requested: %s", level_name)
                continue
            logging.getLogger().setLevel(getattr(logging, level_name))
        elif cmd.cmd == "set_static_delay_ms":
            daemon = daemon_ref[0] if daemon_ref else None
            raw_value = cmd.payload.get("value")
            try:
                delay_ms = float(raw_value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                logger.warning("Invalid static_delay_ms value: %r", raw_value)
                continue
            # Clamp to the same range as config.schema.json.
            delay_ms = max(0.0, min(5000.0, delay_ms))
            client = getattr(daemon, "_client", None) if daemon else None
            setter = getattr(client, "set_static_delay_ms", None) if client else None
            applied = False
            if callable(setter):
                try:
                    setter(delay_ms)
                    applied = True
                except Exception as exc:
                    logger.warning("set_static_delay_ms failed: %s", exc)
            else:
                logger.warning("set_static_delay_ms not supported by current sendspin client — value ignored")
            # Only refresh the daemon-level cache if the local apply actually
            # succeeded. The cache feeds _create_client(self._static_delay_ms)
            # on the next server reconnect; updating it after a failed/ignored
            # apply would silently retry the broken value across reconnects,
            # which contradicts the "value ignored" / "failed" log line we
            # just emitted.
            if applied and daemon is not None:
                daemon._static_delay_ms = delay_ms
                # Keep the shared status snapshot in sync with the live
                # aiosendspin client.  Otherwise the next unrelated status
                # notification republishes the previous value and the parent
                # process persists that stale value back to config.
                daemon._bridge_status["static_delay_ms"] = round(delay_ms)
                daemon._notify()
            # Push the updated player state to MA so its slider repaints.
            # aiosendspin.set_static_delay_ms updates _static_delay_us locally
            # but does NOT auto-emit client/state — explicit push is required.
            # Reuse the daemon's tracked _last_player_state instead of
            # hard-coding SYNCHRONIZED so future state machinery can drive
            # this without touching the IPC layer.
            if applied and client is not None and getattr(client, "connected", False):
                try:
                    from aiosendspin.models.types import PlayerStateType

                    cur_volume = int(getattr(daemon, "_volume", 100))
                    cur_muted = bool(getattr(daemon, "_muted", False))
                    cur_state = getattr(daemon, "_last_player_state", None) or PlayerStateType.SYNCHRONIZED
                    try:
                        await client.send_player_state(
                            available=True,
                            volume=cur_volume,
                            muted=cur_muted,
                        )
                    except TypeError:
                        await client.send_player_state(
                            state=cur_state,
                            volume=cur_volume,
                            muted=cur_muted,
                        )
                except Exception as exc:
                    logger.warning("Failed to push static_delay_ms to MA: %s", exc)
        elif cmd.cmd in ("set_required_lead_time_ms", "set_min_buffer_ms"):
            daemon = daemon_ref[0] if daemon_ref else None
            client = getattr(daemon, "_client", None) if daemon else None
            if daemon is None or client is None:
                logger.warning("%s ignored — client not available", cmd.cmd)
                continue
            raw_value = cmd.payload.get("value")
            if raw_value is None:
                logger.warning("Invalid %s value: %r", cmd.cmd, raw_value)
                continue
            try:
                value_ms = max(0.0, min(30000.0, float(raw_value)))
            except (TypeError, ValueError):
                logger.warning("Invalid %s value: %r", cmd.cmd, raw_value)
                continue
            attr = "required_lead_time_ms" if cmd.cmd.startswith("set_required") else "min_buffer_ms"
            setter = getattr(client, f"set_{attr}", None) if client else None
            if not callable(setter):
                logger.warning("%s not supported by current sendspin client", cmd.cmd)
                continue
            try:
                setter(value_ms)
                setattr(daemon, f"_{attr}", value_ms)
                daemon._bridge_status[attr] = round(value_ms)
                daemon._notify()
                if getattr(client, "connected", False):
                    from aiosendspin.models.types import PlayerStateType

                    volume = int(getattr(daemon, "_volume", 100))
                    muted = bool(getattr(daemon, "_muted", False))
                    try:
                        await client.send_player_state(available=True, volume=volume, muted=muted)
                    except TypeError:
                        await client.send_player_state(
                            state=getattr(daemon, "_last_player_state", None) or PlayerStateType.SYNCHRONIZED,
                            volume=volume,
                            muted=muted,
                        )
            except Exception as exc:
                logger.warning("%s failed: %s", cmd.cmd, exc)
        elif cmd.cmd == "transport":
            daemon = daemon_ref[0] if daemon_ref else None
            action = str(cmd.payload.get("action", "")).strip()
            if not daemon or not daemon._client or not daemon._client.connected:
                logger.warning("Transport command %r ignored — client not connected", action)
                continue
            from aiosendspin.models.types import MediaCommand

            _TRANSPORT_MAP = {mc.value: mc for mc in MediaCommand}
            mc = _TRANSPORT_MAP.get(action)  # type: ignore[assignment]
            if mc is None:
                logger.warning("Unknown transport action: %s", action)
                continue
            kwargs: dict = {}
            value = cmd.payload.get("value")
            if mc == MediaCommand.VOLUME and value is not None:
                try:
                    kwargs["volume"] = max(0, min(100, int(value)))
                except (ValueError, TypeError):
                    logger.warning("Invalid volume value for transport: %s", value)
                    continue
            elif mc == MediaCommand.MUTE and value is not None:
                kwargs["mute"] = bool(value)
            _task = asyncio.ensure_future(daemon._client.send_group_command(mc, **kwargs))
            _background_tasks.add(_task)
            _task.add_done_callback(_background_tasks.discard)
            _task.add_done_callback(lambda t, _a=action: _log_background_task_result(t, f"transport {_a}"))
        elif cmd.cmd == "set_standby":
            daemon = daemon_ref[0] if daemon_ref else None
            sink = cmd.payload.get("sink")
            target = sink or bt_sink_name
            if daemon is not None and hasattr(daemon, "retarget_sink"):
                daemon.retarget_sink(target)
                logger.info("Player sink retargeted to %s", target)
        elif cmd.cmd == "open_pairing_window":
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon is not None and hasattr(daemon, "open_pairing_window"):
                daemon.open_pairing_window()
                logger.info("Opened Sendspin pairing window")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def _run(params: dict) -> None:
    from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

    player_name: str = params["player_name"]
    client_id: str = params["client_id"]
    listen_port: int = params["listen_port"]
    server_url: str | None = params.get("url")
    static_delay_ms: float = params.get("static_delay_ms", 0.0)
    required_lead_time_ms: float = params.get("required_lead_time_ms", 250.0)
    min_buffer_ms: float = params.get("min_buffer_ms", 250.0)
    bluetooth_sink_name: str | None = params.get("bluetooth_sink_name")
    initial_volume: int = params.get("volume", 100)
    initial_muted: bool = bool(params.get("muted", False))
    # Per-device identity surfaced to MA in client/hello.device_info. Empty
    # strings here mean the parent couldn't resolve the BT props (no D-Bus
    # path yet, BlueZ silent on Modalias, etc.) — BridgeDaemon falls back
    # to the bridge-wide identity so MA always sees something.
    bt_product_name: str = params.get("bt_product_name", "") or ""
    bt_manufacturer: str = params.get("bt_manufacturer", "") or ""
    # sanitize client_id for safe path usage
    safe_id = re.sub(r"[^a-zA-Z0-9_:-]", "_", client_id)
    settings_dir: str = params.get("settings_dir", f"/tmp/sendspin-{safe_id}")
    # CRITICAL: Security — path traversal guard. settings_dir comes from IPC params;
    # must resolve under /tmp/ to prevent directory escape via ../
    resolved = str(Path(settings_dir).resolve())
    if not resolved.startswith("/tmp/"):
        settings_dir = f"/tmp/sendspin-{safe_id}"
    logger = logging.getLogger(__name__)
    # The handshake is the one point where a version mismatch can still be
    # reported cleanly.  Logging it and running on left a mixed-version pair
    # talking a contract neither side had agreed to, and the failure surfaced
    # much later as commands that quietly did nothing.  A parent that omits
    # the key predates it and stays compatible.
    if not is_compatible_protocol_version(params.get(IPC_PROTOCOL_VERSION_KEY)):
        message = (
            f"parent speaks IPC protocol {params.get(IPC_PROTOCOL_VERSION_KEY)!r}, "
            f"this daemon speaks {IPC_PROTOCOL_VERSION}"
        )
        _emit_error(
            "incompatible_protocol_version",
            message,
            details={"parent": params.get(IPC_PROTOCOL_VERSION_KEY), "daemon": IPC_PROTOCOL_VERSION},
        )
        logger.error("[%s] Refusing to start: %s", player_name, message)
        sys.exit(1)

    logger.info("[%s] Player sink=%s", player_name, bluetooth_sink_name or "default")

    config_dir = Path(os.environ.get("CONFIG_DIR", "/config")).resolve()
    identity_path = (config_dir / "identity" / f"{safe_id}.key").resolve()
    pairing_path = (config_dir / "pairing" / f"{safe_id}.json").resolve()
    if not str(identity_path).startswith(str(config_dir)):
        identity_path = config_dir / "identity" / "default.key"
    if not str(pairing_path).startswith(str(config_dir)):
        pairing_path = config_dir / "pairing" / "default.json"

    args = DaemonArgs(
        client_id=client_id,
        client_name=player_name,
        url=server_url,
        listen_port=listen_port,
        static_delay_ms=static_delay_ms,
        initial_volume=initial_volume,
        initial_muted=initial_muted,
        sink_name=bluetooth_sink_name,
        pulse_latency_msec=int(os.environ.get("PULSE_LATENCY_MSEC", "600") or 600),
        identity_path=str(identity_path),
        pairing_store_path=str(pairing_path),
    )

    status = StatusStore(
        {
            "player_name": player_name,
            "connected": False,
            "playing": False,
            "server_connected": False,
            "server_connected_at": None,
            "server_url": server_url,
            "current_track": None,
            "current_artist": None,
            "volume": initial_volume,
            "muted": initial_muted,
            "audio_format": None,
            "group_name": None,
            "group_id": None,
            "connected_server_url": None,
            "last_error": None,
            "reanchor_count": 0,
            "reanchor_count_session": 0,
            "reanchor_count_5m": 0,
            "reanchor_count_30m": 0,
            "reanchoring": False,
            "last_reanchor_at": None,
            "last_sync_error_ms": None,
            "audio_streaming": False,
            "sink_muted": False,
            "track_progress_ms": None,
            "track_duration_ms": None,
            "playback_speed": None,
            "current_album": None,
            "current_album_artist": None,
            "artwork_url": None,
            "track_year": None,
            "track_number": None,
            "shuffle": None,
            "repeat_mode": None,
            "supported_commands": None,
            "group_volume": None,
            "group_muted": None,
            "required_lead_time_ms": round(required_lead_time_ms),
            "min_buffer_ms": round(min_buffer_ms),
            "timing_metrics_available": False,
        }
    )

    # Emit initial status so parent knows subprocess is alive
    _emit_status(status)

    stop_event = asyncio.Event()
    daemon_ref: list = []

    def _on_status_change() -> None:
        _emit_status(status)

    # Wire the log handler so re-anchor log messages update status
    _json_handler.set_status(status, _on_status_change)

    daemon = BridgeDaemon(
        args=args,
        status=status,
        bluetooth_sink_name=bluetooth_sink_name,
        on_status_change=_on_status_change,
        bt_product_name=bt_product_name,
        bt_manufacturer=bt_manufacturer,
        required_lead_time_ms=required_lead_time_ms,
        min_buffer_ms=min_buffer_ms,
    )
    daemon_ref.append(daemon)

    cmd_task = asyncio.create_task(_read_commands(daemon_ref, stop_event, bt_sink_name=bluetooth_sink_name))
    daemon_task = asyncio.create_task(daemon.run())
    watcher_task = asyncio.create_task(_reanchor_watcher(status, _on_status_change, stop_event))
    timing_task = asyncio.create_task(_timing_telemetry_watcher(daemon, status, _on_status_change, stop_event))
    # Connection watchdog: surfaces a clear error when daemon cannot reach the server
    conn_watchdog_task = asyncio.create_task(daemon._connection_watchdog())
    # Correct PA sink routing once audio starts (PA may ignore PULSE_SINK).
    routing_task = None
    if bluetooth_sink_name:
        routing_task = asyncio.create_task(
            _startup_sink_routing_watcher(status, bluetooth_sink_name, stop_event, player_name)
        )

    # Wait until stdin closes, a stop command arrives, or the daemon exits.
    # Observability helpers are deliberately excluded: diagnostics must never
    # own the playback lifecycle if a metric collector fails unexpectedly.
    stop_task = asyncio.create_task(stop_event.wait())
    all_tasks = [cmd_task, daemon_task, stop_task]
    _done, pending = await asyncio.wait(
        all_tasks,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Cancel tracked fire-and-forget tasks
    for t in list(_background_tasks):
        t.cancel()
    _background_tasks.clear()

    auxiliary_tasks = [cmd_task, watcher_task, timing_task, conn_watchdog_task]
    if routing_task:
        auxiliary_tasks.append(routing_task)
    auxiliary_tasks.extend(t for t in pending if t not in auxiliary_tasks and t is not daemon_task)

    # The daemon task is given its grace period *before* anyone cancels it, so
    # it can say goodbye to the server and let PortAudio drain.  Observability
    # tasks have nothing to flush and go at once.
    await shut_down_tasks(
        primary=daemon_task,
        auxiliary=auxiliary_tasks,
        grace_s=_SHUTDOWN_GRACE_S,
        on_error=lambda label, exc: logger.warning("[%s] %s task failed during shutdown: %s", player_name, label, exc),
    )


def main() -> None:
    _setup_logging()
    if len(sys.argv) < 2:
        _write_line(
            json.dumps(
                with_protocol_version(
                    {"type": "log", "level": "error", "msg": "Usage: daemon_process.py <json_params>"}
                )
            )
        )
        _emit_error("missing_params", "Usage: daemon_process.py <json_params>")
        sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        _write_line(
            json.dumps(with_protocol_version({"type": "log", "level": "error", "msg": f"Invalid JSON params: {e}"}))
        )
        _emit_error("invalid_params_json", f"Invalid JSON params: {e}")
        sys.exit(1)

    asyncio.run(_run(params))


if __name__ == "__main__":
    main()
