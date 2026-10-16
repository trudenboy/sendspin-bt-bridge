"""Hosts the click track (and manual tuning's listening sounds) for Music Assistant under per-session,
unguessable URLs."""

from __future__ import annotations

import errno
import functools
import hashlib
import hmac
import logging
import os
import secrets
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from sendspin_bridge.config.network import get_local_ip, resolve_web_port
from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_api_credentials
from sendspin_sync import click_track as ct
from sendspin_sync import listening_tracks as lt
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_sync.ports import Track

logger = logging.getLogger(__name__)


def _cache_dir() -> Path:
    """A disk-backed temp dir: ``/var/tmp`` where it exists (``/tmp`` is often RAM), else the system one."""
    var_tmp = Path("/var/tmp")
    base = var_tmp if var_tmp.is_dir() and os.access(var_tmp, os.W_OK) else Path(tempfile.gettempdir())
    return base / "sendspin-sync"


#: A ``.part`` file older than this is left from a write that died (a crash, a power cut): removed.
STALE_PART_S = 600.0


@functools.cache
def _digest(kind: str) -> str:
    """A digest of the source of the code that generates *kind*: a changed generator (level, timbre)
    gives a new file name, so an install whose /var/tmp survives an upgrade never serves the old sound."""
    modules = [ct] if kind == "measure" else [lt, ct]
    h = hashlib.sha256(kind.encode() if kind == "measure" else b"listening")
    for module in modules:
        try:
            h.update(Path(module.__file__ or "").read_bytes())
        except OSError:  # no source shipped: fall back to the module's name (never regenerated)
            h.update(module.__name__.encode())
    return h.hexdigest()[:10]


def generator_version(kind: str) -> str:
    return _digest(kind)  # looked up per call, so tests can swap it


class ClickTrack(Protocol):
    def ensure(self) -> Any:
        """Block until the track can be served."""
        ...


class ClickTrackFile:
    """The click track (or, with *kind*, a listening sound) as a WAV file, written once and kept on
    disk (about 35 MB each).

    ``ensure()`` writes it when it is missing or incomplete (to a temporary name, then
    renamed, so a reader never sees half a file); concurrent callers wait for one write.
    Serving it from a file keeps it out of the bridge's memory, and Music Assistant's
    first fetch never waits for a render.
    """

    def __init__(
        self,
        kind: Track = "measure",
        *,
        directory: Callable[[], Path] = _cache_dir,
        duration_s: float = ct.DURATION_S,
        sample_rate: int = ct.SAMPLE_RATE,
        writer: Callable[..., None] | None = None,
    ) -> None:
        self._kind = kind
        if writer is None:
            writer = ct.write_wav if kind == "measure" else functools.partial(lt.write_wav, kind=kind)
        self._dir = directory
        self._duration_s = duration_s
        self._sample_rate = sample_rate
        self._writer = writer
        self._lock = threading.Lock()

    def _prefix(self) -> str:
        return "click" if self._kind == "measure" else f"listen-{self._kind}"

    def path(self) -> Path:
        version = generator_version(self._kind)
        return self._dir() / f"{self._prefix()}-{version}-{self._duration_s:g}s-{self._sample_rate}.wav"

    def _remove_stale(self, directory: Path, keep: Path) -> None:
        """Best effort: this track's files of other versions, and ``.part`` files of a write that died."""
        now = time.time()
        for old in directory.glob(f"{self._prefix()}-*.wav"):
            if old != keep:
                old.unlink(missing_ok=True)  # a reader still streaming it keeps its open file
        for part in directory.glob(".*.part"):
            try:
                if now - part.stat().st_mtime > STALE_PART_S:
                    part.unlink(missing_ok=True)
            except OSError:
                pass

    def _size(self) -> int:
        if self._kind == "measure":
            return ct.wav_size(duration_s=self._duration_s, sample_rate=self._sample_rate)
        return lt.wav_size(self._kind, duration_s=self._duration_s, sample_rate=self._sample_rate)

    def _complete(self, path: Path) -> bool:
        try:
            return path.stat().st_size == self._size()
        except OSError:
            return False

    def ensure(self) -> Path:
        path = self.path()
        if self._complete(path):
            return path
        with self._lock:
            if self._complete(path):
                return path
            tmp: Path | None = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._remove_stale(path.parent, path)
                fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{self._prefix()}-", suffix=".part")
                tmp = Path(tmp_name)
                with os.fdopen(fd, "wb") as fh:
                    self._writer(fh, duration_s=self._duration_s, sample_rate=self._sample_rate)
                tmp.replace(path)
            except OSError as exc:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
                logger.warning("Writing the sync %s track to %s failed: %s", self._kind, path, exc)
                if exc.errno == errno.ENOSPC:
                    raise ControlError("disk_full", f"No space left for the sound file in {path.parent}") from exc
                raise ControlError("track_unavailable", f"The sound file cannot be written: {exc}") from exc
            except BaseException:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
                raise
            logger.info("Sync %s track written to %s", self._kind, path)
            return path


