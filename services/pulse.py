"""PulseAudio helpers via pulsectl_asyncio.

Replaces all subprocess ``pactl`` calls with native PA API calls.
Both sync wrappers (safe from any thread) and async coroutines are provided.

Design note: each call creates a fresh PulseAsync() context rather than keeping
a persistent connection.  This simplifies error recovery (no stale-connection
handling) at the cost of slightly higher overhead per call.  The volume-persist
debounce in routes/api.py limits call frequency in practice.

Graceful fallback: if pulsectl_asyncio is unavailable (import error or
libpulse0 not installed), every function falls back to the equivalent
``pactl`` subprocess invocation so the rest of the codebase never needs
to branch on availability.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "_PULSECTL_AVAILABLE",
    "amove_pid_sink_inputs",
    "get_server_name",
    "get_sink_description",
    "get_sink_input_ids",
    "get_sink_mute",
    "get_sink_volume",
    "list_sinks",
    "set_sink_mute",
    "set_sink_volume",
]

# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

try:
    import pulsectl_asyncio  # type: ignore[import-untyped]

    _PULSECTL_AVAILABLE = True
except (ImportError, OSError) as _err:
    pulsectl_asyncio = None  # type: ignore[assignment,unused-ignore]
    _PULSECTL_AVAILABLE = False
    logger.warning("pulsectl_asyncio unavailable (%s) — falling back to pactl subprocess", _err)

_CLIENT_NAME = "sendspin-bridge"
_TIMEOUT = 5.0  # seconds for any PA operation


# ---------------------------------------------------------------------------
# Internal async helpers
# ---------------------------------------------------------------------------


async def _aget_sink(pulse, sink_name: str) -> Any | None:
    """Return the pulsectl SinkInfo for *sink_name*, or None if not found."""
    try:
        sinks = await pulse.sink_list()
        return next((s for s in sinks if s.name == sink_name), None)
    except Exception as exc:
        logger.debug("pulse: sink_list error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Async public API
# ---------------------------------------------------------------------------


async def alist_sinks() -> list[dict]:
    """Return all PA sinks as dicts with name, description, volume (0-100), muted."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_list_sinks()
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                sinks = await pulse.sink_list()
                return [
                    {
                        "name": s.name,
                        "description": s.description,
                        "volume": max(0, min(100, int(round(s.volume.value_flat * 100)))),
                        "muted": bool(s.mute),
                    }
                    for s in sinks
                ]
    except Exception as exc:
        logger.debug("alist_sinks error: %s — falling back", exc)
        return _fallback_list_sinks()


async def aget_sink_description(sink_name: str) -> str | None:
    """Return the friendly description of *sink_name*, or None."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_get_description(sink_name)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                sink = await _aget_sink(pulse, sink_name)
                return sink.description if sink else None
    except Exception as exc:
        logger.debug("aget_sink_description(%s) error: %s — falling back", sink_name, exc)
        return _fallback_get_description(sink_name)


async def aset_sink_volume(sink_name: str, volume_pct: int) -> bool:
    """Set *sink_name* volume to *volume_pct* (0-100). Returns True on success."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_set_volume(sink_name, volume_pct)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                sink = await _aget_sink(pulse, sink_name)
                if sink is None:
                    logger.warning("aset_sink_volume: sink %s not found", sink_name)
                    return False
                await pulse.volume_set_all_chans(sink, volume_pct / 100.0)
                return True
    except Exception as exc:
        logger.debug(
            "aset_sink_volume(%s, %d) error: %s — falling back",
            sink_name,
            volume_pct,
            exc,
        )
        return _fallback_set_volume(sink_name, volume_pct)


async def aget_sink_volume(sink_name: str) -> int | None:
    """Return current volume of *sink_name* (0-100), or None on error."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_get_volume(sink_name)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                sink = await _aget_sink(pulse, sink_name)
                if sink is None:
                    return None
                return max(0, min(100, int(round(sink.volume.value_flat * 100))))
    except Exception as exc:
        logger.debug("aget_sink_volume(%s) error: %s — falling back", sink_name, exc)
        return _fallback_get_volume(sink_name)


async def aset_sink_mute(sink_name: str, muted: bool | None) -> bool:
    """Set or toggle mute on *sink_name*. muted=None toggles. Returns True on success."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_set_mute(sink_name, muted)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                sink = await _aget_sink(pulse, sink_name)
                if sink is None:
                    logger.warning("aset_sink_mute: sink %s not found", sink_name)
                    return False
                new_mute = (not bool(sink.mute)) if muted is None else muted
                await pulse.mute(sink, new_mute)
                return True
    except Exception as exc:
        logger.debug("aset_sink_mute(%s) error: %s — falling back", sink_name, exc)
        return _fallback_set_mute(sink_name, muted)


