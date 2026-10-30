"""Play the phone's Bluetooth input on a local speaker without the Sendspin stream (#563).

The Sendspin source serves remote players; for a speaker the bridge itself drives, a direct
PipeWire relay is simpler and always works: read the phone's capture node, write the speaker's
sink. One process pair per speaker.
"""

from __future__ import annotations

import asyncio
import logging
import shlex

logger = logging.getLogger(__name__)

_relays: dict[str, asyncio.subprocess.Process] = {}


def _command(node: str, sink: str) -> str:
    return (
        f"pw-record --target {shlex.quote(node)} --rate 48000 --channels 2 --format s16 - "
        f"| pw-play --target {shlex.quote(sink)} --rate 48000 --channels 2 --format s16 -"
    )


async def start(device_id: str, node: str, sink: str) -> bool:
    """Relay *node* to *sink* for *device_id*; a running relay is left alone."""
    existing = _relays.get(device_id)
    if existing is not None and existing.returncode is None:
        return True
    proc = await asyncio.create_subprocess_shell(
        _command(node, sink), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    _relays[device_id] = proc
    logger.info("Bluetooth source: relaying %s to %s", node, sink)
    return True


async def stop(device_id: str) -> None:
    proc = _relays.pop(device_id, None)
    if proc is None:
        return
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except TimeoutError:
            proc.kill()
    logger.info("Bluetooth source: relay for %s stopped", device_id)


def active_devices() -> list[str]:
    """Devices whose relay process is still running."""
    return [device_id for device_id, proc in _relays.items() if proc.returncode is None]


async def stop_all() -> None:
    for device_id in list(_relays):
        await stop(device_id)