track = ClickTrackFile()
#: Manual tuning's listening sounds, each its own file (written when a session first plays it).
tracks: dict[str, ClickTrackFile] = {kind: ClickTrackFile(kind) for kind in lt.KINDS}


def current_track(kind: Track = "measure") -> ClickTrack:
    # looked up per call, so the module attributes can be swapped (tests)
    return track if kind == "measure" else tracks[kind]


class ClickTrackTokens:
    """Maps a random 32-byte token to the session that owns it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_session: dict[str, str] = {}

    def issue(self, session_id: str) -> str:
        token = secrets.token_hex(32)
        with self._lock:
            self._by_session[session_id] = token
        return token

    def current(self, session_id: str) -> str | None:
        """The session's live token, if it has one."""
        with self._lock:
            return self._by_session.get(session_id)

    def session_for(self, token: str) -> str | None:
        with self._lock:
            items = list(self._by_session.items())
        found: str | None = None
        for sid, known in items:  # compare against all, so timing does not reveal a match
            if hmac.compare_digest(known.encode(), token.encode()):
                found = sid
        return found

    def release(self, session_id: str) -> None:
        with self._lock:
            self._by_session.pop(session_id, None)


tokens = ClickTrackTokens()


_announced_base_url: str | None = None


def _ip_towards_music_assistant() -> str:
    """The local address the kernel would use to reach Music Assistant, or "" when unknown.

    The default route (what ``get_local_ip`` probes) can belong to a VPN that Music Assistant
    cannot reach back through; the route to Music Assistant itself picks its network's interface."""
    host = urlsplit(get_ma_api_credentials()[0] or "").hostname
    if not host:
        return ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))  # UDP connect sends nothing; it only selects the route
            return str(s.getsockname()[0])
    except OSError:
        return ""


def default_base_url() -> str:
    """The bridge as Music Assistant reaches it; logged once per value, with a warning on the loopback fallback."""
    global _announced_base_url
    ip = _ip_towards_music_assistant() or get_local_ip()
    url = f"http://{ip or '127.0.0.1'}:{resolve_web_port()}"
    if url != _announced_base_url:
        _announced_base_url = url
        if ip:
            logger.info("Sync click track is served to Music Assistant from %s", url)
        else:
            logger.warning(
                "No LAN address found: the sync click track URL falls back to %s, "
                "which Music Assistant on another host cannot reach",
                url,
            )
    return url


class FastApiClickTrackHost:
    def __init__(
        self,
        tokens: ClickTrackTokens = tokens,
        base_url: Callable[[], str] = default_base_url,
        track: ClickTrack | None = None,
    ) -> None:
        self._tokens = tokens
        self._base_url = base_url
        self._track = track

    def prepare(self, track: Track = "measure") -> None:
        """Write *track*'s file if it is missing (the session calls this outside its lock); no token."""
        (self._track or current_track(track)).ensure()

    def url_for(self, session_id: str, track: Track = "measure") -> str:
        """The URL Music Assistant fetches *track* from (the same for the whole session); returns only
        once the file is on disk, so the clicker is never started before its first byte can be served.
        The measurement's click track is ``{token}.wav``, a listening sound ``{token}/{kind}.wav``."""
        (self._track or current_track(track)).ensure()
        # one token per session: a clicker started again (or a start that timed out, or another sound)
        # never invalidates the URL Music Assistant may still be streaming
        token = self._tokens.current(session_id) or self._tokens.issue(session_id)
        name = token if track == "measure" else f"{token}/{track}"
        return f"{self._base_url().rstrip('/')}/api/v1/sync/signal/{name}.wav"

    def release(self, session_id: str) -> None:
        self._tokens.release(session_id)  # the file stays on disk for the next session