async def aget_sink_mute(sink_name: str) -> bool | None:
    """Return current mute state of *sink_name*, or None on error."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_get_mute(sink_name)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                sink = await _aget_sink(pulse, sink_name)
                return bool(sink.mute) if sink else None
    except Exception as exc:
        logger.debug("aget_sink_mute(%s) error: %s — falling back", sink_name, exc)
        return _fallback_get_mute(sink_name)


async def alist_sink_input_ids() -> set[int]:
    """Return set of current PA sink-input indices."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_sink_input_ids()
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                inputs = await pulse.sink_input_list()
                return {i.index for i in inputs}
    except Exception as exc:
        logger.debug("alist_sink_input_ids error: %s — falling back", exc)
        return _fallback_sink_input_ids()


async def amove_sink_input(sink_input_idx: int, sink_name: str) -> bool:
    """Move sink-input *sink_input_idx* to sink *sink_name*. Returns True on success."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_move_sink_input(sink_input_idx, sink_name)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                inputs = await pulse.sink_input_list()
                si = next((i for i in inputs if i.index == sink_input_idx), None)
                if si is None:
                    logger.warning("amove_sink_input: sink-input %d not found", sink_input_idx)
                    return False
                sinks = await pulse.sink_list()
                sink = next((s for s in sinks if s.name == sink_name), None)
                if sink is None:
                    logger.warning("amove_sink_input: sink %s not found", sink_name)
                    return False
                await pulse.sink_input_move(si, sink)
                return True
    except Exception as exc:
        logger.debug(
            "amove_sink_input(%d, %s) error: %s — falling back",
            sink_input_idx,
            sink_name,
            exc,
        )
        return _fallback_move_sink_input(sink_input_idx, sink_name)


async def amove_pid_sink_inputs(pid: int, sink_name: str) -> int:
    """Move all sink-inputs belonging to *pid* to *sink_name*.

    Returns the number of sink-inputs moved (0 means nothing to do or nothing found).
    Used from within a daemon subprocess to correct PipeWire auto-routing after a BT
    sink disappears and re-appears: PULSE_SINK handles initial routing but WirePlumber
    may re-route streams to the default sink during reconnect events.
    """
    if not _PULSECTL_AVAILABLE:
        return _fallback_move_pid_sink_inputs(pid, sink_name)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                inputs = await pulse.sink_input_list()
                sinks = await pulse.sink_list()
                target = next((s for s in sinks if s.name == sink_name), None)
                if target is None:
                    logger.debug("amove_pid_sink_inputs: sink %s not found", sink_name)
                    return 0
                moved = 0
                for si in inputs:
                    props = getattr(si, "proplist", {}) or {}
                    if str(props.get("application.process.id", "")) == str(pid):
                        if si.sink != target.index:
                            await pulse.sink_input_move(si, target)
                            logger.info("Moved sink-input %d (pid=%d) → %s", si.index, pid, sink_name)
                            moved += 1
                return moved
    except Exception as exc:
        logger.debug("amove_pid_sink_inputs(%d, %s) error: %s — falling back", pid, sink_name, exc)
        return _fallback_move_pid_sink_inputs(pid, sink_name)


async def aget_server_name() -> str:
    """Return PA server name string (for diagnostics)."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_server_name()
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                info = await pulse.server_info()
                return info.server_name or "running"
    except Exception as exc:
        logger.debug("aget_server_name error: %s — falling back", exc)
        return _fallback_server_name()


# ---------------------------------------------------------------------------
# Sync public API (thread-safe wrappers using a fresh event loop)
# ---------------------------------------------------------------------------


_thread_local = threading.local()
_thread_loops: list = []  # Track for cleanup
_thread_loops_lock = threading.Lock()


