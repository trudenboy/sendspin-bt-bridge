"""Capture backends: the phone's Bluetooth audio as PCM, whatever the host audio server is.

The source only needs three facts from a backend: whether a phone is streaming, a stable
name for its capture, and a stream of whole 10 ms s16 frames.  PipeWire exposes the phone
as ``bluez_input.*``, PulseAudio as a ``bluez_source.*`` recording source.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "CHUNK_MS",
    "CaptureBackend",
    "CaptureEndpoint",
    "CaptureStream",
    "CommandResult",
    "ProcessCapture",
    "chunk_bytes",
    "frame_bytes",
    "mac_from_node",
    "run_command",
    "terminate_process",
]

logger = logging.getLogger(__name__)

CHUNK_MS = 10
SAMPLE_RATE = 48000
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # s16le
RUN_TIMEOUT_S = 15.0
STOP_TIMEOUT_S = 3.0


@dataclass(frozen=True, slots=True)
class CaptureEndpoint:
    """A phone's capture as one audio server names it."""

    node: str
    backend: str
    phone_mac: str | None = None
    running: bool = False


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of a short helper command (``pactl``, ``wpctl``)."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class CaptureStream(Protocol):
    """The PCM of one capture endpoint, in whole 10 ms frames."""

    @property
    def running(self) -> bool: ...

    async def start(self) -> None: ...

    def chunks(self) -> AsyncIterator[bytes]: ...

    async def stop(self) -> None: ...


class CaptureBackend(Protocol):
    """One audio-server family and its capture endpoints."""

    name: str

    async def find(self) -> CaptureEndpoint | None: ...

    async def open(self, endpoint: CaptureEndpoint) -> CaptureStream: ...


def frame_bytes(channels: int = CHANNELS, bytes_per_sample: int = BYTES_PER_SAMPLE) -> int:
    """Bytes of one s16 frame (one sample per channel)."""
    return bytes_per_sample * channels


def chunk_bytes(sample_rate: int = SAMPLE_RATE, channels: int = CHANNELS, chunk_ms: int = CHUNK_MS) -> int:
    """Bytes pushed at a time: ``chunk_ms`` of s16 audio, rounded to whole frames."""
    return sample_rate * chunk_ms // 1000 * frame_bytes(channels)


_NODE_RE = re.compile(r"^(?:bluez_input|bluez_source)\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})(?:\..*)?$")


def mac_from_node(name: str) -> str | None:
    """The phone's MAC from a ``bluez_input``/``bluez_source`` name, if it is one."""
    match = _NODE_RE.match(str(name or "").strip())
    if match is None:
        return None
    return match.group(1).replace("_", ":").upper()


def _run_command_sync(argv: list[str], timeout: float) -> CommandResult:
    """One short helper command in a worker thread; its pipes are closed by ``subprocess``."""
    import subprocess

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (FileNotFoundError, PermissionError) as exc:
        return CommandResult(returncode=-1, stderr=str(exc))
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=-1, stderr=f"{argv[0]} timed out after {timeout:.0f}s")
    return CommandResult(returncode=int(proc.returncode), stdout=proc.stdout or "", stderr=proc.stderr or "")


async def run_command(argv: Sequence[str], *, timeout: float = RUN_TIMEOUT_S) -> CommandResult:
    """Run a short helper command; never raise, report the failure in the result.

    Deliberately a thread, not ``asyncio.create_subprocess_exec``: the source polls
    ``pactl`` every couple of seconds, and on Python 3.13 each asyncio subprocess transport
    left two sockets and an epoll object behind on the add-on, exhausting the 1024-fd limit
    after a few minutes of streaming.  ``subprocess.run`` closes its pipes deterministically.
    """
    return await asyncio.to_thread(_run_command_sync, list(argv), timeout)


async def terminate_process(proc: Any, *, timeout: float = STOP_TIMEOUT_S) -> None:
    """Stop a long-running capture process: TERM, wait, then KILL if it ignores TERM."""
    if proc is None or getattr(proc, "returncode", None) is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except TimeoutError:
        logger.warning("A capture process ignored TERM for %.0f s; killing it", timeout)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(Exception):
        await proc.wait()


Spawn = Callable[..., Awaitable[Any]]


class ProcessCapture:
    """A capture backed by a subprocess writing raw s16 PCM to stdout."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        chunk_ms: int = CHUNK_MS,
        spawn: Spawn | None = None,
    ) -> None:
        self._argv = list(argv)
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_bytes = chunk_bytes(sample_rate, channels, chunk_ms)
        self._spawn = spawn or asyncio.create_subprocess_exec
        self._proc: Any = None

    @property
    def running(self) -> bool:
        return self._proc is not None and getattr(self._proc, "returncode", None) is None

    async def start(self) -> None:
        self._proc = await self._spawn(
            *self._argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("Capture started: %s", " ".join(self._argv[:2]))

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield whole chunks until the capture process ends."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        buffer = bytearray()
        while True:
            data = await proc.stdout.read(self._chunk_bytes - len(buffer))
            if not data:
                return
            buffer += data
            while len(buffer) >= self._chunk_bytes:
                yield bytes(buffer[: self._chunk_bytes])
                del buffer[: self._chunk_bytes]

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            await terminate_process(proc)
