"""Per-speaker daemon: aiosendspin client + GStreamer player + status callbacks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.ipc.commands import AirCommand

from aiosendspin.models.core import (
    DeviceInfo,
    GroupUpdateServerPayload,
    ServerCommandPayload,
    ServerStatePayload,
)
from aiosendspin.models.types import PlayerCommand, UndefinedField

from sendspin_bridge.config import VERSION as _BRIDGE_VERSION
from sendspin_bridge.services.audio.player.pipeline import default_pulsesink_factory, playout_pulsesink_factory
from sendspin_bridge.services.audio.player.player import InPlaceShift, PcmFormat, StreamPlayer
from sendspin_bridge.services.audio.playout.air_bias import AirFeed, AirSample
from sendspin_bridge.services.audio.playout.air_sensor import AirSensor, AirWindow
from sendspin_bridge.services.audio.playout.air_tap import INTERNAL_ERROR as _AIR_INTERNAL_ERROR
from sendspin_bridge.services.audio.playout.air_tap import WrittenEnvelope
from sendspin_bridge.services.audio.playout.engine import PlayoutSettings
from sendspin_bridge.services.audio.playout.gst_output import LEAD_MS as _PLAYOUT_LEAD_MS
from sendspin_bridge.services.audio.playout.gst_output import PlayoutPlayer, raw_now_us
from sendspin_bridge.services.audio.playout.stats import RateLimitedLog
from sendspin_bridge.services.ipc.commands import AIR_ENVELOPE_VERSION, SetAirCalibration
from sendspin_bridge.services.ipc.own_server import OwnServer, ServerGate, normalize_host, peer_host_var
from sendspin_bridge.services.ipc.status_store import StatusStore

UTC = timezone.utc

logger = logging.getLogger(__name__)

# Player commands the bridge accepts. Sendspin 1.0.0-rc1 declares them in
# client/state (never the hello) and names the delay command set_output_delay.
# Delay changes closer together than this are applied as one resync.
_DELAY_RESYNC_QUIET_S = 0.15
# A delay change up to this size is applied on the running output (the
# PulseAudio stream is kept, so a Bluetooth speaker does not re-buffer with a
# new offset); a larger one rebuilds the output.
_MAX_IN_PLACE_SHIFT_US = 2_000_000
# A compensated chunk must reach PulseAudio before the sink renders it, i.e.
# the sink's lookahead (PA "sink latency") ahead of its play time; the declared
# lead covers a measured lookahead plus this much.
_LEAD_MARGIN_MS = 50.0
# The modes that run the playout engine (PLAYOUT_SYNC); anything else is ``off``.
PLAYOUT_MODES = ("measure", "loop")
# In those modes a chunk must reach the engine before the PulseAudio buffer, the
# appsrc lead and this margin ahead of its play time: the declared start lead is
# raised to cover them.
_PLAYOUT_LEAD_MARGIN_MS = 100.0
_monotonic = time.monotonic
# Opening a PulseAudio output can take a second or more (a Bluetooth sink waits
# for its A2DP transport). It runs in a worker thread; the chunks that arrive
# meanwhile wait here (oldest dropped beyond this many, ~50 s of 25 ms chunks).
_MAX_OPENING_CHUNKS = 2048
# An open still running after this long is abandoned (its output is closed if it
# ever finishes); the next stream start or chunk opens anew.
_OPEN_TIMEOUT_S = 5.0
# #555: opening a Bluetooth output costs ~1.0-1.5 s, and a stream start that pays it
# while the first chunk is already due loses that chunk's head (110-210 ms dropped)
# and starts late. The daemon opens the output right after it starts instead, when the
# sink is known, and holds it until audio comes; a stream that starts in that window
# finds an open output. The hold ends when the output has been idle this long (the
# speaker can idle out again) or when the stream starts.
_PREOPEN_DELAY_S = 1.5
_PREOPEN_HOLD_S = 180.0
# A failed open is retried no sooner than this, doubling per failure up to the max.
_OPEN_RETRY_MIN_S = 0.5
_OPEN_RETRY_MAX_S = 10.0
# PyAV (the FLAC decoder) is imported in a worker thread this long after the
# daemon starts, so neither the handshake nor the first stream/start waits for it.
_DECODER_WARMUP_DELAY_S = 5.0


def _load_decoder_library() -> None:
    import importlib

    importlib.import_module("av")


def _warm_decoder_library() -> None:
    try:
        _load_decoder_library()
    except Exception as exc:  # the first FLAC stream then loads it (or reports it)
        logger.debug("decoder library warm-up failed: %s", exc)


# The stream start timing line waits until the output has settled.
_START_TIMING_DELAY_S = 2.0
_CLOCK_DIAG_INTERVAL_S = 1.0
_PLAYER_COMMANDS = [PlayerCommand.VOLUME, PlayerCommand.MUTE, PlayerCommand.SET_OUTPUT_DELAY]


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
    require_pairing: bool = False
    # ``pa``: volume and mute are the sink's own (PulseVolumeController).
    # ``sendspin``: they scale this daemon's stream; the sink is never touched.
    volume_controller: str = "pa"
    # Fix the pipeline latency to the PulseAudio buffer and take it out of
    # every timestamp (SENDSPIN_COMPENSATE_OUTPUT_LATENCY, default on).
    compensate_output_latency: bool = True
    # Let pulsesink provide its audio clock so its rate can be logged
    # (SENDSPIN_PLAYOUT_DIAG=1). The pipeline stays on the system clock.
    playout_diag: bool = False
    # PLAYOUT_SYNC: ``off`` keeps the legacy StreamPlayer (3.1.0-beta.5
    # behaviour); ``measure`` and ``loop`` run the playout engine.
    playout_mode: str = "off"
    # The spawn's ``playout`` dict: PlayoutSettings fields beyond the mode.
    playout: dict | None = None
    # The player's idle mode: ``power_save``/``auto_disconnect`` mean the speaker is meant to
    # sleep, so the output is not pre-opened (#555) and held against its idle timers.
    idle_mode: str = "default"
    # The spawn's ``air_sensor`` dict ({"enabled": True, "latency_ms": …}), sent only when the
    # sensor is effective for this speaker; None (switch off) = no sensor, no tap, no air commands.
    air_sensor: dict | None = None


class _AirCalibrationTee:
    """The air sink: every command goes to the sensor; a calibration also sets the feed's target."""

    def __init__(self, sensor: AirSensor, calibrate: Callable[..., None]) -> None:
        self._sensor = sensor
        self._calibrate = calibrate

    def submit(self, command: AirCommand) -> None:
        if isinstance(command, SetAirCalibration):
            self._calibrate(
                command.latency_ms,
                command.d_ref_ms,
                command.learned_ms,
                command.learned_d_ref_ms,
                command.learned_starts,
            )
        else:
            self._sensor.submit(command)


