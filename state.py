"""
Shared application state for sendspin-bt-bridge.

Single source of truth for the active SendspinClient list, shared between
web_interface.py (reads for API responses) and sendspin_client.py (writes via set_clients).
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Active SendspinClient instances. Mutated in-place so all existing
# references (imported via `from state import clients`) stay valid.
clients: list = []


def set_clients(new_clients: list) -> None:
    """Replace active client list in-place (keeps existing references valid)."""
    clients.clear()
    clients.extend(new_clients if new_clients else [])
    logger.info(f"Client references updated: {len(clients)} client(s)")


# ---------------------------------------------------------------------------
# Adapter name cache — populated from config.json on first use, invalidated on save
# ---------------------------------------------------------------------------
_adapter_name_cache: dict[str, str] = {}
_adapter_cache_loaded = False


def load_adapter_name_cache() -> None:
    """Load adapter friendly names from config.json into the in-memory cache."""
    global _adapter_name_cache, _adapter_cache_loaded
    config_file = Path(os.getenv('CONFIG_DIR', '/config')) / 'config.json'
    try:
        with open(config_file) as _f:
            _cfg = json.load(_f)
        _adapter_name_cache = {
            a.get('mac', a.get('id', '')).upper(): a.get('name', '')
            for a in _cfg.get('BLUETOOTH_ADAPTERS', [])
            if a.get('mac') or a.get('id')
        }
    except Exception as _exc:
        _adapter_name_cache = {}
        logger.debug("Could not load adapter name cache: %s", _exc)
    _adapter_cache_loaded = True


def get_adapter_name(mac_upper: str) -> 'str | None':
    """Return adapter friendly name for the given MAC (uppercase), loading cache if needed."""
    if not _adapter_cache_loaded:
        load_adapter_name_cache()
    return _adapter_name_cache.get(mac_upper) or None
