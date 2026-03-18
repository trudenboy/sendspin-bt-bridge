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
import time
from datetime import datetime, timezone
from pathlib import Path

from services.ipc_protocol import (
    IPC_PROTOCOL_VERSION,
    IPC_PROTOCOL_VERSION_KEY,
    parse_protocol_version,
    with_protocol_version,
)
from services.sendspin_compat import filter_supported_call_kwargs

# ---------------------------------------------------------------------------
# PyAV compatibility: older PyAV (<13) has no AudioLayout.nb_channels.
# The sendspin decoder uses frame.layout.nb_channels, so we monkey-patch
# the decoder's _append_frame_to_pcm to use len(layout.channels) instead.
# AudioLayout is a C extension type (immutable), so we replace the method.
# ---------------------------------------------------------------------------
try:
    import av.audio.layout as _av_layout

    if not hasattr(_av_layout.AudioLayout("stereo"), "nb_channels"):
        import sendspin.decoder as _sd

        def _append_compat(self, frame, output):  # type: ignore[no-untyped-def]
            """Patched: len(layout.channels) instead of layout.nb_channels."""
            src_bits = frame.format.bits
            src_bytes_per_sample = frame.format.bytes
            samples_per_channel = frame.samples
            channel_count = len(frame.layout.channels)  # <-- patched line
            total_samples = samples_per_channel * channel_count
            exact_src_bytes = total_samples * src_bytes_per_sample

            if src_bits not in (16, 32):
                _sd.logger.warning("Unsupported FLAC sample format: %s", frame.format.name)
                output.extend(memoryview(frame.planes[0])[:exact_src_bytes])
                return

            self._samples_decoded += total_samples

            if not frame.format.is_planar:
                self._append_packed_frame(
                    output,
                    memoryview(frame.planes[0])[:exact_src_bytes],
                    total_samples,
                    src_bits,
                )
                return

            self._append_planar_frame(output, frame, samples_per_channel, channel_count, src_bits)

        _sd.FlacDecoder._append_frame_to_pcm = _append_compat  # type: ignore[assignment]
except Exception:
    pass

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


class _JsonLineHandler(logging.Handler):
    """Emit log records as versioned JSON lines on stdout."""

    def __init__(self) -> None:
        super().__init__()
        self._status: dict | None = None
        self._on_status_change: object = None

    def set_status(self, status: dict, on_status_change) -> None:
        """Attach the shared status dict so re-anchor events update it."""
        self._status = status
        self._on_status_change = on_status_change

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            # Detect re-anchor log message from sendspin/audio.py
            if self._status is not None and _REANCHOR_MSG in msg:
                self._status["reanchor_count"] = self._status.get("reanchor_count", 0) + 1
                self._status["reanchoring"] = True
                self._status["last_reanchor_at"] = time.monotonic()
                # Extract sync error value if present: "Sync error 123.4 ms too large; re-anchoring"
                if _SYNC_ERROR_PREFIX in msg:
                    try:
                        after = msg.split(_SYNC_ERROR_PREFIX, 1)[1]
                        self._status["last_sync_error_ms"] = float(after.split()[0])
                    except (IndexError, ValueError):
                        pass  # best-effort parse inside log handler
                if callable(self._on_status_change):
                    try:
                        self._on_status_change()
                    except Exception:
                        pass  # cannot log inside log handler
            line = json.dumps(
                with_protocol_version(
                    {
                        "type": "log",
                        "level": record.levelname.lower(),
                        "name": record.name,
                        "msg": msg,
                    }
                )
            )
            print(line, flush=True)
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


def _filter_supported_daemon_args_kwargs(daemon_args_cls, kwargs: dict[str, object]) -> dict[str, object]:
    """Keep only kwargs supported by the installed sendspin DaemonArgs signature."""
    return filter_supported_call_kwargs(daemon_args_cls, kwargs)


def _str_default(obj) -> str:
    """JSON default: convert non-serialisable values to their string repr."""
    return str(obj)


def _emit_status(status: dict) -> None:
    """Serialize status dict and write to stdout as a single JSON line.

    De-duplicates: if the serialized payload is identical to the last emission,
    the write is skipped to avoid flooding the parent with no-op updates.
    """
    global _last_status_json
    payload = json.dumps(with_protocol_version({"type": "status", **status}), default=_str_default, sort_keys=True)
    if payload == _last_status_json:
        return
    _last_status_json = payload
    print(payload, flush=True)


