"""Per-speaker daemon: aiosendspin client + GStreamer player + status callbacks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.ipc.status_store import StatusStore

from aiosendspin.models.core import (
    DeviceInfo,
    GroupUpdateServerPayload,
    ServerCommandPayload,
    ServerStatePayload,
)
from aiosendspin.models.types import PlayerCommand, UndefinedField

from sendspin_bridge.config import VERSION as _BRIDGE_VERSION
from sendspin_bridge.services.audio.player.pipeline import default_pulsesink_factory
from sendspin_bridge.services.audio.player.player import PcmFormat, StreamPlayer

UTC = timezone.utc

logger = logging.getLogger(__name__)

# aiosendspin <5.1 doesn't define SET_STATIC_DELAY on PlayerCommand. Resolving
# the enum lazily via getattr lets the bridge keep advertising VOLUME/MUTE and
# handling other server commands on those older runtimes — the new code paths
# (capability advertising in client/state, MA-inbound delay handling) just
# short-circuit. The bridge currently pins aiosendspin==5.1.1, but the
# <5.x compat code in this module (e.g. _handle_disconnect fallback) shows
# this surface still ships to environments where defensive lookups are
# the established pattern.
_SET_STATIC_DELAY_CMD: Any = getattr(PlayerCommand, "SET_STATIC_DELAY", None)


@dataclass(frozen=True, slots=True)
class DaemonArgs:
    client_id: str
    client_name: str
    url: str | None = None
    listen_port: int = 8928
    static_delay_ms: float = 0.0
    initial_volume: int = 100
    initial_muted: bool = False
    interface: str | None = None
    sink_name: str | None = None
    pulse_latency_msec: int = 600
    slave_method: str = "skew"
    identity_path: str | None = None
    pairing_store_path: str | None = None


class BridgeDaemon:
    """Mirrors aiosendspin + Player status into the bridge status dict."""

    # Single source of truth for the player state advertised in client/state
    # messages we emit (e.g. when pushing a Web-UI-driven static_delay_ms back
    # to MA). aiosendspin's handshake calls send_player_state with
    # PlayerStateType.SYNCHRONIZED once at connect, and the bridge currently
    # has no concept of error / buffering states surfaced over client/state —
    # the AudioPlayer either streams or stops, with errors reported out-of-band
    # (status envelope, daemon stderr). Keeping this as a class attribute lets
    # future state-management code mutate it in one place without touching
    # every send_player_state call site.
    _last_player_state: object | None = None  # set in __init__ from PlayerStateType

    def __init__(
        self,
        args: DaemonArgs,
        status: StatusStore | dict,
        bluetooth_sink_name: str | None,
        on_status_change: Callable[[], None] | None = None,
        *,
        bt_product_name: str = "",
        bt_manufacturer: str = "",
        required_lead_time_ms: float = 250.0,
        min_buffer_ms: float = 250.0,
    ) -> None:
        self._args = args
        self._client = None
        self._listener = None
        self._connection_lock: asyncio.Lock | None = None
        self._player: StreamPlayer | None = None
        self._audio_handler = None
        self._volume = args.initial_volume
        self._muted = args.initial_muted
        self._volume_controller = None
        self._static_delay_ms = max(0.0, min(5000.0, args.static_delay_ms))
        self._bridge_status = status
        self._bluetooth_sink_name = bluetooth_sink_name
        self._on_status_change = on_status_change
        # Per-device identity advertised to MA via client/hello.device_info.
        # Empty strings mean "fall back to the bridge-wide identity" so MA
        # always sees something useful even when BlueZ Modalias is missing
        # or the speaker hasn't reported a friendly Name/Alias yet.
        self._bt_product_name = bt_product_name
        self._bt_manufacturer = bt_manufacturer
        self._required_lead_time_ms = required_lead_time_ms
        self._min_buffer_ms = min_buffer_ms
        # Initialise the synchronized state lazily so test fixtures that
        # build a BridgeDaemon without aiosendspin installed still work.
        try:
            from aiosendspin.models.types import PlayerStateType

            self._last_player_state = PlayerStateType.SYNCHRONIZED
        except Exception:
            self._last_player_state = None

    def _notify(self) -> None:
        """Notify subscriber that status has changed (no-op if no callback)."""
        if self._on_status_change:
            try:
                self._on_status_change()
            except Exception as exc:
                logger.warning("on_status_change callback failed: %s", exc)

    async def run(self) -> int:
        logger.info("Starting daemon: %s", self._args.client_id)
        loop = asyncio.get_running_loop()
        main_task = asyncio.current_task()
        assert main_task is not None

        def signal_handler() -> None:
            main_task.cancel()

        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal.SIGINT, signal_handler)
            loop.add_signal_handler(signal.SIGTERM, signal_handler)

        self._static_delay_ms = max(0.0, min(5000.0, self._args.static_delay_ms))
        await self._load_identity_and_pairing_store()
        sink_name = self._args.sink_name or self._bluetooth_sink_name
        self._player = StreamPlayer(
            sink_factory=default_pulsesink_factory(
                device=sink_name,
                client_name=self._args.client_name,
                buffer_time_us=max(1, int(self._args.pulse_latency_msec)) * 1000,
                slave_method=self._args.slave_method,
            )
        )
        self._audio_handler = self._player
        if self._bluetooth_sink_name:
            try:
                from sendspin_bridge.services.audio.pa_volume_controller import PulseVolumeController

                self._volume_controller = PulseVolumeController(self._bluetooth_sink_name)
                await self._volume_controller.start_monitoring(self._on_external_volume)
            except Exception as exc:
                logger.warning("PulseVolumeController unavailable: %s", exc)

        try:
            if self._args.url is not None:
                await self._run_client_initiated()
            else:
                await self._run_server_initiated()
        except asyncio.CancelledError:
            logger.debug("Daemon cancelled")
        finally:
            if self._volume_controller is not None:
                with contextlib.suppress(Exception):
                    await self._volume_controller.stop_monitoring()
            if self._player is not None:
                self._player.stop()
                self._player = None
            if self._client is not None:
                await self._client.disconnect()
                self._client = None
            if self._listener is not None:
                await self._listener.stop()
                self._listener = None
            logger.info("Daemon stopped")
        return 0

    async def _run_client_initiated(self) -> None:
        assert self._args.url is not None
        client = self._create_client(self._static_delay_ms)
        self._client = client
        await self._connection_loop(self._args.url)

    async def _connection_loop(self, url: str) -> None:
        from aiohttp import ClientError

        assert self._client is not None
        error_backoff = 1.0
        max_backoff = 300.0
        while True:
            try:
                await self._client.connect(url)
                error_backoff = 1.0
                disconnect_event = asyncio.Event()
                unsubscribe = self._client.add_disconnect_listener(disconnect_event.set)
                await disconnect_event.wait()
                unsubscribe()
                logger.info("Disconnected from server")
                await self._handle_disconnect()
                logger.info("Reconnecting to %s", url)
            except (TimeoutError, OSError, ClientError) as e:
                logger.warning("Connection error (%s), retrying in %.0fs", type(e).__name__, error_backoff)
                await asyncio.sleep(error_backoff)
                error_backoff = min(error_backoff * 2, max_backoff)
            except Exception:
                logger.exception("Unexpected error during connection")
                break

    async def _load_identity_and_pairing_store(self) -> None:
        identity_path = getattr(self._args, "identity_path", None)
        pairing_path = getattr(self._args, "pairing_store_path", None)
        if identity_path:
            from pathlib import Path

            from sendspin_bridge.services.ipc.client_identity import load_or_create_identity

            self._identity = load_or_create_identity(Path(identity_path))
        if pairing_path:
            from aiosendspin.noise.trust_store import FileClientPairingStore

            self._pairing_store = await FileClientPairingStore.open(pairing_path)

    def _pairing_support(self):
        try:
            from aiosendspin.client.models import PairingSupport
        except Exception:
            return None

        async def _pin_display(pin: str | None) -> None:
            self._bridge_status["pairing_pin"] = pin
            self._notify()

        return PairingSupport(pin_display=_pin_display)

    def open_pairing_window(self) -> None:
        client = getattr(self, "_client", None)
        opener = getattr(client, "open_pairing_window", None) if client is not None else None
        if callable(opener):
            opener()

    def _on_external_volume(self, volume: int, muted: bool) -> None:
        self._volume = volume
        self._muted = muted
        self._bridge_status["volume"] = volume
        self._bridge_status["muted"] = muted
        self._notify()

    def retarget_sink(self, sink_name: str | None) -> None:
        if self._player is None:
            return
        self._player.stop()
        self._player = StreamPlayer(
            sink_factory=default_pulsesink_factory(
                device=sink_name,
                client_name=self._args.client_name,
                buffer_time_us=max(1, int(self._args.pulse_latency_msec)) * 1000,
                slave_method=self._args.slave_method,
            )
        )
        self._audio_handler = self._player

    # ── Client creation ──────────────────────────────────────────────────────

    def _create_client(self, static_delay_ms: float = 0.0):
        """Create client with bridge-specific DeviceInfo and register all listeners."""
        from aiosendspin.models.player import ClientHelloPlayerSupport
        from aiosendspin.models.types import Roles

        from sendspin_bridge.services.infrastructure.call_kwargs import filter_supported_call_kwargs

        try:
            sw_ver = f"aiosendspin {_pkg_version('aiosendspin')}"
        except PackageNotFoundError:
            sw_ver = "aiosendspin"

        # Prefer the per-device BT-derived identity (read from BlueZ by the
        # parent at spawn time) so each bridged speaker shows up distinctly
        # in MA's player card. Fall back to the bridge-wide identity when
        # the BT props weren't resolvable (e.g. no Modalias for the vendor).
        # software_version always carries the bridge version so MA can
        # correlate behaviour across players regardless of the model surface.
        device_info = DeviceInfo(
            product_name=self._bt_product_name or f"Sendspin BT Bridge v{_BRIDGE_VERSION}",
            manufacturer=self._bt_manufacturer or socket.gethostname(),
            software_version=f"sendspin-bt-bridge {_BRIDGE_VERSION} ({sw_ver})",
        )

        client_roles = [Roles.PLAYER, Roles.METADATA, Roles.CONTROLLER]
        supported_formats = _declared_formats()

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
                "identity": getattr(self, "_identity", None),
                "pairing_store": getattr(self, "_pairing_store", None),
                "pairing_support": self._pairing_support(),
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
                "required_lead_time_ms": getattr(self, "_required_lead_time_ms", 250.0),
                "min_buffer_ms": getattr(self, "_min_buffer_ms", 250.0),
                "initial_volume": getattr(self, "_volume", 100),
                "initial_muted": getattr(self, "_muted", False),
                # client/state advertises SET_STATIC_DELAY so MA can drive the
                # per-player delay slider. On aiosendspin <5.1 the enum value
                # itself doesn't exist (resolved at module import as None);
                # we send an empty list in that case so filter_supported_call_kwargs
                # can drop the unknown kwarg without ever evaluating a
                # missing attribute. The pin (5.1.1) covers this in production.
                "state_supported_commands": ([_SET_STATIC_DELAY_CMD] if _SET_STATIC_DELAY_CMD is not None else []),
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
        if hasattr(client, "add_audio_chunk_listener"):
            client.add_audio_chunk_listener(self._on_audio_chunk)
        if hasattr(client, "add_stream_start_listener"):
            client.add_stream_start_listener(self._on_stream_start)
        if hasattr(client, "add_stream_end_listener"):
            client.add_stream_end_listener(lambda _roles: self._on_stream_event("stop"))
        if hasattr(client, "add_stream_clear_listener"):
            client.add_stream_clear_listener(lambda _roles: self._player.clear() if self._player else None)

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
                # Rebuild URL using server_port from status (or default 8927)
                port = self._bridge_status.get("server_port", 8927)
                self._bridge_status["connected_server_url"] = f"{peer}:{port}"
        except Exception as _exc:
            logger.debug("Could not extract peer address: %s", _exc)
        self._notify()

    async def _handle_disconnect(self) -> None:
        if self._player is not None:
            self._player.clear()
        self._on_server_disconnect()

    async def _run_server_initiated(self) -> None:
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

        sendspin 7.3.0 dropped the ``static_delay_ms`` parameter from this
        method; upstream now sets ``self._static_delay_ms`` from
        ``self._args``/``self._settings`` before calling here, so the
        override no longer needs the argument.  Mirror the upstream
        ``host=self._args.interface ...`` listener-bind addition (7.1.0)
        so the bridge honours the ``--interface`` daemon flag end-to-end.
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

        self._connection_lock = asyncio.Lock()

        interface = getattr(self._args, "interface", None)
        host = interface if interface is not None else "0.0.0.0"
        self._listener = _HeartbeatListener(
            client_id=self._args.client_id,
            on_connection=self._handle_server_connection,
            port=self._args.listen_port,
            client_name=self._args.client_name,
            host=host,
        )
        await self._listener.start()

        while True:
            await asyncio.sleep(3600)

    async def _handle_server_connection(self, ws) -> None:
        """Mirror the upstream connect flow without stale disconnect status races."""
        logger.info("Server connected")
        assert self._connection_lock is not None

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
            url = self._bridge_status.get("server_url")
            if url:
                # Outbound mode: the daemon dials MA — the URL is known and
                # SENDSPIN_PORT mismatches are a real cause.
                self._bridge_status["last_error"] = (
                    f"Cannot connect to Sendspin server at {url}. "
                    "Check that SENDSPIN_PORT matches your Music Assistant Sendspin port."
                )
            else:
                # Inbound mode (SENDSPIN_SERVER=auto): the daemon listens and
                # waits for MA to dial in after discovering the mDNS advert.
                # No URL exists, so blame discovery, not a phantom endpoint.
                self._bridge_status["last_error"] = (
                    "Waiting for Music Assistant to connect — no inbound Sendspin "
                    "connection received. Check mDNS/multicast reachability between "
                    "this host and MA (VPN, firewall, or VLAN filtering blocks the "
                    "_sendspin._tcp advert)."
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
        self._bridge_status["audio_format"] = f"{codec or 'PCM'} {sample_rate}Hz/{bit_depth}-bit/{channels}ch"
        self._bridge_status["audio_streaming"] = True  # actual audio data arrived
        self._bridge_status["reanchor_count"] = 0  # reset per-stream re-anchor counter
        self._bridge_status["reanchoring"] = False
        self._bridge_status["last_reanchor_at"] = None
        self._notify()

    def _on_stream_event(self, event: str) -> None:
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
        if payload.player is None:
            return
        cmd = payload.player
        if cmd.command == PlayerCommand.VOLUME and cmd.volume is not None:
            self._volume = max(0, min(100, cmd.volume))
            self._bridge_status["volume"] = self._volume
            self._notify()
            self._apply_sink_volume()
        elif cmd.command == PlayerCommand.MUTE and cmd.mute is not None:
            self._muted = cmd.mute
            self._bridge_status["muted"] = cmd.mute
            self._notify()
            self._apply_sink_volume()
        elif (
            _SET_STATIC_DELAY_CMD is not None
            and cmd.command == _SET_STATIC_DELAY_CMD
            and cmd.static_delay_ms is not None
        ):
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
            # IMPORTANT: also update the daemon-level cache. _handle_server_connection
            # rebuilds the aiosendspin client via _create_client(self._static_delay_ms)
            # on every server reconnect; without this the next reconnect would
            # snap the delay back to the previous value (the one we were spawned
            # with), undoing the MA-pushed change until the whole subprocess
            # restarts. This mirrors what the local IPC path in daemon_process.py
            # already does for parent-driven set_static_delay_ms commands.
            self._static_delay_ms = float(applied)
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

    def _apply_sink_volume(self) -> None:
        controller = getattr(self, "_volume_controller", None)
        if controller is None:
            return
        task = asyncio.get_running_loop().create_task(
            controller.set_state(self._volume, muted=self._muted)
        )

        def _done(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.warning("sink volume apply failed: %s", exc)

        task.add_done_callback(_done)

    def _on_stream_start(self, message) -> None:
        player_cfg = getattr(getattr(message, "payload", message), "player", None)
        if player_cfg is None:
            player_cfg = getattr(message, "player", None)
        if player_cfg is None:
            self._on_stream_event("start")
            return
        codec = getattr(player_cfg, "codec", None)
        codec_name = codec.value if hasattr(codec, "value") else str(codec or "PCM")
        sample_rate = int(getattr(player_cfg, "sample_rate", 48000))
        bit_depth = int(getattr(player_cfg, "bit_depth", 16))
        channels = int(getattr(player_cfg, "channels", 2))
        self._handle_format_change(codec_name, sample_rate, bit_depth, channels)
        if self._player is not None:
            self._player.start(PcmFormat(sample_rate=sample_rate, channels=channels, bit_depth=bit_depth))
        self._on_stream_event("start")

    def _on_audio_chunk(self, server_timestamp_us: int, payload: bytes, audio_format) -> None:
        if self._player is None:
            return
        pcm = getattr(audio_format, "pcm_format", audio_format)
        fmt = PcmFormat(
            sample_rate=int(getattr(pcm, "sample_rate", 48000)),
            channels=int(getattr(pcm, "channels", 2)),
            bit_depth=int(getattr(pcm, "bit_depth", 16)),
        )
        self._player.start(fmt)
        client = self._client
        play_time_us = (
            client.compute_play_time(server_timestamp_us)
            if client is not None and hasattr(client, "compute_play_time")
            else server_timestamp_us
        )
        self._player.submit(play_time_us, payload)

    def metrics(self) -> dict[str, object]:
        if self._player is None:
            return {}
        return self._player.metrics()


def _declared_formats():
    from aiosendspin.models.player import SupportedAudioFormat
    from aiosendspin.models.types import AudioCodec

    formats = []
    for sample_rate in (48000, 44100):
        for codec in (AudioCodec.FLAC, AudioCodec.PCM):
            formats.append(
                SupportedAudioFormat(codec=codec, channels=2, sample_rate=sample_rate, bit_depth=16)
            )
    return formats
