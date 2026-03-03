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
        logger.debug("amove_sink_input(%d, %s) error: %s — falling back",
                     sink_input_idx, sink_name, exc)
        return _fallback_move_sink_input(sink_input_idx, sink_name)


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


def _fallback_sink_input_ids() -> set[int]:
    try:
        r = subprocess.run(['pactl', 'list', 'short', 'sink-inputs'],
                           capture_output=True, text=True, timeout=5)
        ids: set[int] = set()
        for line in r.stdout.splitlines():
            parts = line.split('\t')
            if parts:
                try:
                    ids.add(int(parts[0]))
                except ValueError:
                    pass
        return ids
    except Exception:
        return set()


def _fallback_move_sink_input(sink_input_idx: int, sink_name: str) -> bool:
    try:
        r = subprocess.run(
            ['pactl', 'move-sink-input', str(sink_input_idx), sink_name],
            capture_output=True, text=True, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# PA module management (null-sink / loopback)
# ---------------------------------------------------------------------------

def check_sink_exists(sink_name: str) -> bool:
    """Return True if a PA sink with *sink_name* already exists."""
    try:
        r = subprocess.run(
            ['pactl', 'list', 'short', 'sinks'],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.splitlines():
            parts = line.split('\t')
            if len(parts) >= 2 and parts[1] == sink_name:
                return True
    except Exception as exc:
        logger.debug("check_sink_exists(%s) error: %s", sink_name, exc)
    return False


def load_null_sink(sink_name: str, description: str) -> int | None:
    """Load a ``module-null-sink`` and return the module index, or None on error.

    Idempotent: if a sink with *sink_name* already exists, returns -1 (skip).
    """
    if check_sink_exists(sink_name):
        logger.info("Null-sink %s already exists — skipping", sink_name)
        return -1
    try:
        # Use shell=True so bash handles quoting of description with spaces
        import shlex
        desc_arg = shlex.quote(f'sink_properties=device.description={description}')
        cmd = f'pactl load-module module-null-sink sink_name={sink_name} {desc_arg}'
        logger.info("Creating null-sink: %s", cmd)
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            module_id = int(r.stdout.strip())
            logger.info("Loaded module-null-sink %s (module %d)", sink_name, module_id)
            return module_id
        logger.warning("load_null_sink(%s) failed (rc=%d): %s", sink_name, r.returncode, r.stderr.strip())
    except Exception as exc:
        logger.warning("load_null_sink(%s) error: %s", sink_name, exc)
    return None


def load_loopback(source: str, sink: str, latency_msec: int = 50) -> int | None:
    """Load a ``module-loopback`` from *source* to *sink*. Returns module index or None."""
    try:
        r = subprocess.run(
            ['pactl', 'load-module', 'module-loopback',
             f'source={source}', f'sink={sink}', f'latency_msec={latency_msec}'],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            module_id = int(r.stdout.strip())
            logger.info("Loaded module-loopback %s → %s (module %d)", source, sink, module_id)
            return module_id
        logger.warning("load_loopback(%s → %s) failed (rc=%d): %s",
                        source, sink, r.returncode, r.stderr.strip())
    except Exception as exc:
        logger.warning("load_loopback(%s → %s) error: %s", source, sink, exc)
    return None


def unload_module(module_id: int) -> bool:
    """Unload a PA module by index. Returns True on success."""
    if module_id is None or module_id < 0:
        return False
    try:
        r = subprocess.run(
            ['pactl', 'unload-module', str(module_id)],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            logger.debug("Unloaded PA module %d", module_id)
            return True
        logger.debug("unload_module(%d) failed: %s", module_id, r.stderr.strip())
    except Exception as exc:
        logger.debug("unload_module(%d) error: %s", module_id, exc)
    return False


# Async wrappers (run subprocess in executor to avoid blocking event loop)

async def aload_null_sink(sink_name: str, description: str) -> int | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_null_sink, sink_name, description)


async def aload_loopback(source: str, sink: str, latency_msec: int = 50) -> int | None:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_loopback, source, sink, latency_msec)


async def aunload_module(module_id: int) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, unload_module, module_id)
