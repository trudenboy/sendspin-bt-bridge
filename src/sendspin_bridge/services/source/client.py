"""The Sendspin source client: the phone's capture offered to Music Assistant (#563).

The bridge is normally a Sendspin *client* that plays Music Assistant's stream on Bluetooth
speakers.  Here it is a client of the other role: it captures the phone's A2DP audio and
feeds it to Music Assistant's Sendspin server as a ``source`` client, so MA can play it like
any other source and owns the clock, the jitter buffer and the routing.

Two things drive the capture independently, matching ``sendspin_source``'s autostart:

* the phone's capture endpoint (running or not) is reported as the ``line_sense`` signal,
  which MA uses to decide whether the source is available;
* the server's ``server/command`` (``start``/``stop``) is what starts and stops the capture.

``aiosendspin`` 10.2.1 has no public ``send_source_signal``; :class:`AdmittedSignal` is the one
place that reaches into the private admitted connection, with a shape check and a pinned
dependency (see ``pyproject.toml``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sendspin_bridge.services.source import devices as source_devices
from sendspin_bridge.services.source import state as source_state
from sendspin_bridge.services.source.capture import CHUNK_MS, CaptureBackend, CaptureEndpoint, CaptureStream
from sendspin_bridge.services.source.identity import load_or_create_identity, open_pairing_store

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "AdmittedSignal",
    "SourceClient",
    "SourceError",
    "SourceSettings",
]

PAIRING_WINDOW_S = 300.0
CONNECT_TIMEOUT_S = 30.0
RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 30.0
WATCH_INTERVAL_S = 2.0
PAIRING_POLL_S = 1.0
STOP_ATTEMPTS = 5
STOP_RETRY_DELAY_S = 0.2
STOP_TIMEOUT_S = 5.0

#: Keep in sync with the exact aiosendspin pin in pyproject.toml.
AIOSENDSPIN_PIN = "aiosendspin==10.2.1"


class SourceError(RuntimeError):
    """A source command the API answers with a stable problem code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """Where the source connects and what it captures."""

    server_url: str
    state_dir: Path
    client_name: str = "Sendspin Bluetooth Bridge"
    sample_rate: int = 48000
    channels: int = 2
    target_player: str | None = None
    active_input: str | None = None


class SignalReporter(Protocol):
    """Report whether the phone's audio is present to the Sendspin server."""

    async def report(self, present: bool) -> bool: ...


class AdmittedSignal:
    """Send ``line_sense`` through the admitted connection, the one private aiosendspin use.

    ``SendspinClient`` forwards every other source call publicly, but 10.2.1 has no wrapper
    for ``send_source_signal``.  Re-checking the shape on every call keeps a future library
    release failing with an actionable error at this one site instead of an ``AttributeError``
    from inside aiosendspin.
    """

    _MISSING: Any = object()

    def __init__(self, client: Any) -> None:
        self._client = client

    async def report(self, present: bool) -> bool:
        from aiosendspin.models.types import SignalState

        connection = getattr(self._client, "_admitted_connection", self._MISSING)
        if connection is self._MISSING:
            raise RuntimeError(
                "aiosendspin's SendspinClient no longer has '_admitted_connection'; the source "
                f"is pinned to {AIOSENDSPIN_PIN} and this adapter must be updated"
            )
        if connection is None:
            return False
        send = getattr(connection, "send_source_signal", None)
        if not callable(send):
            raise RuntimeError(
                "aiosendspin's admitted connection no longer has send_source_signal(); the "
                f"source is pinned to {AIOSENDSPIN_PIN} and this adapter must be updated"
            )
        await send(SignalState.PRESENT if present else SignalState.ABSENT)
        return True