def _emit_error(error_code: str, message: str, *, details: dict[str, object] | None = None) -> None:
    """Serialize a structured error envelope for the parent process."""
    payload_details = dict(details or {})
    payload_details.setdefault("at", datetime.now(tz=timezone.utc).isoformat())
    payload = json.dumps(
        with_protocol_version(
            {
                "type": "error",
                "error_code": error_code,
                "message": message,
                "details": payload_details,
            }
        ),
        default=_str_default,
        sort_keys=True,
    )
    print(payload, flush=True)


# ---------------------------------------------------------------------------
# stdin command reader
# ---------------------------------------------------------------------------


async def _reanchor_watcher(status: dict, on_status_change, stop_event: asyncio.Event) -> None:
    """Periodically clear the reanchoring flag once it has been set for long enough.

    sendspin logs "re-anchoring" AFTER restarting the stream, so the
    bridge_daemon on_stream_event("start") guard fires too early to clear it.
    This watcher checks every second; if no new re-anchor has occurred for
    _REANCHOR_AUTO_CLEAR_S seconds it clears the flag and notifies.
    """
    while not stop_event.is_set():
        await asyncio.sleep(1.0)
        if status.get("reanchoring") and status.get("last_reanchor_at") is not None:
            age = time.monotonic() - status["last_reanchor_at"]
            if age >= _REANCHOR_AUTO_CLEAR_S:
                status["reanchoring"] = False
                if callable(on_status_change):
                    try:
                        on_status_change()
                    except Exception as exc:
                        logger.debug("reanchor auto-clear callback failed: %s", exc)


# Seconds after audio_streaming=True before unmuting the sink
_STARTUP_UNMUTE_DELAY_S = 1.5


async def _startup_unmute_watcher(
    status: dict, sink_name: str, stop_event: asyncio.Event, player_name: str, on_status_change=None
) -> None:
    """Wait for audio to stabilize after startup, then unmute the PA sink.

    The sink is muted before BridgeDaemon starts to hide re-anchor clicks,
    format probing noise, and routing glitches. This watcher polls for
    audio_streaming=True, waits an additional stabilization delay, then unmutes.
    Times out after 60s — on timeout unmutes only if audio was streaming
    (avoids unmuting idle players that would immediately get muted again).

    Also corrects sink routing: PA may ignore PULSE_SINK and route the
    sink-input to the default sink instead (module-stream-restore or
    default-sink override). After audio starts we move our PID's
    sink-inputs to the correct sink.
    """
    _logger = logging.getLogger(__name__)
    from services.pulse import amove_pid_sink_inputs, aset_sink_mute

    streamed = False
    deadline = time.monotonic() + 15.0
    while not stop_event.is_set() and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        if status.get("audio_streaming"):
            streamed = True
            # Correct sink routing before unmuting — PA may have routed
            # our sink-input to the default sink instead of PULSE_SINK.
            try:
                moved = await amove_pid_sink_inputs(os.getpid(), sink_name)
                if moved:
                    _logger.info("[%s] Corrected %d sink-input(s) → %s", player_name, moved, sink_name)
            except Exception as exc:
                _logger.debug("[%s] Sink routing correction failed: %s", player_name, exc)
            _logger.info("[%s] Audio streaming, waiting %.1fs for stabilization", player_name, _STARTUP_UNMUTE_DELAY_S)
            await asyncio.sleep(_STARTUP_UNMUTE_DELAY_S)
            break

    if stop_event.is_set():
        return  # daemon shutting down, don't unmute

    if not streamed:
        _logger.info("[%s] Startup unmute timeout — no audio streamed, unmuting anyway", player_name)

    try:
        if await aset_sink_mute(sink_name, False):
            _logger.info("[%s] Unmuted sink %s (startup complete)", player_name, sink_name)
            status["sink_muted"] = False
            if on_status_change:
                on_status_change()
        else:
            _logger.warning("[%s] Failed to unmute sink %s", player_name, sink_name)
    except Exception as exc:
        _logger.warning("[%s] Error unmuting sink: %s", player_name, exc)


