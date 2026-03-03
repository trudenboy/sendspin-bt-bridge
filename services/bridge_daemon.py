"""In-process SendspinDaemon subclass that exposes status callbacks for the bridge.

Replaces the subprocess + stdout-parsing approach: BridgeDaemon subclasses
SendspinDaemon and overrides key methods to update the bridge status dict
directly via typed callbacks instead of fragile log-line parsing.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from aiosendspin.models.core import (
    DeviceInfo,
    GroupUpdateServerPayload,
    ServerCommandPayload,
    ServerStatePayload,
)
from aiosendspin.models.types import PlayerCommand, UndefinedField
from sendspin.daemon.daemon import DaemonArgs, SendspinDaemon

from config import VERSION as _BRIDGE_VERSION
from services.pulse import (
    aget_sink_description,
    alist_sink_input_ids,
    amove_sink_input,
    aset_sink_volume,
)

logger = logging.getLogger(__name__)


class BridgeDaemon(SendspinDaemon):
    """SendspinDaemon subclass that mirrors status into the bridge status dict.

    Args:
        args: Standard DaemonArgs for SendspinDaemon.
        status: Shared status dict owned by SendspinClient (updated in-place).
        bluetooth_sink_name: PulseAudio/PipeWire sink name for volume sync.
        on_volume_save: Optional callback(volume_int) called after volume changes
                        to persist the value to config.
    """

    # Class-level lock to serialize sink-input routing across daemon instances
    _routing_lock = asyncio.Lock()
    _claimed_sink_inputs: set[int] = set()  # IDs already routed by other daemons

    def __init__(
        self,
        args: DaemonArgs,
        status: dict,
        bluetooth_sink_name: str | None,
        on_volume_save: Callable[[int], None] | None = None,
        pre_start_sink_input_ids: set[int] | None = None,
    ) -> None:
        super().__init__(args)
        self._bridge_status = status
        self._bluetooth_sink_name = bluetooth_sink_name
        self._on_volume_save = on_volume_save
        self._pre_start_sink_input_ids = pre_start_sink_input_ids or set()
        self._routed = False  # True after sink-input has been moved to target
        self._routed_sink_input_id: int | None = None
        self._routing_task: asyncio.Task | None = None  # current pending route task

    # ── Client creation ──────────────────────────────────────────────────────

    def _create_client(self, static_delay_ms: float = 0.0):
        """Create client with bridge-specific DeviceInfo and register all listeners."""
        # Build a bridge-specific DeviceInfo and pass it directly into the client
        # so the server sees this process as a dedicated BT bridge endpoint.
        from aiosendspin.models.player import ClientHelloPlayerSupport
        from aiosendspin.models.types import Roles
        from sendspin.audio import detect_supported_audio_formats

        try:
            from aiosendspin_mpris import MPRIS_AVAILABLE
        except ImportError:
            MPRIS_AVAILABLE = False

        try:
            sw_ver = f"aiosendspin {_pkg_version('aiosendspin')}"
        except Exception:
            sw_ver = "aiosendspin"

        device_info = DeviceInfo(
            product_name=f"Sendspin BT Bridge v{_BRIDGE_VERSION}",
            manufacturer=socket.gethostname(),
            software_version=sw_ver,
        )

        assert self._audio_handler is not None
        client_roles = [Roles.PLAYER]
        if MPRIS_AVAILABLE and self._args.use_mpris:
            client_roles.extend([Roles.METADATA, Roles.CONTROLLER])

        supported_formats = detect_supported_audio_formats(self._args.audio_device.index)
        if self._args.preferred_format is not None:
            supported_formats = [f for f in supported_formats if f != self._args.preferred_format]
            supported_formats.insert(0, self._args.preferred_format)

        from aiosendspin.client import SendspinClient as _AioSendspinClient

        client = _AioSendspinClient(
            client_id=self._args.client_id,
            client_name=self._args.client_name,
            roles=client_roles,
            device_info=device_info,
            player_support=ClientHelloPlayerSupport(
                supported_formats=supported_formats,
                buffer_capacity=32_000_000,
                supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE],
            ),
            static_delay_ms=static_delay_ms,
            initial_volume=self._audio_handler.volume,
            initial_muted=self._audio_handler.muted,
        )
        client.add_group_update_listener(self._on_group_update)
        client.add_metadata_listener(self._on_metadata_update)
        client.add_disconnect_listener(self._on_server_disconnect)
        return client

    # ── Connection lifecycle ─────────────────────────────────────────────────

    async def _handle_server_connection(self, ws) -> None:
        """Mark server connected before passing to parent handler (mDNS mode)."""
        if not self._bridge_status.get("server_connected"):
            self._bridge_status["server_connected_at"] = datetime.now().isoformat()
        self._bridge_status["server_connected"] = True
        self._bridge_status["connected"] = True
        # Clear group state on new connection so stale IDs don't persist
        self._bridge_status["group_id"] = None
        self._bridge_status["group_name"] = None
        # Capture real MA server IP from the incoming request's peer address
        try:
            req = getattr(ws, "_req", None)
            if req is not None:
                peer = req.remote  # e.g. '192.168.10.10'
                # Rebuild URL using server_port from status (or default 9000)
                port = self._bridge_status.get("server_port", 9000)
                self._bridge_status["connected_server_url"] = f"{peer}:{port}"
        except Exception as _exc:
            logger.debug("Could not extract peer address: %s", _exc)
        await super()._handle_server_connection(ws)

    def _on_server_disconnect(self) -> None:
        """Clear connection + group state on disconnect."""
        self._bridge_status["server_connected"] = False
        self._bridge_status["connected"] = False
        self._bridge_status["group_name"] = None
        self._bridge_status["group_id"] = None

    # ── Audio / stream events ────────────────────────────────────────────────

    def _handle_format_change(self, codec: str | None, sample_rate: int, bit_depth: int, channels: int) -> None:
        super()._handle_format_change(codec, sample_rate, bit_depth, channels)
        self._bridge_status["audio_format"] = f"{codec or 'PCM'} {sample_rate}Hz/{bit_depth}-bit/{channels}ch"
        # PA stream (sink-input) was just created by set_format() → sounddevice.
        # Reset routing state so every new stream gets routed to the correct BT sink
        # (the stream/sink-input is recreated on each group play start).
        if self._bluetooth_sink_name:
            self._routed = False
            asyncio.ensure_future(self._route_stream_to_sink())

    async def _route_stream_to_sink(self) -> None:
        """Find the newly created sink-input and move it to the target BT sink.

        Two-phase approach to minimise the time audio plays through the wrong sink:

        Phase 1 — Claim (under lock, fast):
          Identify and atomically claim a sink-input ID.  If the previous ID is
          still live (same PortAudio stream, repeated play), claim it immediately
          without sleeping.  For new streams, poll briefly until the sink-input
          appears (max 0.3 s in 50 ms steps) rather than a fixed sleep.

        Phase 2 — Route (outside lock, parallel):
          Call ``pactl move-sink-input`` outside the lock so all daemons can
          route concurrently instead of sequentially.
        """
        _MAX_RETRIES = 3
        player = self._bridge_status.get("player_name", "?")
        sink_name = self._bluetooth_sink_name or ""
        target_id: int | None = None

        try:
            # ── Phase 1: claim a sink-input ID (serialised) ───────────────────
            async with BridgeDaemon._routing_lock:
                prev_id = self._routed_sink_input_id
                if prev_id is not None:
                    BridgeDaemon._claimed_sink_inputs.discard(prev_id)
                    self._routed_sink_input_id = None

                # Prune IDs that no longer exist
                live_ids = await alist_sink_input_ids()
                stale = BridgeDaemon._claimed_sink_inputs - live_ids
                if stale:
                    BridgeDaemon._claimed_sink_inputs -= stale

                # Fast path: re-use our own previous sink-input when still live
                # (same PortAudio stream across stop/play cycles — no sleep needed)
                if prev_id is not None and prev_id in live_ids and prev_id not in BridgeDaemon._claimed_sink_inputs:
                    target_id = prev_id
                else:
                    # Slow path: poll until a new PA sink-input appears (max 300 ms)
                    current_ids: set[int] = set()
                    unclaimed: set[int] = set()
                    for _ in range(6):
                        await asyncio.sleep(0.05)
                        current_ids = await alist_sink_input_ids()
                        new_ids = current_ids - self._pre_start_sink_input_ids
                        unclaimed = new_ids - BridgeDaemon._claimed_sink_inputs
                        if unclaimed:
                            break
                    if not unclaimed:
                        # Fall back to any unclaimed sink-input
                        current_ids = await alist_sink_input_ids()
                        unclaimed = current_ids - BridgeDaemon._claimed_sink_inputs
                    if not unclaimed:
                        logger.warning(
                            "[%s] No unclaimed sink-input found (pre=%s, cur=%s, claimed=%s)",
                            player,
                            self._pre_start_sink_input_ids,
                            current_ids,
                            BridgeDaemon._claimed_sink_inputs,
                        )
                        return
                    target_id = max(unclaimed)

                # Reserve the ID before releasing the lock so no other daemon steals it
                BridgeDaemon._claimed_sink_inputs.add(target_id)

            # ── Phase 2: route (outside lock — parallel across daemons) ───────
            for attempt in range(1, _MAX_RETRIES + 1):
                ok = await amove_sink_input(target_id, sink_name)
                if ok:
                    self._routed = True
                    self._routed_sink_input_id = target_id
                    logger.info(
                        "[%s] ✓ Routed sink-input %d → %s",
                        player,
                        target_id,
                        self._bluetooth_sink_name,
                    )
                    return
                if attempt < _MAX_RETRIES:
                    delay = 0.5 * attempt
                    logger.warning(
                        "[%s] Route attempt %d/%d failed for sink-input %d, retrying in %.1fs...",
                        player,
                        attempt,
                        _MAX_RETRIES,
                        target_id,
                        delay,
                    )
                    await asyncio.sleep(delay)
            # All retries failed — release the claimed ID
            BridgeDaemon._claimed_sink_inputs.discard(target_id)
            logger.warning(
                "[%s] Failed to route sink-input %d → %s after %d attempts",
                player,
                target_id,
                self._bluetooth_sink_name,
                _MAX_RETRIES,
            )

        except asyncio.CancelledError:
            # Task was superseded by a newer stream-start event — release any claimed ID
            if target_id is not None:
                BridgeDaemon._claimed_sink_inputs.discard(target_id)
            raise

    def _on_stream_event(self, event: str) -> None:
        super()._on_stream_event(event)
        is_playing = event == "start"
        if self._bridge_status.get("playing") != is_playing:
            self._bridge_status["playing"] = is_playing
            self._bridge_status["state_changed_at"] = datetime.now().isoformat()
        # Re-route on every stream start: PipeWire may have moved the sink-input
        # back to the default sink when the stream became active again.
        # Cancel any pending routing task so only the latest start event routes.
        if event == "start" and self._bluetooth_sink_name:
            self._routed = False
            if self._routing_task and not self._routing_task.done():
                self._routing_task.cancel()
            self._routing_task = asyncio.ensure_future(self._route_stream_to_sink())
        logger.debug("[%s] stream event: %s", self._bridge_status.get("player_name", "?"), event)

    # ── Server commands (volume / mute) ──────────────────────────────────────

    def _handle_server_command(self, payload: ServerCommandPayload) -> None:
        super()._handle_server_command(payload)
        if payload.player is None:
            return
        cmd = payload.player
        if cmd.command == PlayerCommand.VOLUME and cmd.volume is not None:
            self._bridge_status["volume"] = cmd.volume
            self._sync_bt_sink_volume(cmd.volume)
        elif cmd.command == PlayerCommand.MUTE and cmd.mute is not None:
            self._bridge_status["muted"] = cmd.mute

    def _sync_bt_sink_volume(self, volume: int) -> None:
        """Apply volume to the specific Bluetooth sink via pulsectl_asyncio."""
        if not self._bluetooth_sink_name:
            return
        try:
            task = asyncio.ensure_future(aset_sink_volume(self._bluetooth_sink_name, volume))
            task.add_done_callback(
                lambda t: (
                    logger.info("✓ Synced Bluetooth speaker volume to %d%%", volume)
                    if not t.exception() and t.result()
                    else logger.debug("Could not sync volume: %s", t.exception())
                )
            )
            if self._on_volume_save:
                self._on_volume_save(volume)
        except Exception as exc:
            logger.debug("Could not sync volume: %s", exc)

    # ── MA group updates ─────────────────────────────────────────────────────

    def _on_group_update(self, payload: GroupUpdateServerPayload) -> None:
        self._bridge_status["group_name"] = payload.group_name or None
        self._bridge_status["group_id"] = payload.group_id
        logger.info(
            "Group update: id=%s name=%s state=%s",
            payload.group_id,
            payload.group_name,
            payload.playback_state,
        )

    # ── Track metadata ───────────────────────────────────────────────────────

    def _on_metadata_update(self, payload: ServerStatePayload) -> None:
        """Callback receives ServerStatePayload; track info is in payload.metadata."""
        metadata = getattr(payload, "metadata", None)
        if metadata is None:
            return
        if not isinstance(metadata.title, UndefinedField):
            self._bridge_status["current_track"] = metadata.title
        if not isinstance(metadata.artist, UndefinedField):
            self._bridge_status["current_artist"] = metadata.artist


async def resolve_audio_device_for_sink(sink_name: str | None):
    """Find the sounddevice AudioDevice matching a PulseAudio/PipeWire sink name.

    PulseAudio/PipeWire exposes sinks to sounddevice/PortAudio by their *description*
    (friendly name), not by their PA sink identifier.  We use pulsectl_asyncio to get
    the description, then match against sounddevice devices.
    """
    from sendspin.audio import query_devices

    devices = query_devices()
    if not sink_name:
        return next((d for d in devices if d.is_default), None)

    logger.debug(
        "resolve_audio_device_for_sink(%s): available devices: %s",
        sink_name,
        [d.name for d in devices],
    )

    # 1. Exact match on sink name (PipeWire may expose by name)
    for dev in devices:
        if dev.name == sink_name:
            logger.info("Audio device matched by exact sink name: %s", dev.name)
            return dev

    # 2. Match via PA description (most reliable — pulsectl_asyncio, awaited directly)
    description = await aget_sink_description(sink_name)
    if description:
        logger.debug("Sink description for %s: %s", sink_name, description)
        desc_lower = description.lower()
        for dev in devices:
            if dev.name.lower() == desc_lower:
                logger.info("Audio device matched by description: %s", dev.name)
                return dev
        for dev in devices:
            if desc_lower in dev.name.lower() or dev.name.lower() in desc_lower:
                logger.info(
                    "Audio device partial-matched by description: %s (desc=%s)",
                    dev.name,
                    description,
                )
                return dev

    # 3. MAC-segment match: 'bluez_output.AA_BB_CC_DD_EE_FF.1' → 'AA_BB_CC_DD_EE_FF'
    parts = sink_name.split(".")
    mac_segment = parts[1] if len(parts) >= 2 else ""
    if mac_segment:
        for dev in devices:
            if mac_segment.lower() in dev.name.lower():
                logger.info("Audio device matched by MAC segment: %s", dev.name)
                return dev

    # 4. Prefix match
    for dev in devices:
        if dev.name.startswith(sink_name[:20]):
            logger.info("Audio device matched by prefix: %s", dev.name)
            return dev

    logger.warning(
        "No audio device found for sink %s (description=%s) — falling back to default",
        sink_name,
        description,
    )
    return next((d for d in devices if d.is_default), None)
