"""In-process SendspinDaemon subclass that exposes status callbacks for the bridge.

Replaces the subprocess + stdout-parsing approach: BridgeDaemon subclasses
SendspinDaemon and overrides key methods to update the bridge status dict
directly via typed callbacks instead of fragile log-line parsing.

When running inside a subprocess spawned by SendspinClient, PULSE_SINK is
already set in the subprocess environment before any PA connection is made,
so audio routes to the correct BT sink from the first sample.
"""

from __future__ import annotations

import asyncio
import logging
import os
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
    amove_pid_sink_inputs,
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
        on_status_change: Optional callback() called whenever status is mutated.
                          Used by daemon_process.py to flush status to parent.
    """

    def __init__(
        self,
        args: DaemonArgs,
        status: dict,
        bluetooth_sink_name: str | None,
        on_volume_save: Callable[[int], None] | None = None,
        on_status_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(args)
        self._bridge_status = status
        self._bluetooth_sink_name = bluetooth_sink_name
        self._on_volume_save = on_volume_save
        self._on_status_change = on_status_change
        self._sink_routed = False  # True after first move-sink-input for current stream

    def _notify(self) -> None:
        """Notify subscriber that status has changed (no-op if no callback)."""
        if self._on_status_change:
            try:
                self._on_status_change()
            except Exception:
                pass

    # ── Client creation ──────────────────────────────────────────────────────

    def _create_client(self, static_delay_ms: float = 0.0):
        """Create client with bridge-specific DeviceInfo and register all listeners."""
        # Build a bridge-specific DeviceInfo and pass it directly into the client
        # so the server sees this process as a dedicated BT bridge endpoint.
        from aiosendspin.models.player import ClientHelloPlayerSupport
        from aiosendspin.models.types import Roles
        from sendspin.audio import detect_supported_audio_formats

        try:
            sw_ver = f"aiosendspin {_pkg_version('aiosendspin')}"
        except Exception:
            sw_ver = "aiosendspin"

        device_info = DeviceInfo(
            product_name=f"Sendspin BT Bridge v{_BRIDGE_VERSION}",
            manufacturer=socket.gethostname(),
            software_version=sw_ver,
        )

        if self._audio_handler is None:
            raise RuntimeError("BridgeDaemon: audio handler not initialised")
        client_roles = [Roles.PLAYER, Roles.METADATA, Roles.CONTROLLER]
        # MPRIS is handled separately (requires D-Bus session bus, not available in subprocesses)

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
        self._notify()
        await super()._handle_server_connection(ws)

    def _on_server_disconnect(self) -> None:
        """Clear connection + group state on disconnect."""
        self._bridge_status["server_connected"] = False
        self._bridge_status["connected"] = False
        self._bridge_status["group_name"] = None
        self._bridge_status["group_id"] = None
        self._notify()

    # ── Audio / stream events ────────────────────────────────────────────────

    def _handle_format_change(self, codec: str | None, sample_rate: int, bit_depth: int, channels: int) -> None:
        super()._handle_format_change(codec, sample_rate, bit_depth, channels)
        self._bridge_status["audio_format"] = f"{codec or 'PCM'} {sample_rate}Hz/{bit_depth}-bit/{channels}ch"
        self._bridge_status["audio_streaming"] = True  # actual audio data arrived
        self._bridge_status["reanchor_count"] = 0  # reset per-stream re-anchor counter
        self._bridge_status["reanchoring"] = False
        self._bridge_status["last_reanchor_at"] = None
        self._sink_routed = False  # new stream — allow one routing correction
        self._notify()

    def _on_stream_event(self, event: str) -> None:
        super()._on_stream_event(event)
        is_playing = event == "start"
        if self._bridge_status.get("playing") != is_playing:
            self._bridge_status["playing"] = is_playing
            self._bridge_status["state_changed_at"] = datetime.now().isoformat()
            self._notify()
        if event == "stop":
            self._bridge_status["audio_streaming"] = False
            self._notify()
        # NOTE: reanchoring flag is NOT cleared here because sendspin logs "re-anchoring"
        # AFTER restarting the stream — so this callback fires before the log handler sets
        # the flag. Auto-clear is handled by _reanchor_watcher in daemon_process.py.
        if event == "start" and self._bluetooth_sink_name and not self._sink_routed:
            # Correct any stream PA module-rescue-streams moved to the default sink.
            # Guard with _sink_routed so we only move once per stream — moving a
            # sink-input causes a PA glitch that triggers re-anchoring, creating a loop.
            self._sink_routed = True
            asyncio.ensure_future(self._ensure_sink_routing())
        logger.debug("[%s] stream event: %s", self._bridge_status.get("player_name", "?"), event)

    async def _ensure_sink_routing(self) -> None:
        """Move any of our sink-inputs that ended up on the wrong sink."""
        pid = os.getpid()
        sink = self._bluetooth_sink_name
        if not sink:
            return
        try:
            moved = await amove_pid_sink_inputs(pid, sink)
            if moved:
                logger.info(
                    "[%s] Corrected %d sink-input(s) → %s", self._bridge_status.get("player_name", "?"), moved, sink
                )
        except Exception as exc:
            logger.debug("_ensure_sink_routing: %s", exc)

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
        self._notify()

    # ── Track metadata ───────────────────────────────────────────────────────

    def _on_metadata_update(self, payload: ServerStatePayload) -> None:
        """Callback receives ServerStatePayload; track info is in payload.metadata."""
        metadata = getattr(payload, "metadata", None)
        if metadata is None:
            return
        changed = False
        if not isinstance(metadata.title, UndefinedField):
            self._bridge_status["current_track"] = metadata.title
            changed = True
        if not isinstance(metadata.artist, UndefinedField):
            self._bridge_status["current_artist"] = metadata.artist
            changed = True
        if changed:
            self._notify()
