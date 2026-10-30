"""A Sendspin source fed by the phone that streams into the bridge over Bluetooth (#563).

The bridge is normally a Sendspin *client* (a player).  Here it is the other side: it accepts
A2DP audio from a phone (`bluez_input.*` in PipeWire), wraps it into a Sendspin server and lets
Sendspin players (Music Assistant, the web player) play it as a live stream.

The capture is PipeWire-native on purpose: PulseAudio's compatibility layer never exports the
phone's capture node as a source (`pactl list sources` shows nothing for it), while `pw-record`
reads it by node name.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

logger = logging.getLogger(__name__)

#: Node names PipeWire gives the A2DP capture of a paired phone, e.g. ``bluez_input.34_C7_E9_25_A3_A3.2``.
BLUEZ_INPUT_RE = re.compile(r"bluez_input\.[0-9A-Fa-f_]{17}\.\d+")
#: 10 ms of 48 kHz stereo s16: the unit pushed into the Sendspin stream.
CHUNK_MS = 10
#: How often the node list is checked while no phone streams.
NODE_POLL_S = 2.0


#: What the bridge status reports about the source (read by the API from another thread).
_STATE: dict[str, Any] = {
    "enabled": False,
    "port": None,
    "phone": None,
    "players": 0,
    "streaming": False,
    "error": None,
}


def source_status() -> dict[str, Any]:
    """The source's state for the bridge status: enabled, port, phone node, players, streaming."""
    from sendspin_bridge.services.source.pairing import pairing_status

    state = dict(_STATE)
    state.update(pairing_status())
    return state


def _set_state(**changes: Any) -> None:
    _STATE.update(changes)


@dataclass(frozen=True)
class SourceConfig:
    """Where the source listens and what it accepts (environment-driven)."""

    host: str = "0.0.0.0"
    port: int = 8940
    server_name: str = "Sendspin Bluetooth Source"
    sample_rate: int = 48000
    channels: int = 2
    node: str | None = None  # fixed capture node; None = follow whatever phone connects

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SourceConfig:
        env = dict(os.environ if env is None else env)
        return cls(
            host=env.get("SENDSPIN_SOURCE_HOST", "0.0.0.0"),
            port=int(env.get("SENDSPIN_SOURCE_PORT", "8940")),
            server_name=env.get("SENDSPIN_SOURCE_NAME", "Sendspin Bluetooth Source"),
            sample_rate=int(env.get("SENDSPIN_SOURCE_RATE", "48000")),
            channels=int(env.get("SENDSPIN_SOURCE_CHANNELS", "2")),
            node=env.get("SENDSPIN_SOURCE_NODE") or None,
        )


def find_bluez_input(wpctl_status: str) -> str | None:
    """The node to capture from, as `wpctl status` prints it (None: no phone streaming)."""
    match = BLUEZ_INPUT_RE.search(wpctl_status)
    return match.group(0) if match else None


async def find_bluez_input_on_host() -> str | None:
    """Ask PipeWire for the phone's capture node, if one is streaming."""
    proc = await asyncio.create_subprocess_exec(
        "wpctl", "status", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await proc.communicate()
    return find_bluez_input(out.decode(errors="replace")) if proc.returncode == 0 else None


def frame_bytes(config: SourceConfig) -> int:
    """Bytes of one s16 frame (all channels)."""
    return 2 * config.channels


def chunk_bytes(config: SourceConfig) -> int:
    """Bytes pushed at a time: 10 ms, rounded to whole frames."""
    return config.sample_rate * CHUNK_MS // 1000 * frame_bytes(config)


class PwCapture:
    """Reads a PipeWire capture node as raw s16 PCM on stdout."""

    def __init__(self, node: str, config: SourceConfig) -> None:
        self._node = node
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            "pw-record",
            "--target",
            self._node,
            "--rate",
            str(self._config.sample_rate),
            "--channels",
            str(self._config.channels),
            "--format",
            "s16",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info("Bluetooth source: capturing %s", self._node)

    async def chunks(self) -> AsyncIterator[bytes]:
        """Yield whole 10 ms chunks until the capture ends."""
        assert self._proc is not None and self._proc.stdout is not None
        size = chunk_bytes(self._config)
        buf = bytearray()
        while True:
            data = await self._proc.stdout.read(size - len(buf))
            if not data:
                return
            buf += data
            while len(buf) >= size:
                yield bytes(buf[:size])
                del buf[:size]

    async def stop(self) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._proc.wait(), timeout=3.0)
        self._proc = None