async def _read_commands(daemon_ref: list, stop_event: asyncio.Event) -> None:
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
            cmd = json.loads(line.decode().strip())
        except (json.JSONDecodeError, ValueError):
            continue

        protocol_version = parse_protocol_version(cmd.get(IPC_PROTOCOL_VERSION_KEY))
        if cmd.get(IPC_PROTOCOL_VERSION_KEY) is not None and protocol_version != IPC_PROTOCOL_VERSION:
            logger.warning(
                "Received IPC command with protocol_version=%r; attempting compatible parse",
                cmd.get(IPC_PROTOCOL_VERSION_KEY),
            )

        if cmd.get("cmd") == "stop":
            stop_event.set()
        elif cmd.get("cmd") in ("pause", "play"):
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and daemon._client and daemon._client.connected:
                from aiosendspin.models.types import MediaCommand

                mc = MediaCommand.PAUSE if cmd["cmd"] == "pause" else MediaCommand.PLAY
                _task = asyncio.ensure_future(daemon._client.send_group_command(mc))
                _task.add_done_callback(
                    lambda t: logger.debug("send_group_command error: %s", t.exception()) if t.exception() else None
                )
        elif cmd.get("cmd") == "set_volume":
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and cmd.get("value") is not None:
                try:
                    vol = max(0, min(100, int(cmd["value"])))
                except (ValueError, TypeError):
                    logger.warning("Invalid volume value: %s", cmd.get("value"))
                    continue
                daemon._bridge_status["volume"] = vol
                daemon._sync_bt_sink_volume(vol)
                daemon._notify()
        elif cmd.get("cmd") == "set_mute":
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and "muted" in cmd:
                daemon._bridge_status["muted"] = bool(cmd["muted"])
                daemon._notify()
        elif cmd.get("cmd") == "reconnect":
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and getattr(daemon, "_client", None):
                delay = float(cmd.get("delay", 0))

                async def _delayed_reconnect(_d=daemon, _delay=delay):
                    await _d._client.disconnect()
                    if _delay > 0:
                        # Give MA time to process ClientRemovedEvent and unregister
                        # the old player before the auto-reconnect sends a new client_hello
                        await asyncio.sleep(_delay)

                asyncio.ensure_future(_delayed_reconnect()).add_done_callback(
                    lambda t: logger.debug("reconnect error: %s", t.exception()) if t.exception() else None
                )
        elif cmd.get("cmd") == "set_log_level":
            level_name = str(cmd.get("level", "INFO")).upper()
            if level_name not in _VALID_LOG_LEVELS:
                logger.warning("Invalid log level requested: %s", level_name)
                continue
            logging.getLogger().setLevel(getattr(logging, level_name))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def _run(params: dict) -> None:
    from sendspin.audio import parse_audio_format, query_devices
    from sendspin.daemon.daemon import DaemonArgs
    from sendspin.settings import get_client_settings

    from services.bridge_daemon import BridgeDaemon

    player_name: str = params["player_name"]
    client_id: str = params["client_id"]
    listen_port: int = params["listen_port"]
    server_url: str | None = params.get("url")
    static_delay_ms: float = params.get("static_delay_ms", -500.0)
    bluetooth_sink_name: str | None = params.get("bluetooth_sink_name")
    initial_volume: int = params.get("volume", 100)
    initial_muted: bool = bool(params.get("muted", False))
    # sanitize client_id for safe path usage
    safe_id = re.sub(r"[^a-zA-Z0-9_:-]", "_", client_id)
    settings_dir: str = params.get("settings_dir", f"/tmp/sendspin-{safe_id}")
    # Verify resolved path stays under /tmp
    resolved = str(Path(settings_dir).resolve())
    if not resolved.startswith("/tmp/"):
        settings_dir = f"/tmp/sendspin-{safe_id}"
    preferred_format_str: str | None = params.get("preferred_format")
    protocol_version = parse_protocol_version(params.get(IPC_PROTOCOL_VERSION_KEY))

    logger = logging.getLogger(__name__)
    if params.get(IPC_PROTOCOL_VERSION_KEY) is not None and protocol_version != IPC_PROTOCOL_VERSION:
        logger.warning(
            "[%s] Started with protocol_version=%r; attempting compatible runtime behavior",
            player_name,
            params.get(IPC_PROTOCOL_VERSION_KEY),
        )

    # Resolve audio device — use default since PULSE_SINK in env handles routing
    devices = query_devices()
    audio_device = next((d for d in devices if d.is_default), None)
    if audio_device is None:
        audio_device = devices[0] if devices else None
    if audio_device is None:
        _emit_error("audio_output_missing", "No audio output device found")
        logger.error("No audio output device found")
        sys.exit(1)

    logger.info(
        "[%s] Using audio device %r (index %d) — PULSE_SINK=%s",
        player_name,
        audio_device.name,
        audio_device.index,
        os.environ.get("PULSE_SINK", "not set"),
    )

    settings = await get_client_settings("daemon", config_dir=settings_dir)
    settings.player_volume = initial_volume

    preferred_fmt = None
    if preferred_format_str:
        try:
            preferred_fmt = parse_audio_format(preferred_format_str)
        except Exception as e:
            logger.warning("[%s] Invalid preferred_format %r: %s", player_name, preferred_format_str, e)

    daemon_kwargs = _filter_supported_daemon_args_kwargs(
        DaemonArgs,
        {
            "audio_device": audio_device,
            "client_id": client_id,
            "client_name": player_name,
            "settings": settings,
            "url": server_url,
            "static_delay_ms": static_delay_ms,
            "listen_port": listen_port,
            "use_mpris": False,
            "use_hardware_volume": False,
            "preferred_format": preferred_fmt,
        },
    )
    if "use_hardware_volume" not in daemon_kwargs:
        logger.info("[%s] Installed sendspin DaemonArgs has no use_hardware_volume; skipping compat kwarg", player_name)
    args = DaemonArgs(
        **daemon_kwargs,
    )

    status: dict = {
        "player_name": player_name,
        "connected": False,
        "playing": False,
        "server_connected": False,
        "server_connected_at": None,
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
        "reanchoring": False,
        "last_reanchor_at": None,
        "last_sync_error_ms": None,
        "audio_streaming": False,
        "sink_muted": False,
        "track_progress_ms": None,
        "track_duration_ms": None,
    }

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
    )
    daemon_ref.append(daemon)

    # Mute the PA sink before audio starts to hide re-anchor clicks and routing glitches.
    # The _startup_unmute_watcher will unmute after audio_streaming becomes True + stabilization delay.
    _startup_muted = False
    if bluetooth_sink_name:
        try:
            from services.pulse import aset_sink_mute

            if await aset_sink_mute(bluetooth_sink_name, True):
                _startup_muted = True
                status["sink_muted"] = True
                logger.info("[%s] Muted sink %s during startup", player_name, bluetooth_sink_name)
                _on_status_change()
        except Exception as exc:
            logger.debug("[%s] Could not mute sink on startup: %s", player_name, exc)

    cmd_task = asyncio.create_task(_read_commands(daemon_ref, stop_event))
    daemon_task = asyncio.create_task(daemon.run())
    watcher_task = asyncio.create_task(_reanchor_watcher(status, _on_status_change, stop_event))
    unmute_task = None
    if _startup_muted and bluetooth_sink_name:
        unmute_task = asyncio.create_task(
            _startup_unmute_watcher(
                status, bluetooth_sink_name, stop_event, player_name, on_status_change=_on_status_change
            )
        )

    # Wait until stop command or daemon exits.
    # unmute_task is NOT in all_tasks — it's fire-and-forget so its completion
    # doesn't trigger FIRST_COMPLETED and kill the daemon.
    all_tasks = [cmd_task, daemon_task, watcher_task, asyncio.create_task(stop_event.wait())]
    _done, pending = await asyncio.wait(
        all_tasks,
        return_when=asyncio.FIRST_COMPLETED,
    )

    daemon_task.cancel()
    cmd_task.cancel()
    watcher_task.cancel()
    if unmute_task:
        unmute_task.cancel()
    for t in pending:
        t.cancel()

    # Wait for clean shutdown
    try:
        await asyncio.wait_for(asyncio.shield(daemon_task), timeout=3.0)
    except (asyncio.CancelledError, TimeoutError):
        pass


def main() -> None:
    _setup_logging()
    if len(sys.argv) < 2:
        print(
            json.dumps(
                with_protocol_version(
                    {"type": "log", "level": "error", "msg": "Usage: daemon_process.py <json_params>"}
                )
            ),
            flush=True,
        )
        _emit_error("missing_params", "Usage: daemon_process.py <json_params>")
        sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(
            json.dumps(with_protocol_version({"type": "log", "level": "error", "msg": f"Invalid JSON params: {e}"})),
            flush=True,
        )
        _emit_error("invalid_params_json", f"Invalid JSON params: {e}")
        sys.exit(1)

    asyncio.run(_run(params))


if __name__ == "__main__":
    main()