class AirSink(Protocol):
    """Where the daemon hands validated air commands (the AirSensor, wired in later).

    ``submit`` runs on the daemon loop and must not block; whatever it raises is
    counted and contained by :meth:`BridgeDaemon.air_intake`.
    """

    def submit(self, command: AirCommand) -> None: ...


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
        own_server: OwnServer | None = None,
        own_server_only: bool = True,
    ) -> None:
        self._args = args
        self._gate = ServerGate(
            own_server,
            enforce=own_server_only,
            on_refused=self._publish_refusals,
            logger=logger,
        )
        self._client: Any = None
        self._listener: Any = None
        self._connection_lock: asyncio.Lock | None = None
        self._player: StreamPlayer | PlayoutPlayer | None = None
        self._audio_handler: Any = None
        # The sink the current player plays to, and whether the playout engine
        # gave up for the rest of this process (see fall_back_to_legacy).
        self._sink_name: str | None = None
        self._playout_fell_back = False
        self._decoder: Any = None
        self._decoder_format: tuple[str, int, int, int, bytes | None] | None = None
        self._next_play_time_us: int | None = None
        self._pending_play_time_us: int | None = None
        self._pairing_window_task: asyncio.Task[None] | None = None
        self._volume = args.initial_volume
        self._muted = args.initial_muted
        self._volume_controller: Any = None
        self._static_delay_ms = max(0.0, min(5000.0, args.static_delay_ms))
        self._server_delay_seq = 0
        # The output delay the queued audio is timed with, the latest one set,
        # and the pending coalesced resync (see _on_output_delay_change).
        self._player_delay_ms: float | None = None
        self._target_delay_ms: float | None = None
        self._delay_resync_handle: asyncio.TimerHandle | None = None
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
        self._clock_diag_task: asyncio.Task[None] | None = None
        self._start_timing_task: asyncio.Task[None] | None = None
        # Largest PulseAudio sink lookahead measured at a stream start (ms).
        self._sink_lookahead_ms: float | None = None
        # What the last stream start that opened a new output cost, in ms:
        # {"output_open_ms", "loop_block_ms"} (the latter only when opened off the loop).
        self._start_cost: dict[str, float] | None = None
        self._decoder_warmup_task: asyncio.Task[None] | None = None
        # An output being opened off the loop, and the chunks waiting for it.
        self._opening: asyncio.Future[Any] | None = None
        self._opening_chunks: deque[tuple[int, bytes, PcmFormat]] = deque(maxlen=_MAX_OPENING_CHUNKS)
        self._opening_discard = False
        self._opening_began = 0.0
        self._loop_block_ms = 0.0
        self._opening_from_start = False
        # A pre-open (#555): the output is opened before any stream, so no input format is set at
        # adopt; the first start(format) does that. Its hold timers are cancelled by that start.
        self._opening_from_preopen = False
        self._opening_set_format = True
        self._preopen_handle: asyncio.TimerHandle | None = None
        self._preopen_expiry: asyncio.TimerHandle | None = None
        self._opening_watchdog: asyncio.TimerHandle | None = None
        self._open_failures = 0
        self._open_retry_at = 0.0
        self._warned_hung_open = False
        self._first_arrival_lead_us: int | None = None
        self._warned_min_latency = False
        # Air commands from the parent: validated ones go to the sink; without
        # one they are counted as ignored (R-6: the parent should not send them).
        self._air_sink: AirSink | None = None
        self.air_counters: dict[str, int] = {"accepted": 0, "ignored": 0, "invalid": 0, "errors": 0}
        self.air_log = RateLimitedLog(logger, clock=time.monotonic)
        # The air sensor (built only when the parent asked for it in measure mode), the tap
        # every playout player of this daemon writes into, and the loop its events run on.
        self._air_tap: WrittenEnvelope | None = None
        self._air_sensor: AirSensor | None = None
        self._air_feed: AirFeed | None = None
        self._air_loop: asyncio.AbstractEventLoop | None = None
        self._air_no_loop_logged = False
        self._build_air_sensor()

    def _build_air_sensor(self) -> None:
        """Build the sensor and take air commands, if the spawn asked for it (measure and loop modes)."""
        params = self._args.air_sensor
        if not isinstance(params, dict) or params.get("enabled") is not True:
            return
        if self._args.playout_mode not in ("measure", "loop"):
            return
        self._air_tap = WrittenEnvelope()
        if self._args.playout_mode == "loop":
            self._air_feed = AirFeed()
            self._air_feed.set_output_delay(self._air_output_delay_ms())
        self._air_sensor = AirSensor(
            tap=self._air_tap,
            now_us=raw_now_us,
            output_delay_ms=self._air_output_delay_ms,
            on_window=self._air_feed_push if self._air_feed is not None else None,
        )
        latency = params.get("latency_ms")
        self._air_calibrate(
            float(latency) if isinstance(latency, int | float) and not isinstance(latency, bool) else None,
            params.get("d_ref_ms"),
            params.get("learned_ms"),
            params.get("learned_d_ref_ms"),
            params.get("learned_starts"),
            sensor_unset_is_noop=True,
        )
        self.attach_air_sink(_AirCalibrationTee(self._air_sensor, self._air_calibrate))

    def _air_calibrate(
        self,
        latency_ms: float | None,
        d_ref_ms: float | None = None,
        learned_ms: float | None = None,
        learned_d_ref_ms: float | None = None,
        learned_starts: int | None = None,
        *,
        sensor_unset_is_noop: bool = False,
    ) -> None:
        """The calibration to the sensor and, in loop mode, the target to the feed."""
        sensor = self._air_sensor
        if sensor is not None and not (sensor_unset_is_noop and latency_ms is None):
            sensor.set_calibration(latency_ms)
        if self._air_feed is not None:
            self._air_feed.set_target(
                latency_ms=latency_ms,
                d_ref_ms=d_ref_ms,
                learned_ms=learned_ms,
                learned_d_ref_ms=learned_d_ref_ms,
                learned_starts=learned_starts,
            )

    def _air_feed_push(self, win: AirWindow, confidence: float | None) -> None:
        """The sensor's ``on_window`` hook (daemon loop): one accepted window to the engine's feed."""
        feed = self._air_feed
        if feed is None or confidence is None or not (win.x_lo_ns < win.x_hi_ns):
            return
        feed.push(
            AirSample(
                tap_gen=win.gen,
                x_lo_ns=win.x_lo_ns,
                x_hi_ns=win.x_hi_ns,
                at_us=win.at_us,
                h_us=win.h_us,
                d_out_us=win.d_out_us,
                confidence=confidence,
            )
        )

    def _air_output_delay_ms(self) -> float:
        """``D_out``: the output delay the queued audio is timed with (the static delay before the client runs)."""
        delay = self._player_delay_ms
        return float(self._static_delay_ms if delay is None else delay)

    def _air_output_delay_us(self) -> float:
        return self._air_output_delay_ms() * 1000.0

    def _air_engine_event(self, kind: str) -> None:
        """The player's listener (feeder or caller thread): the sensor sees the event on the loop.

        Without a usable loop the event is dropped (the sensor is loop-only, never run on the
        feeder); after a fallback the retired player's events are dropped too.
        """
        if self._air_sensor is None or self._playout_fell_back:
            return
        loop = self._air_loop
        if loop is None or loop.is_closed():
            if not self._air_no_loop_logged:
                self._air_no_loop_logged = True
                logger.debug("air sensor: no event loop for engine events; dropping %s", kind)
            return
        try:
            loop.call_soon_threadsafe(self._air_on_loop, kind)
        except RuntimeError:  # the loop closed in between
            logger.debug("air sensor: engine event %s dropped (loop closed)", kind)

    def _air_on_loop(self, kind: Any) -> None:
        sensor = self._air_sensor
        if sensor is None or self._playout_fell_back:
            return
        try:
            sensor.on_engine_event(kind)
        except Exception:
            logger.debug("air sensor: engine event %s failed", kind, exc_info=True)

    async def air_step(self) -> None:
        """One sensor step (daemon loop); whatever it raises disables only the sensor."""
        sensor = self._air_sensor
        if sensor is None:
            return
        try:
            await sensor.step()
            feed = self._air_feed
            if feed is not None:
                feed.set_lock(*sensor.lock_state())
                feed.set_key(*sensor.key())
        except Exception:
            with contextlib.suppress(Exception):
                sensor.fail()

    def air_snapshot(self) -> dict[str, object] | None:
        """The ``air_sensor`` status (daemon loop); None when no sensor was built (no status key)."""
        sensor = self._air_sensor
        if sensor is None:
            return None
        try:
            snap = dict(sensor.snapshot())
        except Exception:
            with contextlib.suppress(Exception):
                sensor.fail()
            snap = {"state": "unavailable", "reason": _AIR_INTERNAL_ERROR}
        snap["accepts"] = self.air_accepts()
        return snap

    def attach_air_sink(self, sink: AirSink) -> None:
        self._air_sink = sink

    def detach_air_sink(self) -> None:
        self._air_sink = None

    def air_accepts(self) -> list[int]:
        """The air payload versions this daemon takes now (``air_sensor.accepts``)."""
        return [AIR_ENVELOPE_VERSION] if self._air_sink is not None else []

    def air_intake(self, command: AirCommand) -> None:
        """Hand one validated air command to the sink; never raises."""
        sink = self._air_sink
        if sink is None:
            self.air_ignore()
            return
        try:
            sink.submit(command)
        except Exception as exc:
            self.air_counters["errors"] += 1
            self.air_log.warning("errors", "Air sensor rejected a command: %s", type(exc).__name__)
            return
        self.air_counters["accepted"] += 1

    def air_ignore(self) -> None:
        """Count one air command that arrived while no sensor takes them."""
        self.air_counters["ignored"] += 1
        self.air_log.warning("ignored", "Ignoring air commands: this daemon has no air sensor")

    def air_note_invalid(self, reason: str) -> None:
        """Count one dropped air message; ``reason`` names the field, never its value."""
        self.air_counters["invalid"] += 1
        self.air_log.warning("invalid", "Dropping invalid air command (%s); one warning per 10 min", reason)

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
        if self._air_sensor is not None:
            self._air_loop = loop  # engine events from the feeder threads are handed to this loop
        main_task = asyncio.current_task()
        assert main_task is not None

        def signal_handler() -> None:
            main_task.cancel()

        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signal.SIGINT, signal_handler)
            loop.add_signal_handler(signal.SIGTERM, signal_handler)

        self._static_delay_ms = max(0.0, min(5000.0, self._args.static_delay_ms))
        # Report the delay the client starts with, so the parent shows it before
        # Music Assistant ever sends one.
        self._bridge_status["static_delay_ms"] = round(self._static_delay_ms)
        await self._load_identity_and_pairing_store()
        if self._identity is not None:
            # MA keys the player by this id, not by the bridge's player id.
            self._bridge_status["sendspin_client_id"] = self._identity.peer_id
        self._client = self._create_client(self._static_delay_ms)
        sink_name = self._args.sink_name or self._bluetooth_sink_name
        self._player = self._make_player(sink_name)
        self._audio_handler = self._player
        self._schedule_preopen()
        self._clock_diag_task = asyncio.create_task(self._clock_diag_loop())
        # Loaded on the first FLAC stream/start, PyAV's import would hold the
        # loop (0.07 s warm, seconds from a cold disk); load it in a worker
        # thread once the daemon has settled instead.
        self._decoder_warmup_task = asyncio.create_task(self._warm_decoder_later())
        if self._scales_stream:
            self._apply_stream_volume()
        elif self._bluetooth_sink_name:
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
            if self._delay_resync_handle is not None:
                self._delay_resync_handle.cancel()
                self._delay_resync_handle = None
            if self._opening is not None:
                # The worker thread cannot be cancelled: let it finish and close its output.
                self._opening_discard = True
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(self._opening), timeout=5.0)
            if self._opening_watchdog is not None:
                self._opening_watchdog.cancel()
                self._opening_watchdog = None
            for task in (self._clock_diag_task, self._start_timing_task, self._decoder_warmup_task):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            self._clock_diag_task = None
            self._start_timing_task = None
            self._decoder_warmup_task = None
            if self._pairing_window_task is not None:
                self._pairing_window_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._pairing_window_task
                self._pairing_window_task = None
            if self._volume_controller is not None:
                with contextlib.suppress(Exception):
                    await self._volume_controller.stop_monitoring()
            if self._player is not None:
                self._player.stop()
                self._player = None
            if self._client is not None:
                from aiosendspin.models.types import GoodbyeReason

                # Every stop the parent orders brings the daemon back (link
                # drop, standby, config change, bridge restart). ``shutdown``
                # tells the server not to auto-reconnect, and Music Assistant
                # then never dialled the respawned daemon again.
                await self._client.disconnect(GoodbyeReason.RESTART)
                self._client = None
            if self._listener is not None:
                await self._listener.stop()
                self._listener = None
            logger.info("Daemon stopped")
        return 0

    async def _run_client_initiated(self) -> None:
        assert self._args.url is not None
        await self._connection_loop(self._args.url)

    async def _connection_loop(self, url: str) -> None:
        from aiohttp import ClientError

        assert self._client is not None
        error_backoff = 1.0
        max_backoff = 300.0
        while True:
            try:
                await self._client.connect(url)
                # connect() returns once the server admitted us.
                self._mark_server_connected(None, url=url)
                # The player role waits for the first client/state: aiosendspin sends none on
                # its own, so a plain Sendspin server (the bridge's own source, #563) would
                # never start the stream. Sent a moment later: a state pushed in the same tick
                # as the admission is dropped before the server's handshake finishes.
                asyncio.get_running_loop().call_later(1.0, self._report_player_state)
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
            await self._apply_pairing_policy()
        await self._refresh_pairing_state()

    async def _refresh_pairing_state(self) -> None:
        if not bool(getattr(self._args, "require_pairing", False)):
            self._bridge_status["pairing_state"] = "disabled"
            self._notify()
            return
        store = getattr(self, "_pairing_store", None)
        records = await store.list_records() if store is not None else ()
        self._bridge_status["pairing_state"] = (
            "paired" if any(getattr(record, "server_id", None) for record in records) else "unpaired"
        )
        self._notify()

    async def _apply_pairing_policy(self) -> None:
        store = getattr(self, "_pairing_store", None)
        if store is None:
            return
        from dataclasses import replace

        require_pairing = bool(getattr(self._args, "require_pairing", False))
        current = await store.get_pairing_config()
        desired = replace(
            current,
            unpaired_access_enabled=not require_pairing,
            dynamic_pairing_code_enabled=require_pairing,
            static_pairing_code_enabled=False,
        )
        if desired != current:
            await store.store_pairing_config(desired)
            logger.info(
                "Sendspin pairing %s (unpaired access %s)",
                "required" if require_pairing else "disabled",
                "off" if require_pairing else "on",
            )

    def _pairing_support(self):
        if not bool(getattr(self._args, "require_pairing", False)):
            return None
        try:
            from aiosendspin.client.models import PairingSupport
        except Exception:
            return None

        async def _pairing_code_display(code: str | None, *, grouped: str | None = None) -> None:
            # The grouped form ("123 456") is the one meant for people.
            shown = grouped or code
            self._bridge_status["pairing_pin"] = shown
            if shown:
                logger.debug("Sendspin pairing code is ready for display")
                self._bridge_status["pairing_state"] = "pin_displayed"
            else:
                self._bridge_status["pairing_window_open"] = False
                await self._refresh_pairing_state()
            self._notify()

        return PairingSupport(pairing_code_display=_pairing_code_display)

    def open_pairing_window(self) -> None:
        client = getattr(self, "_client", None)
        opener = getattr(client, "open_pairing_window", None) if client is not None else None
        if callable(opener):
            opener()
            self._bridge_status["pairing_window_open"] = bool(getattr(client, "pairing_window_open", True))
            self._bridge_status["pairing_state"] = "window_open"
            self._notify()
            if self._pairing_window_task is None or self._pairing_window_task.done():
                self._pairing_window_task = asyncio.create_task(self._watch_pairing_window(client))

    async def _watch_pairing_window(self, client: object) -> None:
        while bool(getattr(client, "pairing_window_open", False)):
            await asyncio.sleep(0.05)
        self._bridge_status["pairing_window_open"] = False
        if not self._bridge_status.get("pairing_pin"):
            await self._refresh_pairing_state()
        self._notify()

    def _on_external_volume(self, volume: int, muted: bool) -> None:
        """The sink changed outside Music Assistant (the bridge's slider, the
        speaker's own buttons). The daemon holds the controller's only
        callback, so it is also the one that tells Music Assistant — otherwise
        MA keeps the old value and restores it later."""
        self._volume = volume
        self._muted = muted
        self._bridge_status["volume"] = volume
        self._bridge_status["muted"] = muted
        self._notify()
        self._report_player_state()

    @property
    def _playout_active(self) -> bool:
        """Whether new players run the playout engine (PLAYOUT_SYNC on, no fallback yet)."""
        args = getattr(self, "_args", None)
        return getattr(args, "playout_mode", "off") in PLAYOUT_MODES and not getattr(self, "_playout_fell_back", False)

    def _make_player(self, sink_name: str | None) -> StreamPlayer | PlayoutPlayer:
        """The player for ``sink_name``: the playout engine in ``measure``/``loop``
        (until a fallback), else the legacy StreamPlayer."""
        self._sink_name = sink_name
        if not self._playout_active:
            return self._new_player(sink_name)
        return self._new_playout_player(sink_name)

    def _playout_settings(self) -> PlayoutSettings:
        known = {f for f in PlayoutSettings.__dataclass_fields__ if f != "mode"}
        extra = {k: v for k, v in (self._args.playout or {}).items() if k in known}
        mode: Literal["measure", "loop"] = "loop" if self._args.playout_mode == "loop" else "measure"
        extra.setdefault("rate", os.environ.get("PLAYOUT_RATE", "").strip().lower() in ("1", "true", "yes"))
        return PlayoutSettings(mode=mode, **extra)

    def _new_playout_player(self, sink_name: str | None) -> PlayoutPlayer:
        buffer_time_us = max(1, int(self._args.pulse_latency_msec)) * 1000
        air: dict[str, Any] = {}
        if self._air_sensor is not None:
            with contextlib.suppress(RuntimeError):
                self._air_loop = asyncio.get_running_loop()
            air = {
                "air_tap": self._air_tap,
                "output_delay_us": self._air_output_delay_us,
                "on_engine_event": self._air_engine_event,
                "air_feed": self._air_feed,
            }
        return PlayoutPlayer(
            sink_factory=playout_pulsesink_factory(
                device=sink_name,
                client_name=self._args.client_name,
                buffer_time_us=buffer_time_us,
            ),
            settings=self._playout_settings(),
            **air,
        )

    def _retire_player(self, player: Any) -> None:
        """Stop a replaced player.

        The legacy StreamPlayer is stopped here, as in 3.1.0-beta.5 (off-mode
        parity). PlayoutPlayer's stop can block (it joins its feeder, up to
        2 s, and sets its pipeline to NULL), so with a running loop it runs in
        a worker; without one (start-up, tests) here.
        """
        if not isinstance(player, PlayoutPlayer):
            player.stop()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            player.stop()
            return

        def _stopped(done: asyncio.Future[Any]) -> None:
            if not done.cancelled() and done.exception() is not None:
                logger.warning("Stopping the replaced player failed: %s", done.exception())

        loop.run_in_executor(None, player.stop).add_done_callback(_stopped)

    def fall_back_to_legacy(self, reason: str) -> None:
        """Replace the playout engine with the legacy StreamPlayer for the rest of the process.

        Called once by the telemetry watcher when the engine reports the
        ``fallback`` state (no audio clock from the sink, a GStreamer error, a
        feeder failure). The sink, the volume and the mute are kept. The status
        keeps ``playout`` with ``state: "fallback"`` and the reason, so the
        operator sees why the engine is not running.
        """
        if self._playout_fell_back:
            return
        self._playout_fell_back = True
        logger.warning("Playout engine unavailable (%s); falling back to the legacy player", reason)
        previous = self._bridge_status.get("playout")
        playout = dict(previous) if isinstance(previous, dict) else {}
        playout.update(state="fallback", fallback_reason=reason)
        self._bridge_status["playout"] = playout
        sensor = self._air_sensor
        if sensor is not None:
            # R-6: unavailable/playout_fallback, and the parent stops sending (accepts = []).
            self.detach_air_sink()
            with contextlib.suppress(Exception):
                sensor.on_engine_event("fallback")
        old = self._player
        # An open in flight belongs to the engine: its output is closed when it finishes.
        self._forget_open()
        self._player = self._make_player(self._sink_name)
        self._audio_handler = self._player
        self._cancel_preopen()
        self._schedule_preopen()
        if old is not None:
            self._retire_player(old)
        if self._scales_stream:
            self._apply_stream_volume()
        self._notify()

    def _new_player(self, sink_name: str | None) -> StreamPlayer:
        buffer_time_us = max(1, int(self._args.pulse_latency_msec)) * 1000
        return StreamPlayer(
            sink_factory=default_pulsesink_factory(
                device=sink_name,
                client_name=self._args.client_name,
                buffer_time_us=buffer_time_us,
                slave_method=self._args.slave_method,
                provide_clock=self._args.playout_diag,
            ),
            output_latency_ns=buffer_time_us * 1000 if self._args.compensate_output_latency else 0,
            max_in_place_shift_us=_MAX_IN_PLACE_SHIFT_US,
            # A rebuild's new output is opened like a stream start's: in a
            # worker, never on the loop.
            defer_rebuild_open=True,
        )

    def declared_lead_time_ms(self, value_ms: float) -> float:
        """The startup lead to declare to the server for a configured value.

        A compensated chunk only has to reach PulseAudio before the sink
        renders it: the sink's own lookahead ahead of its play time. Measured
        on PulseAudio 17 and pipewire-pulse null sinks at buffer 200/600/800 ms,
        a steady 60 ms lead played every chunk on time, so the configured lead
        stands until a stream start measures a sink lookahead that, with a
        margin, needs more. The minimum buffer is never changed.

        The output delay is not part of it: the server adds that on top
        (required_lead_time_ms "excludes output_delay_ms"). Nor is the time an
        output takes to open: that no longer holds the loop, and covering it
        would delay every start of the whole group.
        """
        value_ms = self._effective_lead_for(value_ms)
        lookahead = getattr(self, "_sink_lookahead_ms", None)
        if lookahead is None or not self._args.compensate_output_latency:
            return float(value_ms)
        return max(float(value_ms), lookahead + _LEAD_MARGIN_MS)

    def _effective_lead_for(self, configured_ms: float) -> float:
        """A configured lead raised, in ``measure``/``loop``, to cover the output buffer.

        The engine writes a chunk into a PulseAudio buffer of PULSE_LATENCY_MSEC
        behind an appsrc lead of LEAD_MS: a chunk that arrives later than both
        (plus a margin) ahead of its play time is partly dropped as overdue.
        """
        args = getattr(self, "_args", None)
        if getattr(args, "playout_mode", "off") not in PLAYOUT_MODES:
            return float(configured_ms)
        floor = float(getattr(args, "pulse_latency_msec", 0)) + _PLAYOUT_LEAD_MS + _PLAYOUT_LEAD_MARGIN_MS
        return max(float(configured_ms), floor)

    def _effective_lead_ms(self) -> float:
        """The configured start lead after the playout floor (before any sink lookahead)."""
        return self._effective_lead_for(getattr(self, "_required_lead_time_ms", 250.0))

    def _note_sink_lookahead(self, sink_ms: float) -> None:
        """Keep the largest sink lookahead seen this session (the lead only grows)."""
        if self._sink_lookahead_ms is None or sink_ms > self._sink_lookahead_ms:
            self._sink_lookahead_ms = float(sink_ms)

    def retarget_sink(self, sink_name: str | None) -> None:
        if self._player is None:
            return
        # An open in flight belongs to the old sink: its output is closed when it finishes.
        self._forget_open()
        old = self._player
        if not isinstance(old, PlayoutPlayer):
            # Legacy player: stopped before the new one, exactly as before playout sync.
            old.stop()
        self._player = self._make_player(sink_name)
        self._audio_handler = self._player
        if isinstance(old, PlayoutPlayer):
            self._retire_player(old)
        if self._scales_stream:
            self._apply_stream_volume()

    # ── Stream volume (volume_controller: sendspin) ──────────────────────────

    @property
    def _scales_stream(self) -> bool:
        return self._args.volume_controller == "sendspin"

    def _apply_stream_volume(self) -> None:
        """Set the stream's gain on PulseAudio's cubic percent scale, never the sink's."""
        if self._player is None:
            return
        self._player.set_volume((self._volume / 100) ** 3, self._muted)

    def apply_bridge_volume(self, *, volume: int | None = None, muted: bool | None = None) -> None:
        """Apply a volume or mute change made on the bridge (its slider or API), not by MA.

        Under ``pa`` the parent already changed the sink and the controller
        reports it to Music Assistant: nothing to do here.  Under ``sendspin``
        the change is the stream's, so the daemon applies it and tells MA.
        """
        if not self._scales_stream:
            return
        if volume is not None:
            self._volume = volume
        if muted is not None:
            self._muted = muted
        self._apply_stream_volume()
        self._report_player_state()

    # ── Client creation ──────────────────────────────────────────────────────

    def _declared_lead_for_client(self) -> float:
        lead = self.declared_lead_time_ms(getattr(self, "_required_lead_time_ms", 250.0))
        status = getattr(self, "_bridge_status", None)
        if status is not None:
            status["declared_lead_time_ms"] = round(lead)
            status["effective_required_lead_time_ms"] = round(self._effective_lead_ms())
        return lead

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
                "client_name": self._args.client_name,
                "roles": client_roles,
                "device_info": device_info,
                "player_support": ClientHelloPlayerSupport(
                    supported_formats=supported_formats,  # type: ignore[arg-type]
                    buffer_capacity=32_000_000,
                ),
                "output_delay_ms": static_delay_ms,
                "required_lead_time_ms": self._declared_lead_for_client(),
                "min_buffer_ms": getattr(self, "_min_buffer_ms", 250.0),
                "initial_volume": getattr(self, "_volume", 100),
                "initial_muted": getattr(self, "_muted", False),
                # Declared in client/state: volume, mute and set_output_delay
                # (the per-player delay slider in MA).
                "state_supported_commands": list(_PLAYER_COMMANDS),
            },
        )

        client = self._own_server_client_class(_AioSendspinClient)(**client_kwargs)  # type: ignore[arg-type]

        client.add_group_update_listener(self._on_group_update)
        client.add_metadata_listener(self._on_metadata_update)
        client.add_controller_state_listener(self._on_controller_state)
        client.add_disconnect_listener(self._on_server_disconnect)
        client.add_server_command_listener(self._handle_server_command)
        if hasattr(client, "add_output_delay_listener"):
            if self._delay_resync_handle is not None:
                self._delay_resync_handle.cancel()
                self._delay_resync_handle = None
            self._player_delay_ms = float(getattr(client, "output_delay_ms", static_delay_ms))
            self._target_delay_ms = self._player_delay_ms
            if self._air_feed is not None:
                self._air_feed.set_output_delay(self._player_delay_ms)
            client.add_output_delay_listener(self._on_output_delay_change)

        # Register visualizer listener if available
        if hasattr(client, "add_visualizer_listener"):
            client.add_visualizer_listener(self._on_visualizer_frames)
        if hasattr(client, "add_audio_chunk_listener"):
            client.add_audio_chunk_listener(self._on_audio_chunk)
        if hasattr(client, "add_stream_start_listener"):
            client.add_stream_start_listener(self._on_stream_start)
        if hasattr(client, "add_stream_end_listener"):
            client.add_stream_end_listener(self._on_stream_end)
        if hasattr(client, "add_stream_clear_listener"):
            client.add_stream_clear_listener(self._on_stream_clear)

        return client

    @property
    def _server_gate(self) -> ServerGate:
        """The admission gate; a daemon built without one accepts every server."""
        gate = self.__dict__.get("_gate")
        if gate is None:
            gate = self.__dict__["_gate"] = ServerGate(None, on_refused=self._publish_refusals, logger=logger)
        return gate

    def _publish_refusals(self, recent: list[dict]) -> None:
        self._bridge_status["refused_servers"] = recent
        self._notify()

    def _own_server_client_class(self, base: type) -> type:
        """The aiosendspin client, with admission limited to the bridge's own server.

        aiosendspin 10 has no public admission hook: whether an incoming server
        takes the player is decided by ``_should_admit_connection``, and a loser
        is told off by ``_reject_connection``.  Both are the seams its own
        multi-server arbitration is built on, so the bridge overrides exactly
        those two.  A refused server gets ``client/goodbye`` with reason
        ``restart``: the server keeps retrying with its exponential backoff
        (capped at five minutes), so a refused server costs one short session
        every few minutes, and the bridge's own server comes back by itself
        once a wrong setting is fixed.  Every other goodbye ends the server's
        retry loop, after which it dials again only on an mDNS change, which
        a restarted bridge does not reliably produce (seen live).  If a
        future aiosendspin drops either method the check degrades to accepting
        every server, with a warning, rather than breaking connections.
        """
        gate = self._server_gate
        if not (hasattr(base, "_should_admit_connection") and hasattr(base, "_reject_connection")):
            logger.warning("aiosendspin has no admission seam: connecting to any Music Assistant server")
            return base

        from aiosendspin.models.types import GoodbyeReason

        class _OwnServerClient(base):  # type: ignore[misc, valid-type]
            def _should_admit_connection(self, incoming):
                if not gate.admits(incoming):
                    return False
                return super()._should_admit_connection(incoming)

            async def _reject_connection(self, connection):
                if gate.take_refused(connection):
                    await self._dismiss_connection(connection, GoodbyeReason.RESTART)
                    await connection.disconnect()
                    return
                await super()._reject_connection(connection)

        return _OwnServerClient

    # ── Connection lifecycle ─────────────────────────────────────────────────

    def _mark_server_connected(self, ws, url: str | None = None) -> None:
        """Publish bridge status only after the new server handshake succeeds.

        ``ws`` is the inbound socket (Music Assistant dialled us); ``url`` the
        server the daemon dialled itself.
        """
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
        if url:
            self._bridge_status["connected_server_url"] = url
        self._publish_connected_server(ws, url)
        self._notify()

    def _publish_connected_server(self, ws, url: str | None) -> None:
        """Say which server the player is connected to, and whether it is the bridge's own."""
        info = getattr(self._client, "server_info", None)
        server_id = getattr(info, "server_id", None)
        host: str | None = None
        if url:
            with contextlib.suppress(ValueError):
                host = urlsplit(url).hostname
        else:
            host = getattr(getattr(ws, "_req", None), "remote", None)
        verdict = self._server_gate.verdict_for(server_id, host)
        if verdict.own and server_id:
            self._server_gate.trusted_ids.add(server_id)
        self._bridge_status["connected_server_name"] = getattr(info, "name", None) or None
        self._bridge_status["connected_server_id"] = server_id
        self._bridge_status["connected_server_host"] = normalize_host(host) or None
        self._bridge_status["connected_server_own"] = verdict.own

    async def _handle_disconnect(self) -> None:
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
            client_id=self._identity.peer_id,
            on_connection=self._handle_server_connection,
            port=self._args.listen_port,
            client_name=self._args.client_name,
            host=host,
        )
        await self._listener.start()

        while True:
            await asyncio.sleep(3600)

    async def _handle_server_connection(self, ws) -> None:
        """Give an inbound socket to the persistent client's admission machinery."""
        logger.info("Server connected")
        client = self._client
        if client is None:
            raise RuntimeError("Sendspin client is not initialized")

        async def _publish_when_connected() -> None:
            while True:
                admitted = getattr(client, "_admitted_connection", None)
                transport = getattr(admitted, "_ws", None)
                raw_socket = getattr(transport, "_ws", transport)
                owns_socket = admitted is None or raw_socket is ws
                if getattr(client, "connected", False) and owns_socket:
                    self._mark_server_connected(ws)
                    return
                await asyncio.sleep(0.05)

        peer_host_var.set(getattr(getattr(ws, "_req", None), "remote", None))
        publisher = asyncio.create_task(_publish_when_connected())
        try:
            await client.attach_websocket(ws)
        except Exception:
            logger.exception("Error serving inbound server connection")
        finally:
            publisher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await publisher

    def _on_server_disconnect(self) -> None:
        """Clear connection + group state on disconnect."""
        already_disconnected = (
            not self._bridge_status.get("server_connected")
            and not self._bridge_status.get("connected")
            and self._bridge_status.get("group_id") is None
            and self._decoder is None
            and self._next_play_time_us is None
            and self._pending_play_time_us is None
            and not self._bridge_status.get("audio_streaming")
        )
        if already_disconnected:
            return
        if self._player is not None:
            self._player.clear()
        self._decoder = None
        self._decoder_format = None
        self._next_play_time_us = None
        self._pending_play_time_us = None
        self._bridge_status["server_connected"] = False
        self._bridge_status["connected"] = False
        self._bridge_status["group_name"] = None
        self._bridge_status["group_id"] = None
        # A stale "foreign server" warning must not outlive the connection.
        for key in ("connected_server_name", "connected_server_id", "connected_server_host", "connected_server_own"):
            self._bridge_status[key] = None
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
        # Volume and mute are mirrored into the bridge status (the parent
        # surfaces them to the web UI), applied by ``_apply_sink_volume`` and
        # echoed to Music Assistant.  Where they land depends on the player's
        # ``volume_controller``: ``sendspin`` scales this daemon's own stream
        # and never touches the sink (a local output may be shared); ``pa``
        # sets the sink through ``PulseVolumeController.set_state``.
        if payload.player is None:
            return
        cmd = payload.player
        if cmd.command == PlayerCommand.VOLUME and cmd.volume is not None:
            self._volume = max(0, min(100, cmd.volume))
            self._bridge_status["volume"] = self._volume
            self._notify()
            self._apply_sink_volume()
            self._report_player_state()
        elif cmd.command == PlayerCommand.MUTE and cmd.mute is not None:
            self._muted = cmd.mute
            self._bridge_status["muted"] = cmd.mute
            self._notify()
            self._apply_sink_volume()
            self._report_player_state()
        elif cmd.command == PlayerCommand.SET_OUTPUT_DELAY and cmd.output_delay_ms is not None:
            # aiosendspin's SendspinClient._handle_server_command auto-applied
            # the new delay via self.set_output_delay_ms(value) before this
            # listener fires, and its output-delay listener has already
            # resynced the player (_on_output_delay_change). Here we only
            # mirror the value into bridge_status so the parent persists it
            # and the web UI repaints.
            client = getattr(self, "_client", None)
            applied = (
                int(client.output_delay_ms)
                if client is not None and hasattr(client, "output_delay_ms")
                else max(0, min(5000, int(cmd.output_delay_ms)))
            )
            # Numbering the server's delays tells the parent this one came from
            # Music Assistant (to keep and save); a delay the bridge sets goes
            # through set_static_delay_ms and never moves the count.
            self._server_delay_seq = getattr(self, "_server_delay_seq", 0) + 1
            delay_updates = {"static_delay_ms": applied, "static_delay_server_seq": self._server_delay_seq}
            if isinstance(self._bridge_status, StatusStore):
                self._bridge_status.patch(delay_updates)
            else:
                self._bridge_status.update(delay_updates)
            # IMPORTANT: also update the daemon-level cache. _handle_server_connection
            # rebuilds the aiosendspin client via _create_client(self._static_delay_ms)
            # on every server reconnect; without this the next reconnect would
            # snap the delay back to the previous value (the one we were spawned
            # with), undoing the MA-pushed change until the whole subprocess
            # restarts. This mirrors what the local IPC path in daemon_process.py
            # already does for parent-driven set_static_delay_ms commands.
            self._static_delay_ms = float(applied)
            self._notify()

    def _on_output_delay_change(self, delay_ms: float) -> None:
        """Re-time the queued audio once the output delay stops changing.

        aiosendspin calls this for every change, whether Music Assistant sent
        set_output_delay or the bridge set it (set_static_delay_ms). Only later
        chunks get the new play time from ``compute_play_time``; the queued ones
        keep the old one, and GStreamer would absorb a step under 40 ms as
        jitter. The player applies the shift on its running output (or, for a
        very large one, rebuilds it); either way a burst of changes (a slider
        being dragged) is applied once, after a short quiet period.
        """
        self._target_delay_ms = delay_ms
        if self._delay_resync_handle is not None:
            self._delay_resync_handle.cancel()
            self._delay_resync_handle = None
        if self._player_delay_ms is None or delay_ms == self._player_delay_ms:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._apply_output_delay()
            return
        self._delay_resync_handle = loop.call_later(_DELAY_RESYNC_QUIET_S, self._apply_output_delay)

    def _queued_delay_correction_us(self) -> int:
        """What keeps a new chunk on the queued audio's timeline until the resync.

        ``compute_play_time`` already uses the new delay; the queue still has
        the old one. A larger delay plays earlier (play time = client time -
        delay), so the chunk moves later by the difference.
        """
        if self._player_delay_ms is None or self._target_delay_ms is None:
            return 0
        return round((self._target_delay_ms - self._player_delay_ms) * 1000)

    def _apply_output_delay(self) -> None:
        self._delay_resync_handle = None
        old_ms, new_ms = self._player_delay_ms, self._target_delay_ms
        if old_ms is None or new_ms is None or old_ms == new_ms:
            return
        shift_us = round((old_ms - new_ms) * 1000)
        self._player_delay_ms = new_ms
        if self._air_feed is not None:
            self._air_feed.set_output_delay(float(new_ms))
        if self._pending_play_time_us is not None:
            self._pending_play_time_us += shift_us
        if self._next_play_time_us is not None:
            self._next_play_time_us += shift_us
        if self._opening_chunks:
            # Waiting for an output being opened: move them like queued audio.
            self._opening_chunks = deque(
                ((t + shift_us, p, f) for t, p, f in self._opening_chunks), maxlen=_MAX_OPENING_CHUNKS
            )
        result: InPlaceShift | bool = False
        was_opening = self._opening is not None
        if self._player is not None:
            try:
                result = self._player.resync(shift_us)
            except Exception:
                logger.exception("Player resync after an output delay change failed")
                return
            reopen = getattr(self._player, "awaiting_reopen", None)
            if result is True and isinstance(reopen, PcmFormat):
                # The rebuild closed the output: open the new one off the loop
                # (its kept audio, and what arrives meanwhile, wait for it).
                self._output_state(reopen)
        if was_opening:
            outcome = "waiting audio shifted (output still opening)"
        elif isinstance(result, InPlaceShift):
            verb = "inserted" if result.shift_us > 0 else "dropped"
            outcome = f"shifted in place ({verb} {abs(result.shift_us) / 1000:g} ms)"
        elif not result:
            outcome = "applies to the next stream"
        elif isinstance(self._player, PlayoutPlayer):
            # The engine re-places the queued audio exactly; its output is never rebuilt.
            outcome = "player resynchronised (placed exactly, output kept)"
        else:
            outcome = "player resynchronised (output rebuilt)"
        logger.info("Output delay changed %g→%g ms; %s", old_ms, new_ms, outcome)

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

    def _on_metadata_update(self, payload: ServerStatePayload | None) -> None:
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

    def _on_controller_state(self, payload: ServerStatePayload | None) -> None:
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
        if self._scales_stream:
            self._apply_stream_volume()
            return
        controller = getattr(self, "_volume_controller", None)
        if controller is None:
            return
        task = asyncio.get_running_loop().create_task(controller.set_state(self._volume, muted=self._muted))

        def _done(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.warning("sink volume apply failed: %s", exc)

        task.add_done_callback(_done)

    def _report_player_state(self) -> None:
        """Echo volume and mute back in client/state.

        The spec requires a state update whenever a player field changes,
        including through server/command, and aiosendspin does not send one
        on its own. Without it Music Assistant refreshes from the stale value
        and snaps its slider back after Pause/Stop (#464).
        """
        client = self._client
        if client is None or not getattr(client, "connected", False):
            logger.debug("player state report skipped: client not connected")
            return
        logger.debug("player state report: available, volume %s, muted %s", self._volume, self._muted)
        task = asyncio.get_running_loop().create_task(
            client.send_player_state(available=True, volume=self._volume, muted=self._muted)
        )

        def _done(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                logger.warning("player state report failed: %s", exc)

        task.add_done_callback(_done)

    def _on_stream_start(self, message) -> None:
        player_cfg = getattr(getattr(message, "payload", message), "player", None)
        if player_cfg is None:
            player_cfg = getattr(message, "player", None)
        if player_cfg is None:
            self._on_stream_event("start")
            return
        codec = getattr(player_cfg, "codec", None)
        codec_name = str(getattr(codec, "value", codec or "PCM"))
        sample_rate = int(getattr(player_cfg, "sample_rate", 48000))
        bit_depth = int(getattr(player_cfg, "bit_depth", 16))
        channels = int(getattr(player_cfg, "channels", 2))
        header = getattr(player_cfg, "codec_header", None)
        began = _monotonic()
        self._cancel_preopen()
        self._reset_decoder(codec_name, sample_rate, bit_depth, channels, header)
        self._handle_format_change(codec_name, sample_rate, bit_depth, channels)
        if self._player is not None:
            fmt = PcmFormat(sample_rate=sample_rate, channels=channels, bit_depth=bit_depth)
            state = self._output_state(fmt, from_start=True)
            if state == "opening":
                # An output already being opened serves this stream too.
                self._opening_discard = False
            elif state == "ready":
                self._first_arrival_lead_us = None
                generation = self._player.stream_generation
                self._player.start(fmt)
                # A start that keeps the running output has no new start to describe.
                if self._player.stream_generation != generation:
                    self._start_cost = {"output_open_ms": (_monotonic() - began) * 1000.0}
                    self._schedule_start_timing_line()
        # What this start held the loop for (chunks sent meanwhile wait unread).
        self._loop_block_ms = (_monotonic() - began) * 1000.0
        self._on_stream_event("start")

    def _output_state(self, fmt: PcmFormat, *, from_start: bool = False, from_preopen: bool = False) -> str:
        """Where audio for ``fmt`` goes now: ``ready`` (the player, opening its
        output on the loop if it must: no loop to come back to), ``opening``
        (wait for the output being opened in a worker thread, starting that
        open if needed) or ``unavailable`` (a failed open is backing off)."""
        player = self._player
        if self._opening is not None:
            return "opening"
        if player is None or player.needs_output() is not True:
            return "ready"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return "ready"
        if _monotonic() < self._open_retry_at:
            return "unavailable"
        self._opening_chunks.clear()
        self._opening_discard = False
        self._opening_from_start = from_start
        self._opening_from_preopen = from_preopen
        self._opening_set_format = not from_preopen
        self._opening_began = _monotonic()
        self._first_arrival_lead_us = None
        opening = asyncio.ensure_future(loop.run_in_executor(None, player.prepare_output, fmt))
        self._opening = opening
        self._opening_watchdog = loop.call_later(_OPEN_TIMEOUT_S, self._abandon_open, opening)
        opening.add_done_callback(lambda done: self._on_output_opened(done, player, fmt))
        return "opening"

    # -- pre-open (#555) ----------------------------------------------------------------

    def _schedule_preopen(self) -> None:
        """Open the output soon after the daemon starts, before any stream asks for it."""
        if not self._playout_active or self._player is None or self._sink_name is None:
            return
        if getattr(self._args, "idle_mode", "default") in ("power_save", "auto_disconnect"):
            return
        if getattr(self, "_preopen_handle", None) is not None or self._preopen_expiry is not None:
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._preopen_handle = loop.call_later(_PREOPEN_DELAY_S, self._preopen_output)

    def _preopen_output(self) -> None:
        """Start the open without a stream; the first start(format) applies the real format."""
        self._preopen_handle = None
        player = self._player
        if player is None or not self._playout_active or self._player is not player:
            return
        status = self._bridge_status
        if status.get("bt_standby") or status.get("playing") or self._opening is not None:
            return
        if not isinstance(player, PlayoutPlayer) or player.needs_output() is not True:
            return
        # The format is not applied for a pre-open (prepare_output ignores it): any placeholder works.
        placeholder = PcmFormat(sample_rate=48000, channels=2, bit_depth=16)
        if self._output_state(placeholder, from_preopen=True) != "opening":
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            self._preopen_expiry = loop.call_later(_PREOPEN_HOLD_S, self._expire_preopen)

    def _expire_preopen(self) -> None:
        """No stream came while the output was held: close it so the speaker can idle out."""
        self._preopen_expiry = None
        player = self._player
        if player is None or not self._playout_active or not isinstance(player, PlayoutPlayer):
            return
        if self._bridge_status.get("playing") or player.needs_output() is True:
            return
        logger.info("playout output closed after the pre-open hold: no stream arrived")
        player.stop("pre-open hold expired")

    def _cancel_preopen(self) -> None:
        """A stream started (or the player changed): the hold no longer applies."""
        if self._preopen_handle is not None:
            self._preopen_handle.cancel()
            self._preopen_handle = None
        if self._preopen_expiry is not None:
            self._preopen_expiry.cancel()
            self._preopen_expiry = None

    def _forget_open(self) -> None:
        """Stop waiting for the open in flight; its output is closed when it finishes."""
        if self._opening_watchdog is not None:
            self._opening_watchdog.cancel()
            self._opening_watchdog = None
        self._opening = None
        self._opening_chunks.clear()

    def _abandon_open(self, opening: asyncio.Future[Any]) -> None:
        if self._opening is not opening:
            return
        self._opening_watchdog = None
        if not self._warned_hung_open:
            self._warned_hung_open = True
            logger.warning(
                "The audio output did not open within %.0f s; giving up on it, the next audio opens it anew",
                _OPEN_TIMEOUT_S,
            )
        if self._player is not None and hasattr(self._player, "forget_reopen"):
            self._player.forget_reopen()
        self._forget_open()

    def _on_output_opened(
        self, done: asyncio.Future[Any], player: StreamPlayer | PlayoutPlayer, fmt: PcmFormat
    ) -> None:
        """Adopt the output opened off the loop and hand it the chunks that waited."""
        current = self._opening is done
        chunks: list[tuple[int, bytes, PcmFormat]] = []
        if current:
            chunks = list(self._opening_chunks)
            self._forget_open()
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            if current:
                self._open_failures += 1
                backoff = min(_OPEN_RETRY_MIN_S * 2 ** (self._open_failures - 1), _OPEN_RETRY_MAX_S)
                self._open_retry_at = _monotonic() + backoff
                logger.warning("Opening the audio output failed: %s; retrying in %.1f s", exc, backoff)
                # Audio a rebuild kept for this output would be stale by the next one.
                if hasattr(player, "forget_reopen"):
                    player.forget_reopen()
            return
        output = done.result()
        if not current or player is not self._player or self._opening_discard:
            player.discard_output(output)
            return
        self._open_failures = 0
        self._open_retry_at = 0.0
        opened_ms = (_monotonic() - self._opening_began) * 1000.0
        preopened = self._opening_from_preopen
        if isinstance(player, PlayoutPlayer):
            player.adopt_output(output, fmt, set_format=self._opening_set_format)
        else:
            player.adopt_output(output, fmt)
        if preopened:
            # Not this start's cost: the stream will find an open output (no open, no drop).
            logger.info("playout output pre-opened in %.0f ms (no stream yet)", opened_ms)
        else:
            self._start_cost = {"output_open_ms": opened_ms}
        if self._opening_from_start and self._start_cost is not None:
            self._start_cost["loop_block_ms"] = self._loop_block_ms
        self._schedule_start_timing_line()
        try:
            for play_time_us, payload, chunk_fmt in chunks:
                player.start(chunk_fmt)
                player.submit(play_time_us, payload)
        except Exception:
            logger.exception("Handing the waiting audio to the new output failed")

    def _prune_waiting(self, now_us: int) -> None:
        """Drop waiting chunks that end before now: the player would drop them anyway."""
        chunks = self._opening_chunks
        while chunks:
            play_time_us, payload, fmt = chunks[0]
            frame_bytes = fmt.frame_bytes()
            frames = len(payload) // frame_bytes if frame_bytes else 0
            if play_time_us + frames * 1_000_000 // fmt.sample_rate > now_us:
                break
            chunks.popleft()

    async def _warm_decoder_later(self) -> None:
        await asyncio.sleep(_DECODER_WARMUP_DELAY_S)
        await asyncio.to_thread(_warm_decoder_library)

    def _reset_decoder(
        self,
        codec_name: str,
        sample_rate: int,
        bit_depth: int,
        channels: int,
        codec_header: object,
    ) -> None:
        from aiosendspin.audio.codecs import create_decoder

        header = codec_header
        if isinstance(header, str):
            import base64

            try:
                header = base64.b64decode(header)
            except Exception:
                header = None
        codec = str(codec_name or "pcm").split(".")[-1].lower()
        header_bytes = bytes(header) if isinstance(header, (bytes, bytearray)) else None
        signature = (codec, sample_rate, bit_depth, channels, header_bytes)
        if signature == self._decoder_format:
            return
        self._decoder_format = signature
        self._decoder = None
        if codec == "pcm":
            return
        try:
            self._decoder = create_decoder(
                codec,
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                channels=channels,
                codec_header=header_bytes,
            )
        except Exception as exc:
            logger.error("Cannot decode %s: %s", codec, exc)
            self._decoder = None

    def _on_audio_chunk(
        self, server_timestamp_us: int, payload: bytes, audio_format, send_ahead_us: int | None = None
    ) -> None:
        if self._player is None:
            return
        pcm = getattr(audio_format, "pcm_format", audio_format)
        fmt = PcmFormat(
            sample_rate=int(getattr(pcm, "sample_rate", 48000)),
            channels=int(getattr(pcm, "channels", 2)),
            bit_depth=int(getattr(pcm, "bit_depth", 16)),
        )
        codec = getattr(audio_format, "codec", None)
        codec_name = str(getattr(codec, "value", codec or "pcm")).lower()
        header = getattr(audio_format, "codec_header", None)
        self._reset_decoder(codec_name, fmt.sample_rate, fmt.bit_depth, fmt.channels, header)
        client = self._client
        play_time_us = (
            client.compute_play_time(server_timestamp_us)
            if client is not None and hasattr(client, "compute_play_time")
            else server_timestamp_us
        )
        play_time_us += self._queued_delay_correction_us()
        if codec_name != "pcm":
            if self._decoder is None:
                return
            if self._pending_play_time_us is None:
                self._pending_play_time_us = play_time_us
            try:
                payload = self._decoder.decode(payload)
            except Exception:
                logger.exception("audio decode failed")
                return
            if not payload:
                return
            play_time_us = self._pending_play_time_us
            self._pending_play_time_us = None
        else:
            self._pending_play_time_us = None
        state = self._output_state(fmt)
        if state == "opening":
            now_us = self._player.now_us()
            if self._first_arrival_lead_us is None:
                self._first_arrival_lead_us = play_time_us - now_us
            if self._args.compensate_output_latency:
                self._prune_waiting(now_us)
            self._opening_chunks.append((play_time_us, payload, fmt))
        elif state == "unavailable":
            pass  # a failed open is backing off: nothing can play this
        else:
            self._player.start(fmt)
            self._player.submit(play_time_us, payload)
        frame_bytes = fmt.frame_bytes()
        duration_us = (len(payload) // frame_bytes) * 1_000_000 // fmt.sample_rate if frame_bytes else 0
        self._next_play_time_us = play_time_us + duration_us

    def _on_stream_clear(self, _roles: object = None) -> None:
        self._decoder = None
        self._decoder_format = None
        self._next_play_time_us = None
        self._pending_play_time_us = None
        self._opening_chunks.clear()
        if self._player is not None:
            self._player.clear()

    def _on_stream_end(self, _roles: object = None) -> None:
        # The player role defines no completion behaviour, so stream/end means
        # stop output and clear buffers (Sendspin spec). Playing out what was
        # queued made Pause/Stop lag by the send-ahead, 5-30 s (#464). Tear the
        # pipeline down rather than flush it: flush events deadlocked the
        # daemon on some A2DP sinks, and the next stream/start rebuilds it.
        self._decoder = None
        self._decoder_format = None
        self._next_play_time_us = None
        self._pending_play_time_us = None
        if self._opening is not None:
            self._opening_discard = True
            self._opening_chunks.clear()
        if self._player is not None:
            self._player.stop()
        self._on_stream_event("stop")

    def metrics(self) -> dict[str, object]:
        if self._player is None:
            return {}
        return self._player.metrics()

    # ── Timing diagnostics ───────────────────────────────────────────────────

    def _schedule_start_timing_line(self) -> None:
        player = self._player
        if player is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._start_timing_task is not None:
            self._start_timing_task.cancel()
        generation = player.stream_generation
        self._start_timing_task = loop.create_task(self._log_stream_start_timing(generation, _START_TIMING_DELAY_S))

    async def _log_stream_start_timing(self, generation: int, delay_s: float = 0.0) -> None:
        """One INFO line per stream start with every timing term the bridge knows."""
        import os

        from sendspin_bridge.services.audio import pulse
        from sendspin_bridge.services.audio.player.start_diag import format_stream_start_line, time_sync_summary

        if delay_s:
            await asyncio.sleep(delay_s)
        player = self._player
        if player is None or player.stream_generation != generation:
            return
        report = player.start_report()
        if report is None:
            return
        if self._first_arrival_lead_us is not None:
            # The first chunk's lead when it arrived, not when the opened output took it.
            report = {**report, "first_lead_ms": round(self._first_arrival_lead_us / 1000.0, 3)}
        try:
            pa_latency = await pulse.apid_sink_input_latency(os.getpid())
        except Exception as exc:
            logger.debug("PA sink-input latency unavailable: %s", exc)
            pa_latency = None
        logger.info(
            "%s",
            format_stream_start_line(
                report,
                pa_latency=pa_latency,
                time_sync=time_sync_summary(self._client),
                output_delay_ms=self._player_delay_ms,
                start_cost=self._start_cost,
            ),
        )
        self._check_min_latency(report)
        if pa_latency is not None and self._args.compensate_output_latency:
            self._note_sink_lookahead(float(pa_latency["sink_ms"]))
        self._redeclare_lead()

    def _check_min_latency(self, report: dict[str, Any]) -> None:
        compensated = report.get("compensated_latency_ms")
        min_latency = report.get("min_latency_ms")
        if self._warned_min_latency or not compensated or min_latency is None or min_latency <= compensated:
            return
        self._warned_min_latency = True
        logger.warning(
            "The sink reports a min latency of %.1f ms, more than the %.1f ms compensated "
            "(PULSE_LATENCY_MSEC); audio may play up to %.1f ms late. Raise PULSE_LATENCY_MSEC.",
            min_latency,
            compensated,
            min_latency - compensated,
        )

    def _redeclare_lead(self) -> None:
        """Send Music Assistant a new lead when what it must cover has changed."""
        client = self._client
        if client is None or not hasattr(client, "set_required_lead_time_ms"):
            return
        lead = self.declared_lead_time_ms(self._required_lead_time_ms)
        if lead == float(getattr(client, "required_lead_time_ms", lead)):
            return
        client.set_required_lead_time_ms(lead)
        self._bridge_status["declared_lead_time_ms"] = round(lead)
        self._notify()
        self._report_player_state()
        logger.info(
            "Declared lead time raised to %.0f ms to cover the sink's %.1f ms lookahead",
            lead,
            self._sink_lookahead_ms or 0.0,
        )

    async def _clock_diag_loop(self) -> None:
        """While playing: clock rates, DEBUG every 10 s and INFO once a minute."""
        import time

        from sendspin_bridge.services.audio.player.clock_diag import ClockDiagnostics

        diag = ClockDiagnostics(audio_clock_enabled=self._args.playout_diag)
        raw_id = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)
        while True:
            await asyncio.sleep(_CLOCK_DIAG_INTERVAL_S)
            player = self._player
            if player is None or not self._bridge_status.get("audio_streaming"):
                continue
            try:
                raw_a = time.clock_gettime_ns(raw_id)
                mono = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
                audio_ns = player.audio_clock_ns()
                raw_b = time.clock_gettime_ns(raw_id)
                raw_us = (raw_a + raw_b) // 2000
                diag.sample(
                    raw_us=raw_us,
                    mono_us=mono // 1000,
                    audio_ns=audio_ns,
                    generation=player.stream_generation,
                )
                due = diag.due(raw_us=raw_us)
            except Exception as exc:
                logger.debug("Clock diag sample failed: %s", exc)
                continue
            if due is not None:
                level, line = due
                logger.log(level, "%s", line)


def _declared_formats():
    from aiosendspin.models.player import SupportedAudioFormat
    from aiosendspin.models.types import AudioCodec

    # The server prefers the first declared format, so speakers stay on 16-bit
    # unless their Music Assistant player is set to a hi-res one (#543).
    formats = []
    for bit_depth, sample_rates in ((16, (48000, 44100)), (24, (48000, 44100, 96000))):
        for sample_rate in sample_rates:
            for codec in (AudioCodec.FLAC, AudioCodec.PCM):
                formats.append(
                    SupportedAudioFormat(codec=codec, channels=2, sample_rate=sample_rate, bit_depth=bit_depth)
                )
    return formats
