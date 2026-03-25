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
import base64
import logging
import socket
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from aiosendspin.models.core import (
    DeviceInfo,
    GroupUpdateServerPayload,
    ServerCommandPayload,
    ServerStatePayload,
)
from aiosendspin.models.types import BinaryMessageType, PlayerCommand, UndefinedField
from sendspin.daemon.daemon import DaemonArgs, SendspinDaemon

from config import VERSION as _BRIDGE_VERSION
from services.pulse import (
    aset_sink_volume,
)

UTC = timezone.utc

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
        self._background_tasks: set = set()

    def _notify(self) -> None:
        """Notify subscriber that status has changed (no-op if no callback)."""
        if self._on_status_change:
            try:
                self._on_status_change()
            except Exception as exc:
                logger.warning("on_status_change callback failed: %s", exc)

    # ── Client creation ──────────────────────────────────────────────────────

    def _create_client(self, static_delay_ms: float = 0.0):
        """Create client with bridge-specific DeviceInfo and register all listeners."""
        from aiosendspin.models.player import ClientHelloPlayerSupport
        from aiosendspin.models.types import Roles

        from services.sendspin_compat import detect_supported_audio_formats_for_device, filter_supported_call_kwargs

        try:
            sw_ver = f"aiosendspin {_pkg_version('aiosendspin')}"
        except PackageNotFoundError:
            sw_ver = "aiosendspin"

        device_info = DeviceInfo(
            product_name=f"Sendspin BT Bridge v{_BRIDGE_VERSION}",
            manufacturer=socket.gethostname(),
            software_version=sw_ver,
        )

        if self._audio_handler is None:
            raise RuntimeError("BridgeDaemon: audio handler not initialised")
        client_roles = [Roles.PLAYER, Roles.METADATA, Roles.CONTROLLER]

        supported_formats = detect_supported_audio_formats_for_device(self._args.audio_device)
        if self._args.preferred_format is not None:
            supported_formats = [f for f in supported_formats if f != self._args.preferred_format]
            supported_formats.insert(0, self._args.preferred_format)

        from aiosendspin.client import SendspinClient as _AioSendspinClient

        # Build optional role support objects (gracefully skip if imports fail)
        visualizer_support = None
        artwork_support = None
        try:
            from aiosendspin.models.visualizer import ClientHelloVisualizerSupport

            visualizer_support = ClientHelloVisualizerSupport(
                buffer_capacity=64_000,
                types=["loudness"],
                batch_max=4,
            )
            client_roles.append(Roles.VISUALIZER)
        except Exception:
            logger.debug("Visualizer role not available in this aiosendspin version")

        try:
            from aiosendspin.models.artwork import ArtworkChannel, ClientHelloArtworkSupport
            from aiosendspin.models.types import ArtworkSource, PictureFormat

            artwork_support = ClientHelloArtworkSupport(
                channels=[
                    ArtworkChannel(
                        source=ArtworkSource.ALBUM,
                        format=PictureFormat.JPEG,
                        media_width=500,
                        media_height=500,
                    )
                ]
            )
            client_roles.append(Roles.ARTWORK)
        except Exception:
            logger.debug("Artwork role not available in this aiosendspin version")

        # Use filter_supported_call_kwargs to handle version differences
        client_kwargs = filter_supported_call_kwargs(
            _AioSendspinClient,
            {
                "client_id": self._args.client_id,
                "client_name": self._args.client_name,
                "roles": client_roles,
                "device_info": device_info,
                "player_support": ClientHelloPlayerSupport(
                    supported_formats=supported_formats,
                    buffer_capacity=32_000_000,
                    supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE],
                ),
                "artwork_support": artwork_support,
                "visualizer_support": visualizer_support,
                "static_delay_ms": static_delay_ms,
                "initial_volume": self._audio_handler.volume,
                "initial_muted": self._audio_handler.muted,
            },
        )
        # Remove role-specific support kwargs if the roles were dropped
        if "artwork_support" in client_kwargs and Roles.ARTWORK not in client_roles:
            client_kwargs.pop("artwork_support", None)
        if "visualizer_support" in client_kwargs and Roles.VISUALIZER not in client_roles:
            client_kwargs.pop("visualizer_support", None)
        # Remove None support kwargs (avoids validation errors when role is present but support is None)
        if client_kwargs.get("artwork_support") is None:
            client_kwargs.pop("artwork_support", None)
            client_roles = [r for r in client_roles if r != Roles.ARTWORK]
            client_kwargs["roles"] = client_roles
        if client_kwargs.get("visualizer_support") is None:
            client_kwargs.pop("visualizer_support", None)
            client_roles = [r for r in client_roles if r != Roles.VISUALIZER]
            client_kwargs["roles"] = client_roles

        client = _AioSendspinClient(**client_kwargs)

        # Monkey-patch binary message handler to support artwork frames
        self._patch_artwork_handler(client)

        client.add_group_update_listener(self._on_group_update)
        client.add_metadata_listener(self._on_metadata_update)
        client.add_controller_state_listener(self._on_controller_state)
        client.add_disconnect_listener(self._on_server_disconnect)

        # Register visualizer listener if available
        if hasattr(client, "add_visualizer_listener"):
            client.add_visualizer_listener(self._on_visualizer_frames)

        return client

    def _patch_artwork_handler(self, client) -> None:
        """Monkey-patch the client's binary message handler to forward artwork frames."""
        original_handler = client._handle_binary_message
        artwork_types = {
            BinaryMessageType.ARTWORK_CHANNEL_0.value,
            BinaryMessageType.ARTWORK_CHANNEL_1.value,
            BinaryMessageType.ARTWORK_CHANNEL_2.value,
            BinaryMessageType.ARTWORK_CHANNEL_3.value,
        }

        def _patched_handler(payload: bytes) -> None:
            if len(payload) >= 1 and payload[0] in artwork_types:
                channel = payload[0] - BinaryMessageType.ARTWORK_CHANNEL_0.value
                # Artwork binary: [type:1][timestamp:8][image_data...]
                image_data = payload[9:] if len(payload) > 9 else b""
                if image_data:
                    self._on_artwork_frame(channel, image_data)
                return
            original_handler(payload)

        client._handle_binary_message = _patched_handler

    # ── Connection lifecycle ─────────────────────────────────────────────────

    def _mark_server_connected(self, ws) -> None:
        """Publish bridge status only after the new server handshake succeeds."""
        if not self._bridge_status.get("server_connected"):
            self._bridge_status["server_connected_at"] = datetime.now(tz=UTC).isoformat()
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

    async def _handle_server_connection(self, ws) -> None:
        """Mirror the upstream connect flow without stale disconnect status races."""
        logger.info("Server connected")
        assert self._audio_handler is not None
        assert self._connection_lock is not None
        assert self._settings is not None

        async with self._connection_lock:
            previous_client: Any = getattr(self, "_client", None)
            if previous_client is not None:
                logger.info("Disconnecting from previous server")
                await self._handle_disconnect()
                if previous_client.connected:
                    try:
                        from aiosendspin.models.core import ClientGoodbyeMessage, ClientGoodbyePayload
                        from aiosendspin.models.types import GoodbyeReason

                        await previous_client._send_message(
                            ClientGoodbyeMessage(
                                payload=ClientGoodbyePayload(reason=GoodbyeReason.ANOTHER_SERVER)
                            ).to_json()
                        )
                    except Exception:
                        logger.debug("Failed to send goodbye message", exc_info=True)
                await previous_client.disconnect()

            client = self._create_client(self._static_delay_ms)
            self._client = client
            self._audio_handler.attach_client(client)
            client.add_server_command_listener(self._handle_server_command)

            try:
                await client.attach_websocket(ws)
            except TimeoutError:
                logger.warning("Handshake with server timed out")
                await self._handle_disconnect()
                if self._client is client:
                    self._client = None
                return
            except Exception:
                logger.exception("Error during server handshake")
                await self._handle_disconnect()
                if self._client is client:
                    self._client = None
                return

            self._mark_server_connected(ws)

        try:
            disconnect_event = asyncio.Event()
            unsubscribe = client.add_disconnect_listener(disconnect_event.set)
            await disconnect_event.wait()
            unsubscribe()
            logger.info("Server disconnected")
        except Exception:
            logger.exception("Error waiting for server disconnect")
        finally:
            if self._client is client:
                await self._handle_disconnect()

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
        self._notify()

    def _on_stream_event(self, event: str) -> None:
        super()._on_stream_event(event)
        is_playing = event == "start"
        if self._bridge_status.get("playing") != is_playing:
            self._bridge_status["playing"] = is_playing
            self._bridge_status["state_changed_at"] = datetime.now(tz=UTC).isoformat()
            self._notify()
        if event == "stop":
            self._bridge_status["audio_streaming"] = False
            self._notify()
        elif event == "start" and self._bridge_status.get("audio_format"):
            # Re-anchor or track change: format_change won't fire again
            # if codec/rate/depth/channels are unchanged, but audio IS flowing.
            self._bridge_status["audio_streaming"] = True
            self._notify()

    # ── Server commands (volume / mute) ──────────────────────────────────────

    def _handle_server_command(self, payload: ServerCommandPayload) -> None:
        super()._handle_server_command(payload)
        if payload.player is None:
            return
        cmd = payload.player
        if cmd.command == PlayerCommand.VOLUME and cmd.volume is not None:
            vol = max(0, min(100, cmd.volume))
            self._bridge_status["volume"] = vol
            # Only sync manually when there is no upstream volume controller
            if not self._has_upstream_volume_controller():
                self._sync_bt_sink_volume(vol)
            self._notify()
        elif cmd.command == PlayerCommand.MUTE and cmd.mute is not None:
            self._bridge_status["muted"] = cmd.mute
            self._notify()

    def _has_upstream_volume_controller(self) -> bool:
        """Check if the upstream AudioStreamHandler uses an external volume controller."""
        handler = self._audio_handler
        return handler is not None and getattr(handler, "uses_external_volume_controller", False)

    def _sync_bt_sink_volume(self, volume: int) -> None:
        """Apply volume to the specific Bluetooth sink via pulsectl_asyncio."""
        if not self._bluetooth_sink_name:
            return
        try:
            task = asyncio.ensure_future(aset_sink_volume(self._bluetooth_sink_name, volume))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
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
            "Group update: id=%r name=%r state=%s",
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
        if not isinstance(getattr(metadata, "album", UndefinedField()), UndefinedField):
            self._bridge_status["current_album"] = metadata.album
            changed = True
        if not isinstance(getattr(metadata, "album_artist", UndefinedField()), UndefinedField):
            self._bridge_status["current_album_artist"] = metadata.album_artist
            changed = True
        if not isinstance(getattr(metadata, "artwork_url", UndefinedField()), UndefinedField):
            self._bridge_status["artwork_url"] = metadata.artwork_url
            changed = True
        if not isinstance(getattr(metadata, "year", UndefinedField()), UndefinedField):
            self._bridge_status["track_year"] = metadata.year
            changed = True
        if not isinstance(getattr(metadata, "track", UndefinedField()), UndefinedField):
            self._bridge_status["track_number"] = metadata.track
            changed = True
        if not isinstance(getattr(metadata, "shuffle", UndefinedField()), UndefinedField):
            self._bridge_status["shuffle"] = bool(metadata.shuffle) if metadata.shuffle is not None else None
            changed = True
        repeat_val = getattr(metadata, "repeat", UndefinedField())
        if not isinstance(repeat_val, UndefinedField):
            self._bridge_status["repeat_mode"] = (
                repeat_val.value
                if hasattr(repeat_val, "value")
                else str(repeat_val)
                if repeat_val is not None
                else None
            )
            changed = True
        progress = getattr(metadata, "progress", None)
        if progress is not None:
            tp = getattr(progress, "track_progress", None)
            td = getattr(progress, "track_duration", None)
            ps = getattr(progress, "playback_speed", None)
            if tp is not None:
                self._bridge_status["track_progress_ms"] = int(tp)
                changed = True
            if td is not None:
                self._bridge_status["track_duration_ms"] = int(td)
                changed = True
            if ps is not None:
                self._bridge_status["playback_speed"] = int(ps)
                changed = True
        if changed:
            self._notify()

    # ── Controller state ─────────────────────────────────────────────────────

    def _on_controller_state(self, payload: ServerStatePayload) -> None:
        """Callback receives ServerStatePayload; controller info is in payload.controller."""
        controller = getattr(payload, "controller", None)
        if controller is None:
            return
        changed = False
        supported = getattr(controller, "supported_commands", None)
        if supported is not None:
            self._bridge_status["supported_commands"] = [
                cmd.value if hasattr(cmd, "value") else str(cmd) for cmd in supported
            ]
            changed = True
        vol = getattr(controller, "volume", None)
        if vol is not None:
            self._bridge_status["group_volume"] = int(vol)
            changed = True
        muted = getattr(controller, "muted", None)
        if muted is not None:
            self._bridge_status["group_muted"] = bool(muted)
            changed = True
        if changed:
            self._notify()

    # ── Artwork (binary frames via monkey-patch) ─────────────────────────────

    def _on_artwork_frame(self, channel: int, image_data: bytes) -> None:
        """Handle artwork binary frame received from the server."""
        # Cap artwork at 48 KB raw (64 KB base64) to stay within IPC line limits
        _MAX_ARTWORK_RAW = 48_000
        if len(image_data) > _MAX_ARTWORK_RAW:
            logger.debug("Artwork frame too large for IPC (%d bytes), skipping", len(image_data))
            return
        encoded = base64.b64encode(image_data).decode("ascii")
        prev = self._bridge_status.get("artwork_b64")
        self._bridge_status["artwork_b64"] = encoded
        self._bridge_status["artwork_channel"] = channel
        logger.debug("Artwork frame: channel=%d size=%d bytes", channel, len(image_data))
        if encoded != prev:
            self._notify()

    # ── Visualizer (loudness / spectrum) ─────────────────────────────────────

    def _on_visualizer_frames(self, frames) -> None:
        """Handle visualizer frames (loudness, f_peak, spectrum).

        Visualizer data is stored in status but does NOT trigger a status
        notification — it arrives many times per second and would cause
        constant SSE re-renders that close modals/popups in the web UI.
        """
        if not frames:
            return
        latest = frames[-1]
        viz: dict = {}
        if latest.loudness is not None:
            viz["loudness"] = latest.loudness
        if latest.f_peak is not None:
            viz["f_peak"] = latest.f_peak
        if latest.spectrum is not None:
            viz["spectrum"] = latest.spectrum
        if viz:
            self._bridge_status["visualizer"] = viz
