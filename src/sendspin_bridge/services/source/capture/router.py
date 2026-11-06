"""Select one source input: a local audio node or a Bluetooth bond, or auto-Bluetooth."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.source.capture import CaptureEndpoint, run_command
from sendspin_bridge.services.source.inputs import parse_source_id

if TYPE_CHECKING:
    from sendspin_bridge.services.source.capture import CaptureBackend, CaptureStream

logger = logging.getLogger(__name__)

__all__ = ["SourceInputRouter", "local_input_present"]


async def local_input_present(engine: str, node: str) -> bool:
    """Whether the audio server still exposes *node* as a capture source."""
    if not node:
        return False
    if engine == "pulseaudio":
        result = await run_command(["pactl", "list", "short", "sources"])
        if result.returncode != 0:
            return False
        return any(
            line.split("\t")[1:2] == [node] or node in line.split()
            for line in result.stdout.splitlines()
            if line.strip()
        )
    if engine == "pipewire":
        result = await run_command(["wpctl", "status"])
        if result.returncode == 0 and node in result.stdout:
            return True
        # ``wpctl status`` is a tree and can truncate long names; pw-dump is
        # the authoritative node list when it is available.
        dumped = await run_command(["pw-dump"])
        if dumped.returncode != 0:
            return False
        try:
            objects = json.loads(dumped.stdout)
        except json.JSONDecodeError:
            return False
        return any(
            isinstance(obj, dict)
            and isinstance(obj.get("info"), dict)
            and (obj["info"].get("props") or {}).get("node.name") == node
            for obj in objects or ()
        )
    return False


class SourceInputRouter:
    """Route ``find``/``open`` to the picked local input or the Bluetooth backend.

    ``None`` keeps the historic auto-Bluetooth policy.  A pinned Bluetooth
    source uses the backend's preferred-MAC filter, so a second phone that
    starts streaming does not steal the capture.
    """

    name = "source"

    def __init__(
        self,
        base: CaptureBackend,
        active_input: str | None = None,
        *,
        probe: Any = local_input_present,
        headset: Any = None,
    ) -> None:
        self._base = base
        self._active = str(active_input or "").strip() or None
        self._probe = probe
        self._headset = headset

    @property
    def active_input(self) -> str | None:
        return self._active

    async def set_active(self, source_id: str | None) -> None:
        """Switch only after the caller has stopped PCM; profile release is awaited."""
        chosen = str(source_id or "").strip() or None
        if chosen != self._active:
            await self.release_active()
        self._active = chosen
        parsed = parse_source_id(chosen) if chosen else None
        self._set_base_mac(parsed[1] if parsed and parsed[0] == "bluetooth" else None)

    def set_preferred_mac(self, mac: str | None) -> None:
        """Legacy Bluetooth selection: turn it into an active Bluetooth source."""
        value = str(mac or "").upper() or None
        self._active = f"bluetooth:{value}" if value else None
        self._set_base_mac(value)

    def _set_base_mac(self, mac: str | None) -> None:
        setter = getattr(self._base, "set_preferred_mac", None)
        if callable(setter):
            setter(mac)

    async def find(self) -> CaptureEndpoint | None:
        if self._active == "none":
            return None
        parsed = parse_source_id(self._active) if self._active else None
        if parsed is not None and parsed[0] == "headset_microphone":
            mac = parsed[1]
            if self._headset is None or not await self._headset.acquire_microphone(mac):
                return None
            try:
                self._set_base_mac(mac)
                for _ in range(16):
                    endpoint = await self._base.find()
                    if endpoint is not None:
                        return endpoint
                    await asyncio.sleep(0.5)
            except BaseException:
                await self._headset.release_microphone(mac)
                raise
            await self._headset.release_microphone(mac)
            return None
        if parsed is not None and parsed[0] == "local":
            node = parsed[1]
            if not await self._probe(self._base.name, node):
                logger.info("Source: local input %s is not present", node)
                return None
            return CaptureEndpoint(node=node, backend=self._base.name, running=True)
        if parsed is not None and parsed[0] == "bluetooth":
            self._set_base_mac(parsed[1])
        return await self._base.find()

    async def open(self, endpoint: CaptureEndpoint) -> CaptureStream:
        return await self._base.open(endpoint)

    async def release_active(self) -> None:
        """Return a headset to A2DP after its microphone input stops being used."""
        if self._headset is None or not self._active:
            return
        parsed = parse_source_id(self._active)
        if parsed is not None and parsed[0] == "headset_microphone":
            await self._headset.release_microphone(parsed[1])
