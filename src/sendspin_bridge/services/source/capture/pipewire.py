"""The phone's capture on PipeWire: ``bluez_input.<MAC>.<n>`` read by ``pw-record``."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

from sendspin_bridge.services.source.capture import (
    CHANNELS,
    SAMPLE_RATE,
    CaptureEndpoint,
    CaptureStream,
    ProcessCapture,
    mac_from_node,
    run_command,
)

__all__ = [
    "BLUEZ_INPUT_RE",
    "PipeWireBackend",
    "bluez_inputs_from_dump",
    "find_bluez_input",
    "find_bluez_input_on_host",
]

logger = logging.getLogger(__name__)

#: Node names PipeWire gives the A2DP capture of a paired phone, e.g. ``bluez_input.34_C7_E9_25_A3_A3.2``.
BLUEZ_INPUT_RE = re.compile(r"bluez_input\.[0-9A-Fa-f_]{17}\.\d+")


def find_bluez_inputs(wpctl_status: str) -> list[str]:
    """Every capture node ``wpctl status`` prints, in its order."""
    return list(dict.fromkeys(match.group(0) for match in BLUEZ_INPUT_RE.finditer(wpctl_status)))


def find_bluez_input(wpctl_status: str) -> str | None:
    """The first node to capture from, as ``wpctl status`` prints it."""
    nodes = find_bluez_inputs(wpctl_status)
    return nodes[0] if nodes else None


def bluez_inputs_from_dump(payload: str) -> list[str]:
    """Every Bluetooth capture node ``pw-dump`` reports, in its order.

    ``wpctl status`` prints a node's *description* once the device has one — a
    headset's HFP capture appears there as "WH-1000XM4", never as
    ``bluez_input.<MAC>.<n>`` — while ``pw-dump`` carries the real
    ``node.name``. A sink is filtered out even when its name looks the same.
    """
    try:
        objects = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    found: list[str] = []
    for entry in objects or ():
        if not isinstance(entry, dict):
            continue
        info = entry.get("info")
        props = info.get("props") if isinstance(info, dict) else None
        if not isinstance(props, dict):
            continue
        name = str(props.get("node.name") or "")
        if not BLUEZ_INPUT_RE.fullmatch(name):
            continue
        media_class = str(props.get("media.class") or "")
        if media_class and media_class != "Audio/Source":
            continue
        found.append(name)
    return list(dict.fromkeys(found))


async def _pipewire_nodes(run: Any = run_command) -> list[str]:
    """Bluetooth capture nodes: ``pw-dump`` first, ``wpctl status`` as the fallback.

    ``pw-dump`` answers with the authoritative node names; ``wpctl`` is what a
    host without the tool can still answer, and what older hosts have.
    """
    dump = await run(["pw-dump"])
    if dump.returncode == 0:
        nodes = bluez_inputs_from_dump(dump.stdout)
        if nodes:
            return nodes
    status = await run(["wpctl", "status"])
    return find_bluez_inputs(status.stdout) if status.returncode == 0 else []


async def find_bluez_input_on_host() -> str | None:
    """Ask PipeWire for the phone's capture node, if one is streaming."""
    nodes = await _pipewire_nodes()
    return nodes[0] if nodes else None


class PipeWireBackend:
    """Find and record the phone's PipeWire capture node."""

    name = "pipewire"

    def __init__(
        self,
        *,
        run: Any = run_command,
        spawn: Any | None = None,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        fixed_node: str | None = None,
    ) -> None:
        self._run = run
        self._spawn = spawn
        self._sample_rate = sample_rate
        self._channels = channels
        self._fixed_node = fixed_node
        self.preferred_mac: str | None = None

    def set_preferred_mac(self, mac: str | None) -> None:
        self.preferred_mac = str(mac or "").upper() or None

    async def find(self) -> CaptureEndpoint | None:
        """The phone's capture node, when PipeWire reports one (or a fixed debug node)."""
        if self._fixed_node:
            if self.preferred_mac and mac_from_node(self._fixed_node) != self.preferred_mac:
                return None
            return CaptureEndpoint(
                node=self._fixed_node,
                backend=self.name,
                phone_mac=mac_from_node(self._fixed_node),
                running=True,
            )
        nodes = await _pipewire_nodes(self._run)
        if self.preferred_mac:
            nodes = [node for node in nodes if mac_from_node(node) == self.preferred_mac]
        node = nodes[0] if nodes else None
        if node is None:
            return None
        # ``wpctl`` lists the node only while the phone has a transport, so its presence is
        # the streaming signal; PipeWire does not expose the transport state there.
        return CaptureEndpoint(node=node, backend=self.name, phone_mac=mac_from_node(node), running=True)

    async def open(self, endpoint: CaptureEndpoint) -> CaptureStream:
        """Record ``endpoint`` as raw s16 PCM on stdout."""
        argv: Sequence[str] = (
            "pw-record",
            "--target",
            endpoint.node,
            "--rate",
            str(self._sample_rate),
            "--channels",
            str(self._channels),
            "--format",
            "s16",
            "-",
        )
        return ProcessCapture(
            argv,
            sample_rate=self._sample_rate,
            channels=self._channels,
            spawn=self._spawn,
        )
