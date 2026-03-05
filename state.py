"""
Shared application state for sendspin-bt-bridge.

Single source of truth for the active SendspinClient list, shared between
web_interface.py (reads for API responses) and sendspin_client.py (writes via set_clients).
"""

import asyncio
import json
import logging
import threading
import time as _time

from config import CONFIG_FILE as _config_file

# ---------------------------------------------------------------------------
# SSE status-change signalling — used by /api/status/stream
# ---------------------------------------------------------------------------
_status_version: int = 0
# Condition used instead of a plain Event to avoid a race between the version
# check and the wait() call in the SSE generator (a plain set()+clear() can
# lose the wakeup if notify fires between them).
_status_condition: threading.Condition = threading.Condition()


_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the main asyncio event loop for use by Flask/WSGI threads."""
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    """Return the main asyncio event loop, or None if not yet set."""
    return _main_loop


_notify_lock: threading.Lock = threading.Lock()
_notify_timer: threading.Timer | None = None


def notify_status_changed() -> None:
    """Signal that at least one client status has changed (wakes SSE listeners).

    Thread-safe: may be called from any thread.
    Notifications are batched within a 100 ms window to prevent SSE storms
    when many devices update simultaneously (e.g., mass reconnect).
    """
    global _notify_timer
    with _notify_lock:
        if _notify_timer is None or not _notify_timer.is_alive():
            _notify_timer = threading.Timer(0.1, _flush_notify)
            _notify_timer.daemon = True
            _notify_timer.start()


def _flush_notify() -> None:
    """Deliver the batched notification to SSE listeners."""
    global _status_version
    with _status_condition:
        _status_version += 1
        _status_condition.notify_all()


logger = logging.getLogger(__name__)

# Active SendspinClient instances. Mutated in-place so all existing
# references (imported via `from state import clients`) stay valid.
clients: list = []
_clients_lock = threading.Lock()


def set_clients(new_clients: list) -> None:
    """Replace active client list in-place (keeps existing references valid)."""
    with _clients_lock:
        clients.clear()
        clients.extend(new_clients if new_clients else [])
    logger.info(f"Client references updated: {len(clients)} client(s)")


# ---------------------------------------------------------------------------
# Adapter name cache — populated from config.json on first use, invalidated on save
# ---------------------------------------------------------------------------
_adapter_name_cache: dict[str, str] = {}
_adapter_cache_loaded = False
_adapter_cache_lock = threading.Lock()


def load_adapter_name_cache() -> None:
    """Load adapter friendly names from config.json into the in-memory cache."""
    global _adapter_name_cache, _adapter_cache_loaded
    try:
        with open(_config_file) as _f:
            _cfg = json.load(_f)
        _adapter_name_cache = {
            a.get("mac", a.get("id", "")).upper(): a.get("name", "")
            for a in _cfg.get("BLUETOOTH_ADAPTERS", [])
            if a.get("mac") or a.get("id")
        }
    except Exception as _exc:
        _adapter_name_cache = {}
        logger.debug("Could not load adapter name cache: %s", _exc)
    _adapter_cache_loaded = True


def get_adapter_name(mac_upper: str) -> "str | None":
    """Return adapter friendly name for the given MAC (uppercase), loading cache if needed."""
    if not _adapter_cache_loaded:
        with _adapter_cache_lock:
            if not _adapter_cache_loaded:  # double-checked locking
                load_adapter_name_cache()
    return _adapter_name_cache.get(mac_upper)


# ---------------------------------------------------------------------------
# BT scan job store — keyed by UUID, TTL ~2 min
# ---------------------------------------------------------------------------

_scan_jobs: dict[str, dict] = {}
_scan_jobs_lock = threading.Lock()
_SCAN_JOB_TTL = 120  # seconds


def create_scan_job(job_id: str) -> None:
    """Register a new in-progress scan job."""
    with _scan_jobs_lock:
        # Evict expired jobs before adding new one
        now = _time.time()
        expired = [jid for jid, j in _scan_jobs.items() if now - j.get("created", 0) > _SCAN_JOB_TTL]
        for jid in expired:
            del _scan_jobs[jid]
        _scan_jobs[job_id] = {"status": "running", "created": now}


def finish_scan_job(job_id: str, result: dict) -> None:
    """Mark a scan job as done with its result."""
    with _scan_jobs_lock:
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["status"] = "done"
            _scan_jobs[job_id].update(result)


def get_scan_job(job_id: str) -> "dict | None":
    """Return the job dict or None if not found."""
    with _scan_jobs_lock:
        return _scan_jobs.get(job_id)