class SourceClient:
    """Owns the Sendspin source connection and the phone's capture for one bridge."""

    def __init__(
        self,
        settings: SourceSettings,
        capture: CaptureBackend,
        *,
        client_factory: Callable[[SourceSettings, Any, Any], Any] | None = None,
        signal_factory: Callable[[Any], SignalReporter] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._capture_backend = capture
        self._client_factory = client_factory or _default_client_factory
        self._signal_factory = signal_factory or (lambda client: AdmittedSignal(client))
        self._clock = clock
        self._client: Any = None
        self._identity: Any = None
        self._store: Any = None
        self._signal: SignalReporter | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._feed_task: asyncio.Task[None] | None = None
        self._subscription: Any = None
        self._stop = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._endpoint: CaptureEndpoint | None = None
        self._stream: CaptureStream | None = None
        self._sendspin_capture: Any = None
        self._stream_requested = False
        self._reported_signal: bool | None = None
        self._endpoint_lock = asyncio.Lock()
        self._capture_lock = asyncio.Lock()
        self._signal_lock = asyncio.Lock()
        self._preferred_mac: str | None = None
        self._target_player: str | None = settings.target_player
        self._active_input: str | None = settings.active_input

    # -- lifecycle -------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    async def start(self) -> dict[str, Any]:
        """Connect the source client; a second call is a no-op."""
        if self.running:
            return {"started": True, "already": True}
        self._stop.clear()
        self._disconnected.clear()
        self._identity = load_or_create_identity(self._settings.state_dir)
        self._store = await open_pairing_store(self._settings.state_dir)
        self._client = self._client_factory(self._settings, self._identity, self._store)
        self._signal = self._signal_factory(self._client)
        self._client.add_disconnect_listener(self._on_disconnected)
        self._client.add_server_command_listener(self._on_server_command)

        source_state.publish(
            enabled=True,
            state="waiting",
            server=self._settings.server_url,
            error=None,
            ma_connected=False,
            ma_error=None,
            phone=None,
            phone_mac=None,
            paired_phone_mac=None,
            streaming=False,
            ma_paired=await self._psk_paired(),
            target_player=self._target_player,
            target_error=None,
            active_input=None if self._active_input == "none" else self._active_input,
        )
        if not await self._psk_paired():
            await self._open_pairing_window()
        else:
            source_state.publish(state="waiting", ma_pairing_code=None, ma_pairing_until=0.0)
        self._run_task = asyncio.create_task(self._run(), name="source-client")
        self._watch_task = asyncio.create_task(self._watch(), name="source-watch")
        return {"started": True, "server": self._settings.server_url}

    async def stop(self) -> dict[str, Any]:
        """Disconnect and release everything; a second call is a no-op."""
        if not self.running and self._client is None:
            source_state.publish(enabled=False, state="off", ma_connected=False, streaming=False)
            return {"started": False, "already": True}
        self._stop.set()
        self._disconnected.set()
        for task in (self._watch_task, self._run_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._watch_task = None
        self._run_task = None
        await self._stop_capture()
        if self._subscription is not None:
            with contextlib.suppress(Exception):
                await self._subscription.stop()
            self._subscription = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None
        self._signal = None
        self._endpoint = None
        self._preferred_mac = None
        self._set_backend_preference(None)
        source_state.publish(
            enabled=False,
            state="off",
            phone=None,
            phone_mac=None,
            paired_phone_mac=None,
            streaming=False,
            ma_connected=False,
            ma_pairing_code=None,
            ma_pairing_until=0.0,
            ma_error=None,
            error=None,
            target_player=None,
            target_error=None,
            active_input=None,
        )
        return {"started": False}

    # -- pairing with Music Assistant ------------------------------------------------------

    async def open_ma_pairing(self) -> dict[str, Any]:
        """Open a pairing window and return the code the operator types into MA."""
        if not self.running:
            raise SourceError("source_off", "The source is off; enable it first")
        if await self._psk_paired():
            raise SourceError("ma_already_paired", "The bridge is already paired with Music Assistant")
        code = await self._open_pairing_window()
        return {
            "code": code,
            "seconds_left": source_state.snapshot().ma_pairing_seconds_left(),
            "client_id": getattr(self._identity, "peer_id", None),
        }

    async def cancel_ma_pairing(self, *, forget: bool = False) -> None:
        """Close the window; ``forget`` drops the stored PSK and starts a fresh window."""
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close_pairing_window()
        source_state.publish(ma_pairing_code=None, ma_pairing_until=0.0)
        if not forget:
            return
        if self._store is not None:
            await self._store.clear_pairing_psk()
            await self._store.clear_static_pairing_code()
            for record in await self._store.list_records():
                await self._store.remove_record(record.psk_id)
        source_state.publish(ma_paired=False, ma_error=None, state="pairing")
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
        self._disconnected.set()
        await self._open_pairing_window()

    @property
    def client_id(self) -> str | None:
        """The Sendspin peer id MA uses for this source client, when started."""
        return getattr(self._identity, "peer_id", None)

    @property
    def target_player(self) -> str | None:
        return self._target_player

    @property
    def active_input(self) -> str | None:
        return self._active_input

    def status(self) -> dict[str, Any]:
        return source_state.snapshot().status_dict()

    # -- connection ------------------------------------------------------------------------

    async def _run(self) -> None:
        delay = RECONNECT_MIN_S
        while not self._stop.is_set():
            self._disconnected.clear()
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code = _classify_connect_error(exc)
                logger.warning("Source: Music Assistant is not reachable (%s): %s", code, exc)
                source_state.publish(ma_connected=False, ma_error=code, state=await self._idle_state())
                await self._sleep_unless_stopped(delay)
                delay = min(delay * 2, RECONNECT_MAX_S)
                continue
            delay = RECONNECT_MIN_S
            await self._wait_for_disconnect()
            if not self._stop.is_set():
                logger.warning("Source: disconnected from Music Assistant; reconnecting")

    async def _connect_once(self) -> None:
        assert self._client is not None
        await asyncio.wait_for(self._client.connect(self._settings.server_url), timeout=CONNECT_TIMEOUT_S)
        paired = await self._psk_paired()
        source_state.publish(
            ma_connected=True,
            ma_error=None,
            ma_paired=paired,
            state="waiting" if paired else "pairing",
        )
        logger.info("Source: connected to Music Assistant at %s", self._settings.server_url)
        if paired:
            source_state.publish(ma_pairing_code=None, ma_pairing_until=0.0)
        await self._report_signal(force=True)
        await self._sync_endpoint()
        if self._target_player and self._endpoint is not None and self._stream is None:
            # A local input has no phone transport event to make MA ask for
            # the capture; starting it on the pinned target is the trigger.
            self._spawn(self._play_target_after_switch())

    async def _wait_for_disconnect(self) -> None:
        stop_task = asyncio.create_task(self._stop.wait())
        disconnect_task = asyncio.create_task(self._disconnected.wait())
        try:
            await asyncio.wait([stop_task, disconnect_task], return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (stop_task, disconnect_task):
                task.cancel()

    def _on_disconnected(self) -> None:
        source_state.publish(ma_connected=False, streaming=False)
        # The Sendspin capture is bound to the dead connection: release it and let a new
        # server start create a fresh one after the reconnect.
        self._spawn(self._stop_capture())
        if not self._stop.is_set():
            self._disconnected.set()

    # -- phone capture ---------------------------------------------------------------------

    async def _watch(self) -> None:
        # Polling, not ``pactl subscribe``: on the add-on every subscription event spawned a
        # watcher task whose subprocess transport leaked two sockets and an eventpoll, and a
        # busy PulseAudio exhausted the 1024-fd limit within minutes of enabling the source.
        # A 2 s poll is one short-lived helper call and keeps the same phone-detection latency.
        while not self._stop.is_set():
            try:
                await self._sync_endpoint()
                await self._check_pairing()
                await self._report_signal()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Source: capture watch failed", exc_info=True)
            await self._sleep_unless_stopped(WATCH_INTERVAL_S)

    async def _sync_endpoint(self) -> None:
        async with self._endpoint_lock:
            await self._sync_endpoint_locked()

    async def _sync_endpoint_locked(self) -> None:
        endpoint = await self._capture_backend.find()
        if endpoint is None and self._preferred_mac and not self._active_input:
            # The preferred device stopped streaming or disconnected: return to
            # the normal "newest active" policy instead of staying blind.
            self._preferred_mac = None
            self._set_backend_preference(None)
            endpoint = await self._capture_backend.find()
        if endpoint is None and self._active_input and self._active_input.startswith("local:"):
            if source_state.snapshot().error != "input_missing":
                source_state.publish(error="input_missing", state="error")
        elif endpoint is not None and source_state.snapshot().error == "input_missing":
            source_state.publish(error=None, state=await self._idle_state())
        if endpoint != self._endpoint:
            if self._stream is not None:
                await self._stop_capture()
                # One server start authorizes one capture opening: a replaced endpoint needs
                # a fresh start from MA, it must not silently reopen the previous grant.
                self._stream_requested = False
            self._endpoint = endpoint
            source_state.publish(
                phone=endpoint.node if endpoint else None,
                phone_mac=endpoint.phone_mac if endpoint else None,
                paired_phone_mac=(
                    endpoint.phone_mac
                    if endpoint is not None and endpoint.phone_mac
                    else source_state.snapshot().paired_phone_mac
                ),
            )
            if endpoint is not None and endpoint.phone_mac:
                await self._nudge_profile(endpoint)
            await self._report_signal(force=True)
            if endpoint is not None and self._stream_requested and self._client is not None:
                await self._start_capture()
            if endpoint is None:
                source_state.publish(streaming=False, state=await self._idle_state())

    def _set_backend_preference(self, mac: str | None) -> None:
        setter = getattr(self._capture_backend, "set_preferred_mac", None)
        if callable(setter):
            setter(mac)

    def _set_backend_active(self, source_id: str | None) -> None:
        setter = getattr(self._capture_backend, "set_active", None)
        if callable(setter):
            setter(source_id)
            return
        if source_id and source_id.startswith("bluetooth:"):
            self._set_backend_preference(source_id.split(":", 1)[1])
        else:
            self._set_backend_preference(None)

    async def set_active(self, source_id: str | None, *, play_target: bool = True) -> str | None:
        """Switch the captured input; None means "no active input", not auto-Bluetooth."""
        chosen = str(source_id or "").strip() or "none"
        if chosen == self._active_input:
            return None
        old = self._active_input
        was_streaming = self._stream is not None
        self._active_input = chosen
        self._set_backend_active(chosen)
        source_state.publish(active_input=None if chosen == "none" else chosen, target_error=None, error=None)
        if was_streaming:
            # One server start authorises one capture opening; the switched
            # input needs MA to ask again.
            self._stream_requested = False
            await self._stop_capture()
            self._endpoint = None
        await self._sync_endpoint()
        if chosen.startswith("local:") and self._endpoint is None:
            # The node vanished before we could open it: restore the previous
            # choice so a failed switch does not lose the operator's source.
            self._active_input = old
            self._set_backend_active(old)
            source_state.publish(active_input=None if old in (None, "none") else old)
            await self._sync_endpoint()
            raise SourceError("input_unavailable", "That local audio input is not present.")
        if play_target and self._target_player and self._endpoint is not None:
            return await self._play_target(allow_without_stream=True)
        if was_streaming:
            if not self._target_player:
                source_state.publish(target_error="press_play")
            source_state.publish(state=await self._idle_state(), streaming=False)
        return None

    async def _play_target_after_switch(self) -> None:
        await self._play_target(allow_without_stream=True)

    async def use_device(self, mac: str) -> None:
        """Capture *mac* explicitly; it must already be streaming into the host."""
        from sendspin_bridge.bluetooth.address import DeviceAddress

        address = DeviceAddress.parse(mac)
        if address is None:
            raise SourceError("invalid_mac", "A source device address looks like AA:BB:CC:DD:EE:FF.")
        if self._client is None or not self.running:
            raise SourceError("source_off", "The source is off; enable it first.")
        setter = getattr(self._capture_backend, "set_preferred_mac", None)
        if not callable(setter):
            raise SourceError("device_selection_unsupported", "This capture backend cannot select a device.")
        old = self._preferred_mac
        self._preferred_mac = address.colons
        self._set_backend_active(f"bluetooth:{address.colons}")
        endpoint = await self._capture_backend.find()
        if endpoint is None or endpoint.phone_mac != address.colons:
            self._preferred_mac = old
            self._set_backend_preference(old)
            raise SourceError(
                "device_not_streaming",
                "Connect the device and start audio on it before selecting it as the source.",
            )
        self._active_input = f"bluetooth:{address.colons}"
        source_state.publish(active_input=self._active_input)
        await self._sync_endpoint()

    async def forget_device(self, mac: str) -> None:
        """Stop capturing *mac* (when active), then remove its Bluetooth bond."""
        address = mac.upper()
        if self._endpoint is not None and (self._endpoint.phone_mac or "").upper() == address:
            self._stream_requested = False
            await self._stop_capture()
            self._endpoint = None
            source_state.publish(phone=None, phone_mac=None, streaming=False)
        if (self._preferred_mac or "").upper() == address:
            self._preferred_mac = None
            self._set_backend_preference(None)
        if (source_state.snapshot().paired_phone_mac or "").upper() == address:
            source_state.publish(paired_phone_mac=None)
        source_devices.invalidate()
        from sendspin_bridge.services.bluetooth import bt_remove_device

        await asyncio.to_thread(bt_remove_device, address)

    async def _nudge_profile(self, endpoint: CaptureEndpoint | None) -> None:
        nudge = getattr(self._capture_backend, "ensure_a2dp_source", None)
        if callable(nudge):
            with contextlib.suppress(Exception):
                await nudge(endpoint.phone_mac if endpoint is not None else None)

    async def _start_capture(self) -> None:
        async with self._capture_lock:
            await self._start_capture_locked()

    async def _start_capture_locked(self) -> None:
        if self._stream is not None or self._endpoint is None or self._client is None:
            return
        from aiosendspin.models.player import SupportedAudioFormat
        from aiosendspin.models.types import AudioCodec

        try:
            audio_format = SupportedAudioFormat(
                codec=AudioCodec.PCM,
                channels=self._settings.channels,
                sample_rate=self._settings.sample_rate,
                bit_depth=16,
            )
            sendspin_capture = self._client.create_source_capture(audio_format)
            await sendspin_capture.start()
        except RuntimeError as exc:
            # The server grants one capture opening per start; a start already consumed by a
            # previous endpoint cannot be reused.  Wait for MA to ask again.
            logger.warning("Source: could not start the capture yet: %s", exc)
            self._stream_requested = False
            return
        stream = await self._capture_backend.open(self._endpoint)
        await stream.start()
        self._sendspin_capture = sendspin_capture
        self._stream = stream
        self._feed_task = asyncio.create_task(self._feed(sendspin_capture, stream), name="source-feed")
        source_state.publish(streaming=True, state="streaming", error=None, target_error=None)
        logger.info("Source: streaming %s to Music Assistant", self._endpoint.node)
        if self._target_player:
            self._spawn(self._play_target_task())

    async def set_target(self, target: str | None) -> str | None:
        """Pin the source to an MA queue, or clear it. Returns an error code, if any."""
        self._target_player = str(target or "").strip() or None
        source_state.publish(target_player=self._target_player, target_error=None)
        if self._target_player and (self._stream is not None or self._endpoint is not None):
            return await self._play_target(allow_without_stream=True)
        return None

    async def _play_target_task(self) -> None:
        await self._play_target()

    async def _play_target(self, *, allow_without_stream: bool = False) -> str | None:
        """Start the live input on the selected MA queue when capture is running."""
        target = self._target_player
        if not target or (self._stream is None and not allow_without_stream):
            return None
        from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials
        from sendspin_bridge.services.source.target import SOURCE_URI_TEMPLATE, play_source_on

        ma_url, ma_token = get_ma_api_credentials()
        if not (ma_url and ma_token) or not self.client_id:
            source_state.publish(target_error="ma_api_unavailable")
            return "ma_api_unavailable"
        try:
            await play_source_on(
                ma_url,
                ma_token,
                target,
                SOURCE_URI_TEMPLATE.format(client_id=self.client_id),
            )
        except Exception:
            logger.warning("Source: could not start playback on MA target %s", target, exc_info=True)
            source_state.publish(target_error="target_unreachable")
            return "target_unreachable"
        source_state.publish(target_error=None)
        return None

    async def _feed(self, sendspin_capture: Any, stream: CaptureStream) -> None:
        try:
            async for chunk in stream.chunks():
                if self._client is None:
                    return
                await sendspin_capture.feed(chunk, self._client.now_us() - CHUNK_MS * 1000)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Source: the capture stream ended with an error", exc_info=True)
        finally:
            if self._stream is stream:
                self._stream = None
                with contextlib.suppress(Exception):
                    await stream.stop()
                sendspin_capture, self._sendspin_capture = self._sendspin_capture, None
                if sendspin_capture is not None:
                    await self._stop_sendspin_capture(sendspin_capture)
                self._stream_requested = False
                source_state.publish(streaming=False, state=await self._idle_state())
                await self._report_signal(force=True)

    async def _stop_capture(self) -> None:
        async with self._capture_lock:
            await self._stop_capture_locked()

    async def _stop_capture_locked(self) -> None:
        if self._feed_task is not None:
            self._feed_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._feed_task
            self._feed_task = None
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.stop()
        capture, self._sendspin_capture = self._sendspin_capture, None
        if capture is not None:
            await self._stop_sendspin_capture(capture)
        source_state.publish(streaming=False, state=await self._idle_state())

    @staticmethod
    async def _stop_sendspin_capture(capture: Any) -> None:
        for attempt in range(1, STOP_ATTEMPTS + 1):
            try:
                await asyncio.wait_for(capture.stop(), timeout=STOP_TIMEOUT_S)
                return
            except (RuntimeError, TimeoutError):
                if attempt == STOP_ATTEMPTS:
                    logger.warning("Source: capture did not stop after %d attempts; releasing it", STOP_ATTEMPTS)
                    return
                await asyncio.sleep(STOP_RETRY_DELAY_S)

    # -- server commands and the signal ----------------------------------------------------

    def _on_server_command(self, payload: Any) -> None:
        source = getattr(payload, "source", None)
        command = getattr(source, "command", None)
        if command == "start":
            self._stream_requested = True
            self._spawn(self._start_capture())
        elif command == "stop":
            self._stream_requested = False
            self._spawn(self._stop_capture())

    async def _report_signal(self, *, force: bool = False) -> None:
        async with self._signal_lock:
            present = bool(self._endpoint is not None and self._endpoint.running)
            if not force and present == self._reported_signal:
                return
            if self._signal is None:
                return
            try:
                sent = await self._signal.report(present)
            except RuntimeError as exc:
                if "not active" in str(exc):
                    # The server activates the source role after admission; a signal sent
                    # before that is retried by the watch loop.
                    return
                logger.exception("Source: could not report line_sense")
                source_state.publish(error="signal_adapter")
                return
            if sent:
                self._reported_signal = present
                logger.info("Source: line_sense %s", "present" if present else "absent")

    # -- pairing helpers -------------------------------------------------------------------

    async def _psk_paired(self) -> bool:
        """Whether the bridge holds a long-term record for Music Assistant.

        ``pairing_psk()`` is the short-lived PSK that admits a pairing attempt; the result
        of a successful pairing is a long-term *record*, so that is what "paired" means.
        """
        if self._store is None:
            return False
        return bool(await self._store.list_records())

    async def _open_pairing_window(self) -> str:
        assert self._store is not None and self._client is not None
        # The store starts with the static-code method disabled, so the client would abort
        # a server's static-code attempt as "method_not_supported" before showing any code.
        import dataclasses

        config = await self._store.get_pairing_config()
        if not config.static_pairing_code_enabled:
            await self._store.store_pairing_config(dataclasses.replace(config, static_pairing_code_enabled=True))
        code = await self._store.static_pairing_code()
        if not code:
            code = "".join(secrets.choice("0123456789") for _ in range(8))
            await self._store.set_static_pairing_code(code)
        self._client.open_pairing_window()
        until = self._clock() + PAIRING_WINDOW_S
        source_state.publish(
            ma_pairing_code=code,
            ma_pairing_until=until,
            state="pairing",
            ma_error=None,
        )
        logger.info("Source: Music Assistant pairing window open for %.0f s", PAIRING_WINDOW_S)
        return code

    async def _check_pairing(self) -> None:
        if await self._psk_paired():
            if not source_state.snapshot().ma_paired:
                logger.info("Source: paired with Music Assistant")
            source_state.publish(ma_paired=True, ma_pairing_code=None, ma_pairing_until=0.0, ma_error=None)
            if source_state.snapshot().state == "pairing":
                source_state.publish(state=await self._idle_state())
            return
        until = source_state.snapshot().ma_pairing_until
        if until and self._clock() >= until:
            source_state.publish(ma_pairing_code=None, ma_pairing_until=0.0, ma_error="ma_pairing_required")
            if self._client is not None:
                with contextlib.suppress(Exception):
                    await self._client.cancel_pairing()
                    await self._client.disconnect()
            self._disconnected.set()

    async def _idle_state(self) -> Literal["pairing", "waiting"]:
        return "pairing" if not await self._psk_paired() else "waiting"

    # -- small helpers ---------------------------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coro)
        task.add_done_callback(_log_task_exception)

    async def _sleep_unless_stopped(self, delay: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=delay)


def _log_task_exception(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Source: background task failed", exc_info=exc)


def _classify_connect_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "unpaired" in text or "pair" in text:
        return "ma_pairing_required"
    if "role" in text and "source" in text:
        return "ma_source_unsupported"
    return "ma_unreachable"


def _default_client_factory(settings: SourceSettings, identity: Any, store: Any) -> Any:
    from aiosendspin.client import PairingSupport, SendspinClient
    from aiosendspin.models.source import ClientHelloSourceFeatures, ClientHelloSourceSupport
    from aiosendspin.models.types import Roles

    return SendspinClient(
        identity,
        settings.client_name,
        [Roles.SOURCE],
        pairing_store=store,
        source_support=ClientHelloSourceSupport(features=ClientHelloSourceFeatures(line_sense=True)),
        pairing_support=PairingSupport(offer_static_pairing_code=True, secret_locations=("device",)),
    )
