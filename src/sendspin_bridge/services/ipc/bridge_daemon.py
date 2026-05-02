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
from aiosendspin.models.types import PlayerCommand, UndefinedField
from sendspin.daemon.daemon import DaemonArgs, SendspinDaemon

from sendspin_bridge.config import VERSION as _BRIDGE_VERSION

UTC = timezone.utc

logger = logging.getLogger(__name__)


class BridgeDaemon(SendspinDaemon):
    """SendspinDaemon subclass that mirrors status into the bridge status dict.

    Args:
        args: Standard DaemonArgs for SendspinDaemon.
        status: Shared status dict owned by SendspinClient (updated in-place).
        bluetooth_sink_name: PulseAudio/PipeWire sink name for volume sync.
        on_status_change: Optional callback() called whenever status is mutated.
                          Used by daemon_process.py to flush status to parent.
    """

    def __init__(
        self,
        args: DaemonArgs,
        status: dict,
        bluetooth_sink_name: str | None,
        on_status_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(args)
        self._bridge_status = status
        self._bluetooth_sink_name = bluetooth_sink_name
        self._on_status_change = on_status_change

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

        from sendspin_bridge.services.diagnostics.sendspin_compat import (
            detect_supported_audio_formats_for_device,
            filter_supported_call_kwargs,
        )

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

        # Use filter_supported_call_kwargs to handle aiosendspin version
        # differences in the constructor surface.  Artwork support / binary
        # frame relay was dropped in 2.62.0-rc.9: bridge UI sources artwork
        # exclusively from MA's ``image_url`` via the existing
        # ``/api/ma/artwork`` HMAC-signed proxy, which removes the
        # monkey-patched ``_handle_binary_message`` (fragile across
        # aiosendspin upgrades) and saves an IPC roundtrip per track.
        client_kwargs = filter_supported_call_kwargs(
            _AioSendspinClient,
            {
                "client_id": self._args.client_id,
                "client_name": self._args.client_name,
                "roles": client_roles,
                "device_info": device_info,
                "player_support": ClientHelloPlayerSupport(
                    supported_formats=supported_formats,  # type: ignore[arg-type]
                    buffer_capacity=32_000_000,
                    supported_commands=[PlayerCommand.VOLUME, PlayerCommand.MUTE],
                ),
                "static_delay_ms": static_delay_ms,
                "initial_volume": self._audio_handler.volume,
                "initial_muted": self._audio_handler.muted,
                # client/state advertises SET_STATIC_DELAY so MA can drive the
                # per-player delay slider. Older aiosendspin (<5.1) drops this
                # kwarg via filter_supported_call_kwargs and degrades silently.
                "state_supported_commands": [PlayerCommand.SET_STATIC_DELAY],
            },
        )

        client = _AioSendspinClient(**client_kwargs)  # type: ignore[arg-type]

        client.add_group_update_listener(self._on_group_update)
        client.add_metadata_listener(self._on_metadata_update)
        client.add_controller_state_listener(self._on_controller_state)
        client.add_disconnect_listener(self._on_server_disconnect)

        # Register visualizer listener if available
        if hasattr(client, "add_visualizer_listener"):
            client.add_visualizer_listener(self._on_visualizer_frames)

        return client

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

    async def _handle_disconnect(self) -> None:
        """Disconnect cleanup — delegate to parent or fall back to local handler.

        Older ``aiosendspin`` versions (< 5.x, e.g. armv7 builds) do not
        provide ``_handle_disconnect`` on ``SendspinDaemon``.  Fall back to
        the synchronous ``_on_server_disconnect`` to clear bridge status.
        """
        parent = getattr(super(), "_handle_disconnect", None)
        if parent is not None:
            await parent()
        else:
            self._on_server_disconnect()

    async def _run_server_initiated(self, static_delay_ms: float) -> None:
        """Override upstream to add WebSocket heartbeat on the listener side.

        The upstream ``ClientListener`` creates ``web.WebSocketResponse()``
        without a ``heartbeat`` parameter.  When MA connects to the daemon,
        only MA's client-side heartbeat (30 s) keeps the connection alive.
        Proxies, firewalls, and Docker bridge networks may still drop the
        idle TCP connection because **no server-side pings** are sent.

        This override injects ``heartbeat=30`` so the daemon sends its own
        WebSocket pings — matching the behaviour of the MA server-side
        (``aiosendspin.server.connection``, line 179) and the client-side
        (``aiosendspin.client.client``, line 331).

        See: music-assistant/support#4598, trudenboy/sendspin-bt-bridge#120.
        """
        from aiohttp import web as _web
        from aiosendspin.client import ClientListener as _BaseListener

        _ws_logger = logging.getLogger("aiosendspin.client.listener")

        class _HeartbeatListener(_BaseListener):
            """ClientListener with server-side WebSocket heartbeat."""

            async def _handle_websocket(self, request: _web.Request) -> _web.WebSocketResponse:
                ws = _web.WebSocketResponse(heartbeat=30)
                await ws.prepare(request)
                _ws_logger.debug("Incoming server connection from %s", request.remote)
                try:
                    await self._on_connection(ws)
                except Exception:
                    _ws_logger.exception(
                        "Unhandled exception in on_connection callback for %s",
                        request.remote,
                    )
                    if not ws.closed:
                        await ws.close(code=1011, message=b"Internal error")
                return ws

        logger.info(
            "Listening for server connections on port %d (mDNS: _sendspin._tcp.local.)",
            self._args.listen_port,
        )

        self._static_delay_ms = static_delay_ms
        self._connection_lock = asyncio.Lock()

        self._listener = _HeartbeatListener(
            client_id=self._args.client_id,
            on_connection=self._handle_server_connection,
            port=self._args.listen_port,
            client_name=self._args.client_name,
        )
        await self._listener.start()

        while True:
            await asyncio.sleep(3600)

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

    async def _connection_watchdog(self, delay: float = 30.0, poll_interval: float = 2.0) -> None:
        """Surface a clear error when the daemon cannot reach the Sendspin server.

        Runs as a background task alongside the daemon.  After *delay* seconds
        with ``server_connected`` still False, sets ``last_error`` with the
        target URL so the operator sees an actionable message in the UI.
        Clears the error automatically once the connection succeeds.
        """
        try:
            await asyncio.sleep(delay)
            if self._bridge_status.get("server_connected"):
                return
            url = self._bridge_status.get("server_url") or "unknown"
            self._bridge_status["last_error"] = (
                f"Cannot connect to Sendspin server at {url}. "
                "Check that SENDSPIN_PORT matches your Music Assistant Sendspin port."
            )
            self._bridge_status["last_error_at"] = datetime.now(tz=UTC).isoformat()
            self._notify()
            # Keep watching — clear the error once connected
            while not self._bridge_status.get("server_connected"):
                await asyncio.sleep(poll_interval)
            self._bridge_status["last_error"] = None
            self._notify()
        except asyncio.CancelledError:
            return

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
        # The actual sink-level volume / mute application is performed by
        # ``services.pa_volume_controller.PulseVolumeController.set_state``,
        # which sendspin invokes upstream of this callback.  We only mirror
        # the new state into the bridge status dict so the parent-process
        # SendspinClient surfaces it to the web UI.  Pinned ``sendspin==7.0.0``
        # always provides the controller — the legacy <5.5.0 fallback that
        # called ``aset_sink_volume`` from inside this method was dropped
        # in 2.62.0-rc.9.
        super()._handle_server_command(payload)
        if payload.player is None:
            return
        cmd = payload.player
        if cmd.command == PlayerCommand.VOLUME and cmd.volume is not None:
            self._bridge_status["volume"] = max(0, min(100, cmd.volume))
            self._notify()
        elif cmd.command == PlayerCommand.MUTE and cmd.mute is not None:
            self._bridge_status["muted"] = cmd.mute
            self._notify()
        elif cmd.command == PlayerCommand.SET_STATIC_DELAY and cmd.static_delay_ms is not None:
            # aiosendspin's SendspinClient._handle_server_command auto-applied
            # the new delay via self.set_static_delay_ms(value) before this
            # listener fires. The sendspin AudioPlayer reads the post-clamp
            # value per chunk so audio shifts naturally — we only mirror the
            # value into bridge_status so the parent persists it and the
            # web UI repaints.
            client = getattr(self, "_client", None)
            applied = (
                int(client.static_delay_ms)
                if client is not None and hasattr(client, "static_delay_ms")
                else max(0, min(5000, int(cmd.static_delay_ms)))
            )
            self._bridge_status["static_delay_ms"] = applied
            self._notify()

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
