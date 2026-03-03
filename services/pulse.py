"""PulseAudio helpers via pulsectl_asyncio.

Replaces all subprocess ``pactl`` calls with native PA API calls.
Both sync wrappers (safe from any thread) and async coroutines are provided.

Graceful fallback: if pulsectl_asyncio is unavailable (import error or
libpulse0 not installed), every function falls back to the equivalent
``pactl`` subprocess invocation so the rest of the codebase never needs
to branch on availability.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

try:
    import pulsectl_asyncio  # type: ignore[import]
    _PULSECTL_AVAILABLE = True
except (ImportError, OSError) as _err:
    pulsectl_asyncio = None  # type: ignore[assignment]
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
                        'name': s.name,
                        'description': s.description,
                        'volume': max(0, min(100, int(round(s.volume.value_flat * 100)))),
                        'muted': bool(s.mute),
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
        logger.debug("aset_sink_volume(%s, %d) error: %s — falling back", sink_name, volume_pct, exc)
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


async def aget_server_name() -> str:
    """Return PA server name string (for diagnostics)."""
    if not _PULSECTL_AVAILABLE:
        return _fallback_server_name()
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                info = await pulse.server_info()
                return info.server_name or 'running'
    except Exception as exc:
        logger.debug("aget_server_name error: %s — falling back", exc)
        return _fallback_server_name()


# ---------------------------------------------------------------------------
# Sync public API (thread-safe wrappers using a fresh event loop)
# ---------------------------------------------------------------------------

def _run(coro):
    """Run *coro* synchronously in a fresh event loop (safe from any thread)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
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
        r = subprocess.run(['pactl', 'list', 'short', 'sinks'],
                           capture_output=True, text=True, timeout=5)
        result = []
        for line in r.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) >= 2:
                result.append({'name': parts[1], 'description': parts[1], 'volume': None, 'muted': None})
        return result
    except Exception:
        return []


def _fallback_get_description(sink_name: str) -> str | None:
    try:
        r = subprocess.run(['pactl', 'list', 'sinks'], capture_output=True, text=True, timeout=5)
        in_target = False
        for line in r.stdout.splitlines():
            s = line.strip()
            if s.startswith('Name:') and sink_name in s:
                in_target = True
            elif in_target and s.startswith('Description:'):
                return s.split(':', 1)[1].strip()
            elif in_target and s.startswith('Name:'):
                break
    except Exception:
        pass
    return None


def _fallback_set_volume(sink_name: str, volume_pct: int) -> bool:
    try:
        r = subprocess.run(['pactl', 'set-sink-volume', sink_name, f'{volume_pct}%'],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _fallback_get_volume(sink_name: str) -> int | None:
    try:
        r = subprocess.run(['pactl', 'get-sink-volume', sink_name],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            import re
            m = re.search(r'(\d+)%', r.stdout)
            return int(m.group(1)) if m else None
    except Exception:
        pass
    return None


def _fallback_set_mute(sink_name: str, muted: bool | None) -> bool:
    arg = 'toggle' if muted is None else ('1' if muted else '0')
    try:
        r = subprocess.run(['pactl', 'set-sink-mute', sink_name, arg],
                           capture_output=True, text=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def _fallback_get_mute(sink_name: str) -> bool | None:
    try:
        r = subprocess.run(['pactl', 'get-sink-mute', sink_name],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            return 'yes' in r.stdout.lower()
    except Exception:
        pass
    return None


def _fallback_server_name() -> str:
    try:
        r = subprocess.run(['pactl', 'info'], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if 'Server Name' in line:
                    return line.split(':', 1)[-1].strip()
    except Exception:
        pass
    return 'not available'