def _run(coro):
    """Run *coro* synchronously, reusing a thread-local event loop."""
    loop = getattr(_thread_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _thread_local.loop = loop
        with _thread_loops_lock:
            _thread_loops.append(loop)
    return loop.run_until_complete(coro)


@atexit.register
def _cleanup_loops():
    with _thread_loops_lock:
        loops = list(_thread_loops)
        _thread_loops.clear()
    for loop in loops:
        if not loop.is_closed():
            loop.close()


def list_sinks() -> list[dict]:
    return _run(alist_sinks())


def get_sink_description(sink_name: str) -> str | None:
    return _run(aget_sink_description(sink_name))


def set_sink_volume(sink_name: str, volume_pct: int) -> bool:
    return _run(aset_sink_volume(sink_name, volume_pct))


def get_sink_volume(sink_name: str) -> int | None:
    return _run(aget_sink_volume(sink_name))


def set_sink_mute(sink_name: str, muted: bool | None) -> bool:
    return _run(aset_sink_mute(sink_name, muted))


def get_sink_mute(sink_name: str) -> bool | None:
    return _run(aget_sink_mute(sink_name))


def get_server_name() -> str:
    return _run(aget_server_name())


# ---------------------------------------------------------------------------
# Subprocess fallbacks (mirror pactl behaviour when pulsectl unavailable)
# ---------------------------------------------------------------------------


def _fallback_list_sinks() -> list[dict]:
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        result = []
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                result.append(
                    {
                        "name": parts[1],
                        "description": parts[1],
                        "volume": None,
                        "muted": None,
                    }
                )
        return result
    except (subprocess.SubprocessError, OSError):
        return []


def _fallback_get_description(sink_name: str) -> str | None:
    try:
        r = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True, timeout=5)
        in_target = False
        for line in r.stdout.splitlines():
            s = line.strip()
            if s.startswith("Name:") and sink_name in s:
                in_target = True
            elif in_target and s.startswith("Description:"):
                return s.split(":", 1)[1].strip()
            elif in_target and s.startswith("Name:"):
                break
    except Exception as exc:
        logger.debug("get sink description failed: %s", exc)
    return None


def _fallback_set_volume(sink_name: str, volume_pct: int) -> bool:
    volume_pct = max(0, min(100, volume_pct))
    try:
        r = subprocess.run(
            ["pactl", "set-sink-volume", sink_name, f"{volume_pct}%"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _fallback_get_volume(sink_name: str) -> int | None:
    try:
        r = subprocess.run(
            ["pactl", "get-sink-volume", sink_name],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0:
            import re

            m = re.search(r"(\d+)%", r.stdout)
            return int(m.group(1)) if m else None
    except Exception as exc:
        logger.debug("get sink volume failed: %s", exc)
    return None


def _fallback_set_mute(sink_name: str, muted: bool | None) -> bool:
    arg = "toggle" if muted is None else ("1" if muted else "0")
    try:
        r = subprocess.run(
            ["pactl", "set-sink-mute", sink_name, arg],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _fallback_get_mute(sink_name: str) -> bool | None:
    try:
        r = subprocess.run(
            ["pactl", "get-sink-mute", sink_name],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0:
            return "yes" in r.stdout.lower()
    except Exception as exc:
        logger.debug("get sink mute failed: %s", exc)
    return None


def _fallback_server_name() -> str:
    try:
        r = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if "Server Name" in line:
                    return line.split(":", 1)[-1].strip()
    except Exception as exc:
        logger.debug("get server name failed: %s", exc)
    return "not available"


def _fallback_sink_input_ids() -> set[int]:
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ids: set[int] = set()
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if parts:
                try:
                    ids.add(int(parts[0]))
                except ValueError as exc:
                    logger.debug("parse sink-input id failed: %s", exc)
        return ids
    except (subprocess.SubprocessError, OSError):
        return set()


def _fallback_move_sink_input(sink_input_idx: int, sink_name: str) -> bool:
    try:
        r = subprocess.run(
            ["pactl", "move-sink-input", str(sink_input_idx), sink_name],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0:
            logger.info("Moved sink-input %d → %s", sink_input_idx, sink_name)
        else:
            logger.warning(
                "move_sink_input(%d → %s) failed (rc=%d): %s",
                sink_input_idx,
                sink_name,
                r.returncode,
                r.stderr.strip(),
            )
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _fallback_move_pid_sink_inputs(pid: int, sink_name: str) -> int:
    """pactl fallback: find sink-inputs by pid and move them to sink_name."""
    try:
        # Get all sink-input details to find our PID
        r = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return 0
        moved = 0
        current_id: int | None = None
        current_pid: str | None = None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("Sink Input #"):
                current_id = int(line.split("#")[1])
                current_pid = None
            elif "application.process.id" in line:
                current_pid = line.split("=", 1)[-1].strip().strip('"')
            if current_id is not None and current_pid == str(pid):
                r2 = subprocess.run(
                    ["pactl", "move-sink-input", str(current_id), sink_name],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if r2.returncode == 0:
                    logger.info("Moved sink-input %d (pid=%d) → %s", current_id, pid, sink_name)
                    moved += 1
                current_id = None
                current_pid = None
        return moved
    except (subprocess.SubprocessError, OSError):
        return 0


def get_sink_input_ids() -> set[int]:
    """Return the set of currently active sink-input IDs (sync)."""
    return _run(alist_sink_input_ids())
