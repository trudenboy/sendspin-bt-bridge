"""The calibration metronome: a phase-aligned click train on one BT sink.

Two speakers can only be compared by ear if they click on the same beat, so
the train is aligned to a process-wide epoch shifted by each device's
declared delay.  Everything else here is subprocess plumbing — spawn a
player, keep its pipe fed, and take it down without leaving a paplay behind.

None of that was ever shared with the rest of the client: the metronome
needed a sink name, the declared delay, and somewhere to publish a flag.  It
lived in `SendspinClient` anyway, which meant its process and task lifecycle
could only be reached by standing up a client with a Bluetooth manager.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import TYPE_CHECKING

from sendspin_bridge.services.audio.latency_calibration import (
    build_metronome_beat_pcm,
    build_subsonic_carrier_pcm,
    calculate_metronome_lead_frames,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = ["CalibrationMetronome"]


_CALIBRATION_METRONOME_SAMPLE_RATE = 48000
_CALIBRATION_METRONOME_BPM = 120
_CALIBRATION_METRONOME_CLICK_MS = 40
_CALIBRATION_METRONOME_GATE_PREROLL_MS = 80
_CALIBRATION_METRONOME_EPOCH = time.monotonic()


def _calibration_metronome_paplay_args(sink_name: str) -> tuple[str, ...]:
    """Return paplay arguments with a small fixed scheduling quantum."""
    return (
        "paplay",
        f"--device={sink_name}",
        "--raw",
        "--format=s16le",
        f"--rate={_CALIBRATION_METRONOME_SAMPLE_RATE}",
        "--channels=2",
        "--latency-msec=20",
        "--process-time-msec=5",
    )


def _calibration_metronome_player_args(sink_name: str) -> tuple[str, ...]:
    """Select the native low-latency player for the active audio backend."""
    pw_play = shutil.which("pw-play")
    if sink_name.startswith("bluez_output.") and pw_play:
        return (
            pw_play,
            f"--target={sink_name}",
            "--latency=20ms",
            f"--rate={_CALIBRATION_METRONOME_SAMPLE_RATE}",
            "--channels=2",
            "--channel-map=stereo",
            "--format=s16",
            "-",
        )
    return _calibration_metronome_paplay_args(sink_name)


async def _default_spawn(args: tuple[str, ...]):
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


class CalibrationMetronome:
    """One device's click train, owning its process and its feed task."""

    def __init__(
        self,
        *,
        sink_name: str,
        player_name: str,
        static_delay_ms: Callable[[], float],
        on_active_change: Callable[[bool], None],
        spawn: Callable[[tuple[str, ...]], Awaitable] | None = None,
        terminate_grace_s: float = 2.0,
    ):
        self.sink_name = sink_name
        self.player_name = player_name
        self._static_delay_ms = static_delay_ms
        self._on_active_change = on_active_change
        self._spawn = spawn or _default_spawn
        self._terminate_grace_s = terminate_grace_s
        self._proc = None
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> bool:
        """Start the click train. True when it is running (or already was)."""
        if self.active:
            return True
        if not self.sink_name:
            return False
        try:
            proc = await self._spawn(_calibration_metronome_player_args(self.sink_name))
        except Exception as exc:
            logger.warning("[%s] Could not start calibration metronome: %s", self.player_name, exc)
            return False

        started_at = time.monotonic()
        lead_frames = calculate_metronome_lead_frames(
            started_at,
            sample_rate=_CALIBRATION_METRONOME_SAMPLE_RATE,
            bpm=_CALIBRATION_METRONOME_BPM,
            epoch_seconds=(_CALIBRATION_METRONOME_EPOCH - float(self._static_delay_ms() or 0.0) / 1000.0),
        )
        # Keep the A2DP stream non-silent before and between clicks.  Some
        # speakers (notably Lenco LS-500) close their input gate during the
        # otherwise-silent gap and only reproduce an occasional probe.
        lead_pcm = build_subsonic_carrier_pcm(
            lead_frames,
            sample_rate=_CALIBRATION_METRONOME_SAMPLE_RATE,
        )
        beat_pcm = build_metronome_beat_pcm(
            sample_rate=_CALIBRATION_METRONOME_SAMPLE_RATE,
            bpm=_CALIBRATION_METRONOME_BPM,
            keepalive_amplitude=100,
            click_duration_ms=_CALIBRATION_METRONOME_CLICK_MS,
            gate_preroll_ms=_CALIBRATION_METRONOME_GATE_PREROLL_MS,
        )
        self._proc = proc
        self._task = asyncio.ensure_future(self._feed(proc, lead_pcm, beat_pcm))
        self._on_active_change(True)
        logger.info(
            "[%s] Calibration metronome started on %s (shared phase, %.0f ms lead)",
            self.player_name,
            self.sink_name,
            lead_frames * 1000.0 / _CALIBRATION_METRONOME_SAMPLE_RATE,
        )
        return True

    async def stop(self) -> None:
        """Stop the click train immediately, leaving no player behind."""
        proc, self._proc = self._proc, None
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception as exc:
                    logger.debug("[%s] Metronome stdin close failed: %s", self.player_name, exc)
            await self._terminate(proc)
            logger.info("[%s] Calibration metronome stopped", self.player_name)
        self._on_active_change(False)

    async def _feed(self, proc, lead_pcm: bytes, beat_pcm: bytes) -> None:
        """Feed one phase-aligned click period repeatedly until cancelled."""
        try:
            if proc.stdin is None:
                return
            proc.stdin.write(lead_pcm)
            await proc.stdin.drain()
            while proc.returncode is None:
                proc.stdin.write(beat_pcm)
                await proc.stdin.drain()
        except asyncio.CancelledError:
            raise
        except (BrokenPipeError, ConnectionResetError) as exc:
            logger.debug("[%s] Calibration metronome pipe closed: %s", self.player_name, exc)
        except Exception as exc:
            logger.warning("[%s] Calibration metronome failed: %s", self.player_name, exc)
        finally:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception as exc:
                    logger.debug("[%s] Metronome stdin close failed: %s", self.player_name, exc)
            await self._terminate(proc)
            # Only report inactive when this run is still the current one; a
            # restart may already have replaced it.
            if self._proc is proc:
                self._proc = None
                self._task = None
                self._on_active_change(False)

    async def _terminate(self, proc) -> None:
        """Terminate the player, escalating to SIGKILL if it does not exit."""
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._terminate_grace_s)
            return
        except TimeoutError:
            logger.warning("[%s] Calibration metronome ignored terminate; killing player", self.player_name)
        except Exception:
            return
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._terminate_grace_s)
        except Exception:
            logger.warning("[%s] Calibration metronome player could not be reaped", self.player_name)
