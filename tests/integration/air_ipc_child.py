"""Test-only child for the air IPC pipe tests (``test_air_ipc_pipe.py``).

A real ``BridgeDaemon`` in measure mode with a real ``AirSensor`` over its own
tap, the real ``daemon_process._read_commands`` on stdin, the real telemetry
watcher publishing ``air_sensor`` and the real ``_emit_status`` writing to
stdout. No Sendspin client, audio device, Bluetooth or GStreamer.

Besides the daemon's status, each status line carries ``child_counters``:
the daemon's air intake counters and the stdin drop counters, so a test can
see drops that the sensor's own ``dropped_messages`` does not cover.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import tempfile
from pathlib import Path

from sendspin_bridge.services.ipc import daemon_process
from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

POLL_S = 0.1


async def _counters(daemon: BridgeDaemon, status: dict, stop: asyncio.Event) -> None:
    while not stop.is_set():
        status["child_counters"] = {
            **{f"air_{k}": v for k, v in daemon.air_counters.items()},
            **{f"stdin_{k}": v for k, v in daemon_process.stdin_drop_counters.items()},
        }
        daemon_process._emit_status(status)
        await asyncio.sleep(POLL_S)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        args = DaemonArgs(
            client_id="air-child",
            client_name="Air child",
            identity_path=str(Path(tmp) / "identity.key"),
            playout_mode="measure",
            air_sensor={"enabled": True, "latency_ms": None},
        )
        status: dict = {}
        daemon = BridgeDaemon(args, status, None)
        stop = asyncio.Event()
        daemon_ref = [daemon]
        reader = asyncio.create_task(daemon_process._read_commands(daemon_ref, stop))
        watcher = asyncio.create_task(
            daemon_process._timing_telemetry_watcher(
                daemon, status, lambda: daemon_process._emit_status(status), stop, poll_interval=POLL_S
            )
        )
        counters = asyncio.create_task(_counters(daemon, status, stop))
        try:
            await reader  # stdin closed or a ``stop`` command
        finally:
            stop.set()
            for task in (watcher, counters):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            daemon_process._emit_status(status)


if __name__ == "__main__":
    asyncio.run(main())
    sys.exit(0)