class BtSendspinSource:
    """The Sendspin server: a phone's Bluetooth audio becomes a playable stream."""

    def __init__(self, config: SourceConfig, *, identity_path: Path, pairing_path: Path) -> None:
        self._config = config
        self._identity_path = identity_path
        self._pairing_path = pairing_path
        self._server: Any = None
        self._group: Any = None
        self._stream: Any = None
        self._capture: PwCapture | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        from aiosendspin.noise.keys import Identity
        from aiosendspin.noise.trust_store import FileServerPairingStore
        from aiosendspin.server import SendspinServer

        identity = self._load_or_create_identity(Identity)
        self._pairing_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = SendspinServer(
            asyncio.get_running_loop(),
            identity,
            self._config.server_name,
            pairing_store=FileServerPairingStore(self._pairing_path),
            allow_noncompliant_clients=True,
        )
        # Groups are made by the server around connected players: the stream is created once a
        # player is there (a group cannot exist without one).
        try:
            await self._server.start_server(port=self._config.port, host=self._config.host)
        except OSError as exc:
            # A player's listen port or another service may hold it: say so instead of a traceback.
            await self._server.close()
            self._server = None
            _set_state(enabled=False, error="port_in_use")
            raise RuntimeError(
                f"the Sendspin source port {self._config.port} is already in use ({exc.strerror or exc}); "
                "set SENDSPIN_SOURCE_PORT to a free port"
            ) from exc
        _set_state(enabled=True, port=self._config.port, phone=None, players=0, streaming=False, error=None)
        logger.info(
            "Sendspin source listening on %s:%d (players connect to ws://<bridge>:%d/sendspin)",
            self._config.host,
            self._config.port,
            self._config.port,
        )
        self._task = asyncio.create_task(self._watch(), name="sendspin-source-watch")

    def _load_or_create_identity(self, identity_cls: type[Any]) -> Any:
        self._identity_path.parent.mkdir(parents=True, exist_ok=True)
        if self._identity_path.exists():
            return identity_cls.from_private_bytes(self._identity_path.read_bytes())
        identity = identity_cls.generate()
        self._identity_path.write_bytes(identity.private_bytes)
        self._identity_path.chmod(0o600)
        return identity

    def _open_stream(self) -> bool:
        """Create the push stream on the first connected player, then keep the group whole."""
        assert self._server is not None
        players = self._server.connected_clients
        if self._stream is not None:
            # The server makes a one-client group per connection: a player that comes later
            # must join ours, or it hears nothing.
            joined = False
            for player in players:
                if player not in self._group.clients:
                    self._group.add_client(player)
                    joined = True
            if joined:
                _set_state(players=len(self._group.clients))
                logger.info("Bluetooth source: %d player(s) on the stream", len(self._group.clients))
            return True
        if not players:
            return False
        self._group = players[0].group
        self._stream = self._group.start_stream()
        self._stream.set_live_source(True)
        _set_state(players=len(players), streaming=True)
        logger.info("Bluetooth source: a player connected, streaming to it")
        return True

    async def _watch(self) -> None:
        """Follow the phone: capture while it streams, wait while it does not."""
        from aiosendspin.server import AudioFormat

        audio_format = AudioFormat(sample_rate=self._config.sample_rate, bit_depth=16, channels=self._config.channels)
        while True:
            try:
                if not self._open_stream():
                    await asyncio.sleep(1.0)
                    continue
                node = self._config.node or await find_bluez_input_on_host()
                if node is None:
                    await asyncio.sleep(NODE_POLL_S)
                    continue
                await self._pump(node, audio_format)
                # The stream ended (the player left): wait for the next one.
                self._stream = None
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Bluetooth source: capture failed, retrying", exc_info=True)
                self._stream = None
                await asyncio.sleep(NODE_POLL_S)

    async def _pump(self, node: str, audio_format: Any) -> None:
        from aiosendspin.server.push_stream import StreamStoppedError

        assert self._stream is not None
        self._capture = PwCapture(node, self._config)
        await self._capture.start()
        _set_state(phone=node)
        sent = 0
        try:
            async for chunk in self._capture.chunks():
                self._stream.prepare_audio(chunk, audio_format)
                try:
                    await self._stream.commit_audio()
                except StreamStoppedError:
                    logger.info("Bluetooth source: the stream stopped (player gone)")
                    return
                sent += 1
                if sent % 500 == 0:  # ~5 s of 10 ms chunks
                    logger.info("Bluetooth source: %.0f s pushed to the stream", sent * CHUNK_MS / 1000)
        finally:
            await self._capture.stop()
            self._capture = None
            players = len(self._server.connected_clients) if self._server is not None else 0
            _set_state(phone=None, streaming=False, players=players)
            logger.info("Bluetooth source: capture ended")

    async def stop(self) -> None:
        _set_state(enabled=False, phone=None, players=0, streaming=False)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._capture is not None:
            await self._capture.stop()
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
            self._stream = None
        if self._server is not None:
            await self._server.close()
            self._server = None
