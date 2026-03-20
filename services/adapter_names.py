"""Adapter-name cache helpers used by route and diagnostics code."""

from __future__ import annotations

import state


def get_adapter_name(mac_upper: str) -> str | None:
    """Return a cached adapter friendly name for the given uppercase MAC."""
    return state.get_adapter_name(mac_upper)


def refresh_adapter_name_cache() -> None:
    """Reload adapter-name cache from config under the shared adapter cache lock."""
    with state._adapter_cache_lock:
        state.load_adapter_name_cache()
