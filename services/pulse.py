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

STANDBY_SINK_NAME = "sendspin_fallback"

__all__ = [
    "STANDBY_SINK_NAME",
    "_PULSECTL_AVAILABLE",
    "acycle_card_profile",
    "aensure_null_sink",
    "alist_cards",
    "amove_pid_sink_inputs",
    "areload_bluez5_discover_module",
    "aset_card_profile",
    "asuspend_sink",
    "cycle_card_profile",
    "ensure_null_sink",
    "get_server_name",
    "get_sink_description",
    "get_sink_input_ids",
    "get_sink_mute",
    "get_sink_volume",
    "list_cards",
    "list_sinks",
    "reload_bluez5_discover_module",
    "remove_null_sink",
    "set_card_profile",
    "set_sink_mute",
    "set_sink_volume",
    "suspend_sink",
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


async def asuspend_sink(sink_name: str, suspend: bool) -> bool:
    """Suspend or resume *sink_name*.  Returns True on success.

    Suspending a Bluetooth sink releases the A2DP transport, allowing
    the speaker to enter its own power-save mode while keeping the BT
    link connected.  Resuming re-opens the transport.
    """
    if not _PULSECTL_AVAILABLE:
        return _fallback_suspend_sink(sink_name, suspend)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                sink = await _aget_sink(pulse, sink_name)
                if sink is None:
                    logger.warning("asuspend_sink: sink %s not found", sink_name)
                    return False
                await pulse.sink_suspend(sink.index, suspend)
                logger.debug("asuspend_sink(%s, %s) ok", sink_name, suspend)
                return True
    except Exception as exc:
        logger.debug("asuspend_sink(%s) error: %s — falling back", sink_name, exc)
        return _fallback_suspend_sink(sink_name, suspend)


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


async def alist_cards() -> list[dict]:
    """Return all PA cards as dicts with name, driver, active_profile, profiles.

    Used for diagnostics and for the bluez-profile auto-switch path in
    ``bt_audio.py``: if a BT speaker connects but no ``bluez_*`` sink is
    ever created, the operator needs to see whether a ``bluez_card.*``
    exists with a non-``a2dp_sink`` active profile (e.g. ``headset_head_unit``
    or ``off``) so the bridge can flip it to A2DP.
    """
    if not _PULSECTL_AVAILABLE:
        return _fallback_list_cards()
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                cards = await pulse.card_list()
                result: list[dict] = []
                for c in cards:
                    active = getattr(c, "profile_active", None)
                    active_name = getattr(active, "name", None) if active else None
                    profile_list = getattr(c, "profile_list", []) or []
                    result.append(
                        {
                            "name": c.name,
                            "driver": getattr(c, "driver", "") or "",
                            "active_profile": active_name,
                            "profiles": [getattr(p, "name", "") for p in profile_list if getattr(p, "name", "")],
                        }
                    )
                return result
    except Exception as exc:
        logger.debug("alist_cards error: %s — falling back", exc)
        return _fallback_list_cards()


async def aset_card_profile(card_name: str, profile: str) -> bool:
    """Set *card_name* to *profile* (e.g. ``a2dp_sink``). Returns True on success.

    Used when a Bluetooth device is connected but its card is stuck on a
    non-audio-sink profile such as ``headset_head_unit``.  Switching to
    ``a2dp_sink`` causes PulseAudio to publish a ``bluez_sink.*`` sink
    suitable for A2DP playback.
    """
    if not _PULSECTL_AVAILABLE:
        return _fallback_set_card_profile(card_name, profile)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                cards = await pulse.card_list()
                card = next((c for c in cards if c.name == card_name), None)
                if card is None:
                    logger.warning("aset_card_profile: card %s not found", card_name)
                    return False
                await pulse.card_profile_set(card, profile)
                return True
    except Exception as exc:
        logger.debug("aset_card_profile(%s, %s) error: %s — falling back", card_name, profile, exc)
        return _fallback_set_card_profile(card_name, profile)


async def acycle_card_profile(card_name: str, target: str, off_wait: float = 1.0) -> bool:
    """Set *card_name* profile to ``off``, wait, then switch to *target*.

    Forces PulseAudio to re-publish ``bluez_sink.*`` after ``module-rescue-streams``
    or other state-confusion scenarios where a direct profile switch leaves
    the sink missing. Returns True if the final target switch succeeded.
    """
    if not _PULSECTL_AVAILABLE:
        return _fallback_cycle_card_profile(card_name, target, off_wait)
    try:
        async with asyncio.timeout(_TIMEOUT):
            async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                cards = await pulse.card_list()
                card = next((c for c in cards if c.name == card_name), None)
                if card is None:
                    logger.warning("acycle_card_profile: card %s not found", card_name)
                    return False
                try:
                    await pulse.card_profile_set(card, "off")
                except Exception as exc:
                    logger.debug("acycle_card_profile: off-set failed: %s", exc)
                await asyncio.sleep(max(0.0, off_wait))
                # Re-fetch the card after the off-cycle — its internal ID can change.
                cards = await pulse.card_list()
                card = next((c for c in cards if c.name == card_name), None)
                if card is None:
                    logger.warning("acycle_card_profile: card %s vanished after off-cycle", card_name)
                    return False
                await pulse.card_profile_set(card, target)
                logger.info("Cycled card profile: %s → off → %s", card_name, target)
                return True
    except Exception as exc:
        logger.debug("acycle_card_profile(%s, %s) error: %s — falling back", card_name, target, exc)
        return _fallback_cycle_card_profile(card_name, target, off_wait)


# Global throttle for module-bluez5-discover reload — it nukes all BT sinks.
_LAST_BLUEZ5_RELOAD_TS: float = 0.0
_BLUEZ5_RELOAD_COOLDOWN: float = 60.0
_BLUEZ5_RELOAD_LOCK = threading.Lock()


async def areload_bluez5_discover_module() -> bool:
    """Unload and reload ``module-bluez5-discover`` via pactl.

    Last-resort recovery when ``bluez_card.*`` fails to register after a
    successful Bluetooth connect. Disruptive: drops every other active BT
    sink, so it is throttled to at most once per 60 seconds across the
    whole bridge. Returns True only if the reload actually happened.
    """
    import time as _time

    global _LAST_BLUEZ5_RELOAD_TS
    with _BLUEZ5_RELOAD_LOCK:
        now = _time.monotonic()
        if now - _LAST_BLUEZ5_RELOAD_TS < _BLUEZ5_RELOAD_COOLDOWN:
            logger.info(
                "module-bluez5-discover reload skipped — cooldown active (%.1fs since last)",
                now - _LAST_BLUEZ5_RELOAD_TS,
            )
            return False
        _LAST_BLUEZ5_RELOAD_TS = now
    try:
        list_proc = await asyncio.create_subprocess_exec(
            "pactl",
            "list",
            "modules",
            "short",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await list_proc.communicate()
    except (OSError, asyncio.CancelledError) as exc:
        logger.warning("areload_bluez5_discover_module list failed: %s", exc)
        return False
    if list_proc.returncode != 0:
        logger.warning("pactl list modules short failed (rc=%s)", list_proc.returncode)
        return False
    module_idx: str | None = None
    for line in stdout.decode(errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[1].strip() == "module-bluez5-discover":
            module_idx = parts[0].strip()
            break
    if module_idx is None:
        logger.info("module-bluez5-discover not currently loaded; nothing to reload")
        return False
    try:
        unload_proc = await asyncio.create_subprocess_exec(
            "pactl",
            "unload-module",
            module_idx,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, unload_err = await unload_proc.communicate()
    except (OSError, asyncio.CancelledError) as exc:
        logger.warning("pactl unload-module failed: %s", exc)
        return False
    if unload_proc.returncode != 0:
        logger.warning(
            "pactl unload-module %s failed: %s",
            module_idx,
            unload_err.decode(errors="replace").strip(),
        )
        return False
    await asyncio.sleep(2.0)
    try:
        load_proc = await asyncio.create_subprocess_exec(
            "pactl",
            "load-module",
            "module-bluez5-discover",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, load_err = await load_proc.communicate()
    except (OSError, asyncio.CancelledError) as exc:
        logger.warning("pactl load-module module-bluez5-discover failed: %s", exc)
        return False
    if load_proc.returncode != 0:
        logger.warning(
            "pactl load-module module-bluez5-discover failed: %s",
            load_err.decode(errors="replace").strip(),
        )
        return False
    logger.warning("Reloaded module-bluez5-discover as BT sink recovery last resort")
    return True


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
# Null sink for standby (Phase 2: keep daemon alive on silent sink)
# ---------------------------------------------------------------------------

_null_sink_module_id: int | None = None


async def aensure_null_sink() -> bool:
    """Create the shared standby null sink if it doesn't already exist.

    Returns True if the sink exists (created or already present).
    Uses ``module-null-sink`` so audio routed here is silently discarded.
    """
    global _null_sink_module_id
    # Check if sink already exists
    if _PULSECTL_AVAILABLE:
        try:
            async with asyncio.timeout(_TIMEOUT):
                async with pulsectl_asyncio.PulseAsync(_CLIENT_NAME) as pulse:
                    sinks = await pulse.sink_list()
                    if any(s.name == STANDBY_SINK_NAME for s in sinks):
                        logger.debug("Standby null sink already exists")
                        return True
        except Exception as exc:
            logger.debug("aensure_null_sink check error: %s", exc)
    # Create via pactl (works with both pulsectl and fallback)
    return _fallback_load_null_sink()


def ensure_null_sink() -> bool:
    """Sync wrapper: create standby null sink if missing."""
    return _run(aensure_null_sink())


def remove_null_sink() -> bool:
    """Unload the standby null sink module. Returns True on success."""
    global _null_sink_module_id
    if _null_sink_module_id is None:
        return True
    try:
        r = subprocess.run(
            ["pactl", "unload-module", str(_null_sink_module_id)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            logger.info("Removed standby null sink (module %d)", _null_sink_module_id)
            _null_sink_module_id = None
            return True
        logger.warning("Failed to remove null sink module %d: %s", _null_sink_module_id, r.stderr.strip())
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("remove_null_sink error: %s", exc)
    return False


def _fallback_load_null_sink() -> bool:
    """Create null sink via pactl subprocess."""
    global _null_sink_module_id
    try:
        # Check if already exists
        r = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and STANDBY_SINK_NAME in r.stdout:
            logger.debug("Standby null sink already exists (pactl)")
            return True
        r = subprocess.run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                f"sink_name={STANDBY_SINK_NAME}",
                "sink_properties=device.description=Sendspin\\ Standby",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            mid = r.stdout.strip()
            _null_sink_module_id = int(mid) if mid.isdigit() else None
            logger.info("Created standby null sink (%s)", STANDBY_SINK_NAME)
            return True
        logger.warning("Failed to create null sink: %s", r.stderr.strip())
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("_fallback_load_null_sink error: %s", exc)
    return False


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


def suspend_sink(sink_name: str, suspend: bool) -> bool:
    return _run(asuspend_sink(sink_name, suspend))


def get_server_name() -> str:
    return _run(aget_server_name())


def list_cards() -> list[dict]:
    return _run(alist_cards())


def set_card_profile(card_name: str, profile: str) -> bool:
    return _run(aset_card_profile(card_name, profile))


def cycle_card_profile(card_name: str, target: str, off_wait: float = 1.0) -> bool:
    return _run(acycle_card_profile(card_name, target, off_wait))


def reload_bluez5_discover_module() -> bool:
    return _run(areload_bluez5_discover_module())


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


def _fallback_suspend_sink(sink_name: str, suspend: bool) -> bool:
    try:
        r = subprocess.run(
            ["pactl", "suspend-sink", sink_name, "1" if suspend else "0"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0:
            logger.debug("pactl suspend-sink %s %s ok", sink_name, suspend)
            return True
        logger.warning("pactl suspend-sink failed: %s", r.stderr.strip())
    except Exception as exc:
        logger.debug("suspend-sink fallback failed: %s", exc)
    return False


def _fallback_list_cards() -> list[dict]:
    """Parse `pactl list cards` output into name/driver/active_profile/profiles dicts."""
    try:
        r = subprocess.run(["pactl", "list", "cards"], capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0:
        return []
    cards: list[dict] = []
    current: dict | None = None
    in_profiles = False
    for raw in r.stdout.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if line.startswith("Card #"):
            if current is not None:
                cards.append(current)
            current = {"name": "", "driver": "", "active_profile": None, "profiles": []}
            in_profiles = False
            continue
        if current is None:
            continue
        if stripped.startswith("Name:"):
            current["name"] = stripped.split(":", 1)[1].strip()
            in_profiles = False
        elif stripped.startswith("Driver:"):
            current["driver"] = stripped.split(":", 1)[1].strip()
            in_profiles = False
        elif stripped.startswith("Active Profile:"):
            current["active_profile"] = stripped.split(":", 1)[1].strip()
            in_profiles = False
        elif stripped == "Profiles:":
            in_profiles = True
        elif in_profiles and ":" in stripped and not stripped.startswith(("Ports", "Sinks", "Sources", "Properties")):
            # Profile line: "a2dp_sink: High Fidelity Playback (A2DP Sink) (sinks: 1, ...)"
            prof_name = stripped.split(":", 1)[0].strip()
            if prof_name:
                current["profiles"].append(prof_name)
        elif stripped.startswith(("Ports:", "Sinks:", "Sources:", "Properties:")):
            in_profiles = False
    if current is not None:
        cards.append(current)
    return cards


def _fallback_set_card_profile(card_name: str, profile: str) -> bool:
    """Set *card_name* profile via `pactl set-card-profile`. Returns True on success."""
    try:
        r = subprocess.run(
            ["pactl", "set-card-profile", card_name, profile],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            logger.info("Set card profile: %s → %s", card_name, profile)
            return True
        logger.warning(
            "pactl set-card-profile %s %s failed: %s",
            card_name,
            profile,
            r.stderr.strip(),
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("_fallback_set_card_profile error: %s", exc)
    return False


def _fallback_cycle_card_profile(card_name: str, target: str, off_wait: float = 1.0) -> bool:
    """Force PA to re-publish the sink via ``pactl set-card-profile`` off→target."""
    import time as _time

    # off-set is best-effort: if the card is already in "off" state it will
    # still succeed, and errors here should not block the target switch.
    try:
        subprocess.run(
            ["pactl", "set-card-profile", card_name, "off"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("_fallback_cycle_card_profile off-set error: %s", exc)
    _time.sleep(max(0.0, off_wait))
    return _fallback_set_card_profile(card_name, target)


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
