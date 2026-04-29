"""Cross-bridge duplicate device detection.

Queries the Music Assistant API to find devices that are already registered
under a different bridge instance, which causes BT disconnect/reconnect loops
when multiple bridge addons share the same host and adapter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sendspin_bridge.config import _player_id_from_mac

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DuplicateDeviceWarning:
    mac: str
    device_name: str
    other_bridge_name: str
    player_id: str


def _normalize_mac(raw: Any) -> str:
    return str(raw or "").strip().upper()


def _is_own_bridge_player(display_name: str, own_bridge_name: str) -> bool:
    """Return True if the MA player display_name belongs to this bridge instance."""
    if not own_bridge_name:
        return False
    # Player names follow the pattern "DeviceName @ BridgeName"
    suffix = f"@ {own_bridge_name}"
    return display_name.rstrip().endswith(suffix)


def find_duplicate_devices(
    config: dict[str, Any],
    bridge_name: str,
) -> list[DuplicateDeviceWarning]:
    """Check MA API for configured devices already registered under another bridge.

    Returns an empty list if ``DUPLICATE_DEVICE_CHECK`` is disabled, MA
    credentials are missing, or the API call fails.
    """
    if not config.get("DUPLICATE_DEVICE_CHECK", True):
        return []

    ma_url = str(config.get("MA_API_URL") or "").strip()
    ma_token = str(config.get("MA_API_TOKEN") or "").strip()
    if not ma_url or not ma_token:
        return []

    devices = config.get("BLUETOOTH_DEVICES") or []
    if not devices:
        return []

    from sendspin_bridge.services.music_assistant.ma_client import fetch_all_players_snapshot

    try:
        players = fetch_all_players_snapshot(ma_url, ma_token)
    except Exception as exc:
        logger.debug("Duplicate device check: MA API unavailable: %s", exc)
        return []

    players_by_id: dict[str, str] = {
        str(p.get("player_id") or "").strip(): str(p.get("display_name") or p.get("name") or "").strip()
        for p in players
        if isinstance(p, dict)
    }

    warnings: list[DuplicateDeviceWarning] = []
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        mac = _normalize_mac(dev.get("mac"))
        if not mac:
            continue
        player_id = _player_id_from_mac(mac)
        existing_name = players_by_id.get(player_id)
        if not existing_name:
            continue
        if _is_own_bridge_player(existing_name, bridge_name):
            continue
        device_name = str(dev.get("name") or mac)
        warnings.append(
            DuplicateDeviceWarning(
                mac=mac,
                device_name=device_name,
                other_bridge_name=existing_name,
                player_id=player_id,
            )
        )

    return warnings


def find_scan_device_conflicts(
    macs: list[str],
    ma_url: str,
    ma_token: str,
    own_bridge_name: str,
) -> dict[str, str]:
    """Return ``{MAC: warning_message}`` for scanned MACs that already exist on another bridge."""
    if not ma_url or not ma_token or not macs:
        return {}

    from sendspin_bridge.services.music_assistant.ma_client import fetch_all_players_snapshot

    try:
        players = fetch_all_players_snapshot(ma_url, ma_token)
    except Exception as exc:
        logger.debug("Scan conflict check: MA API unavailable: %s", exc)
        return {}

    players_by_id: dict[str, str] = {
        str(p.get("player_id") or "").strip(): str(p.get("display_name") or p.get("name") or "").strip()
        for p in players
        if isinstance(p, dict)
    }

    conflicts: dict[str, str] = {}
    for raw_mac in macs:
        mac = _normalize_mac(raw_mac)
        if not mac:
            continue
        player_id = _player_id_from_mac(mac)
        existing_name = players_by_id.get(player_id)
        if not existing_name:
            continue
        if _is_own_bridge_player(existing_name, own_bridge_name):
            continue
        conflicts[mac] = f"Already registered as '{existing_name}' in Music Assistant"

    return conflicts
