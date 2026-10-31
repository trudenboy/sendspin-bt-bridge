"""Play the phone's Bluetooth input on a local speaker without the Sendspin stream (#563).

The Sendspin source serves remote players; for a speaker the bridge itself drives, a direct
PipeWire relay is simpler and always works: read the phone's capture node, write the speaker's
sink. One pipeline per speaker, kept armed across phone reconnects and bridge restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

#: Devices the operator asked to play the phone, and the sink of each (persisted).
_armed: dict[str, str] = {}
#: Running pipelines: device id -> (process, capture node it reads).
_running: dict[str, tuple[asyncio.subprocess.Process, str]] = {}
_state_path: Path | None = None
_watcher: asyncio.Task[None] | None = None


def _command(node: str, sink: str) -> str:
    return (
        f"pw-record --target {shlex.quote(node)} --rate 48000 --channels 2 --format s16 - "
        f"| pw-play --target {shlex.quote(sink)} --rate 48000 --channels 2 --format s16 -"
    )


def configure(state_path: Path) -> None:
    """Where the armed devices are remembered across restarts."""
    global _state_path
    _state_path = state_path


def load() -> None:
    """Read the armed devices saved by a previous run."""
    if _state_path is None or not _state_path.exists():
        return
    try:
        saved = json.loads(_state_path.read_text())
    except Exception:
        logger.warning("Bluetooth source: could not read %s", _state_path, exc_info=True)
        return
    if isinstance(saved, dict):
        _armed.update({str(k): str(v) for k, v in saved.items()})


def _save() -> None:
    if _state_path is None:
        return
    try:
        _state_path.parent.mkdir(parents=True, exist_ok=True)
        _state_path.write_text(json.dumps(_armed, indent=2) + "\n")
    except Exception:
        logger.warning("Bluetooth source: could not write %s", _state_path, exc_info=True)


def armed_devices() -> list[str]:
    """Devices that should be playing the phone (armed, whether or not the phone is here)."""
    return list(_armed)


def active_devices() -> list[str]:
    """Devices whose relay process is still running."""
    return [device_id for device_id, (proc, _node) in _running.items() if proc.returncode is None]


def arm(device_id: str, sink: str) -> None:
    """Remember that this speaker should play the phone, and save it."""
    _armed[device_id] = sink
    _save()


def disarm(device_id: str) -> None:
    _armed.pop(device_id, None)
    _save()


async def _stop_running(device_id: str) -> None:
    entry = _running.pop(device_id, None)
    if entry is None:
        return
    proc, _node = entry
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except TimeoutError:
            proc.kill()
    logger.info("Bluetooth source: relay for %s stopped", device_id)


async def _start_running(device_id: str, node: str, sink: str) -> None:
    proc = await asyncio.create_subprocess_shell(
        _command(node, sink), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    _running[device_id] = (proc, node)
    logger.info("Bluetooth source: relaying %s to %s", node, sink)


async def _reconcile(node: str | None) -> None:
    """Make the running relays match the armed devices and the phone that is here now."""
    for device_id, sink in list(_armed.items()):
        entry = _running.get(device_id)
        alive = entry is not None and entry[0].returncode is None
        if node is None:
            if alive:
                await _stop_running(device_id)
            continue
        if not alive or entry is None or entry[1] != node:
            if alive:
                await _stop_running(device_id)
            await _start_running(device_id, node, sink)
    for device_id in list(_running):
        if device_id not in _armed:
            await _stop_running(device_id)


async def _watch() -> None:
    from sendspin_bridge.services.source.bt_source import find_bluez_input_on_host

    while True:
        try:
            node = await find_bluez_input_on_host()
            await _reconcile(node)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Bluetooth source: relay watch failed", exc_info=True)
        await asyncio.sleep(2.0)


def start_watcher() -> None:
    """Keep the armed relays in step with the phone (reconnects, new capture nodes)."""
    global _watcher
    if _watcher is None or _watcher.done():
        _watcher = asyncio.create_task(_watch(), name="source-relay-watch")


async def stop_watcher() -> None:
    global _watcher
    if _watcher is not None:
        _watcher.cancel()
        try:
            await _watcher
        except asyncio.CancelledError:
            pass
        _watcher = None


async def stop_all() -> None:
    for device_id in list(_running):
        await _stop_running(device_id)
