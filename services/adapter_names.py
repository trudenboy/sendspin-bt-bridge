"""Adapter-name cache owner used by route and diagnostics code."""

from __future__ import annotations

import json
import logging
import threading

from config import CONFIG_FILE

logger = logging.getLogger(__name__)

_adapter_names_by_mac: dict[str, str] = {}
_adapter_cache_lock = threading.Lock()


def load_adapter_name_cache() -> None:
    """Load adapter-name cache from config.

    Called either standalone (already under ``_adapter_cache_lock``) or
    from ``get_adapter_name`` which acquires the lock first.
    ``_adapter_cache_lock`` is **not** reentrant, so this function must
    not reacquire it.
    """
    global _adapter_names_by_mac
    _adapter_names_by_mac = {}
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
        adapters = cfg.get("adapters") or cfg.get("BLUETOOTH_ADAPTERS") or []
        for adapter in adapters:
            mac = str(adapter.get("mac") or adapter.get("id") or "").upper()
            name = str(adapter.get("name") or "").strip()
            if mac and name:
                _adapter_names_by_mac[mac] = name
    except Exception as exc:
        logger.debug("Failed to load adapter name cache: %s", exc)


def get_adapter_name(mac_upper: str) -> str | None:
    """Return cached adapter friendly name for the given uppercase MAC."""
    if mac_upper:
        with _adapter_cache_lock:
            if not _adapter_names_by_mac:
                load_adapter_name_cache()
            return _adapter_names_by_mac.get(mac_upper)
    return None


def refresh_adapter_name_cache() -> None:
    """Reload adapter-name cache from config under the shared adapter cache lock."""
    with _adapter_cache_lock:
        load_adapter_name_cache()
