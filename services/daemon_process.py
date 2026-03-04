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
import sys

# ---------------------------------------------------------------------------
# Minimal JSON-line log handler (forwarded to parent via stdout)
# ---------------------------------------------------------------------------

_LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

# Pattern that audio.py logs when re-anchoring is triggered
_REANCHOR_MSG = "re-anchoring"
_SYNC_ERROR_PREFIX = "Sync error "


class _JsonLineHandler(logging.Handler):
    """Emit log records as {"type":"log", ...} JSON lines on stdout."""

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
                # Extract sync error value if present: "Sync error 123.4 ms too large; re-anchoring"
                if _SYNC_ERROR_PREFIX in msg:
                    try:
                        after = msg.split(_SYNC_ERROR_PREFIX, 1)[1]
                        self._status["last_sync_error_ms"] = float(after.split()[0])
                    except (IndexError, ValueError):
                        pass
                if callable(self._on_status_change):
                    try:
                        self._on_status_change()
                    except Exception:
                        pass
            line = json.dumps(
                {
                    "type": "log",
                    "level": record.levelname.lower(),
                    "name": record.name,
                    "msg": msg,
                }
            )
            print(line, flush=True)
        except Exception:
            pass


_json_handler = _JsonLineHandler()


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(_json_handler)


# ---------------------------------------------------------------------------
# Status emission
# ---------------------------------------------------------------------------


def _emit_status(status: dict) -> None:
    """Serialize status dict and write to stdout as a single JSON line."""
    # Emit only JSON-serialisable values
    serialisable = {}
    for k, v in status.items():
        try:
            json.dumps(v)
            serialisable[k] = v
        except TypeError:
            serialisable[k] = str(v)
    print(json.dumps({"type": "status", **serialisable}), flush=True)


# ---------------------------------------------------------------------------
# stdin command reader
# ---------------------------------------------------------------------------


async def _read_commands(daemon_ref: list, stop_event: asyncio.Event) -> None:
    """Read JSON commands from stdin and dispatch them."""
    loop = asyncio.get_event_loop()
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

        if cmd.get("cmd") == "stop":
            stop_event.set()
        elif cmd.get("cmd") == "set_volume":
            daemon = daemon_ref[0] if daemon_ref else None
            if daemon and cmd.get("value") is not None:
                vol = int(cmd["value"])
                daemon._bridge_status["volume"] = vol
                daemon._sync_bt_sink_volume(vol)


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
    settings_dir: str = params.get("settings_dir", f"/tmp/sendspin-{client_id}")
    preferred_format_str: str | None = params.get("preferred_format")

    logger = logging.getLogger(__name__)

    # Resolve audio device — use default since PULSE_SINK in env handles routing
    devices = query_devices()
    audio_device = next((d for d in devices if d.is_default), None)
    if audio_device is None:
        audio_device = devices[0] if devices else None
    if audio_device is None:
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

    args = DaemonArgs(
        audio_device=audio_device,
        client_id=client_id,
        client_name=player_name,
        settings=settings,
        url=server_url,
        static_delay_ms=static_delay_ms,
        listen_port=listen_port,
        use_mpris=False,  # MPRIS requires D-Bus session which subprocesses don't have
        use_hardware_volume=False,
        preferred_format=preferred_fmt,
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
        "muted": False,
        "audio_format": None,
        "group_name": None,
        "group_id": None,
        "connected_server_url": None,
        "last_error": None,
        "reanchor_count": 0,
        "reanchoring": False,
        "last_sync_error_ms": None,
        "audio_streaming": False,
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

    cmd_task = asyncio.create_task(_read_commands(daemon_ref, stop_event))
    daemon_task = asyncio.create_task(daemon.run())

    # Wait until stop command or daemon exits
    _done, pending = await asyncio.wait(
        [cmd_task, daemon_task, asyncio.create_task(stop_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )

    daemon_task.cancel()
    cmd_task.cancel()
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
            json.dumps({"type": "log", "level": "error", "msg": "Usage: daemon_process.py <json_params>"}), flush=True
        )
        sys.exit(1)
    try:
        params = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(json.dumps({"type": "log", "level": "error", "msg": f"Invalid JSON params: {e}"}), flush=True)
        sys.exit(1)

    asyncio.run(_run(params))


if __name__ == "__main__":
    main()
