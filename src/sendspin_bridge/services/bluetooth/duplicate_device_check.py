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


def _player_display_name(player: dict[str, Any]) -> str:
    return str(player.get("display_name") or player.get("name") or "").strip()


def _output_protocol_ids(player: dict[str, Any]) -> set[str]:
    protocols = player.get("output_protocols") or []
    if not isinstance(protocols, list):
        return set()
    return {
        str(entry.get("output_protocol_id") or "").strip()
        for entry in protocols
        if isinstance(entry, dict) and entry.get("output_protocol_id")
    }


def _matching_ma_name(
    players: list[Any],
    *,
    mac: str,
    device_name: str,
    own_bridge_name: str,
) -> str | None:
    """Return the MA display name if this speaker is already on another bridge."""
    legacy_id = _player_id_from_mac(mac)
    name_prefix = f"{device_name} @ " if device_name else ""
    for player in players:
        if not isinstance(player, dict):
            continue
        display = _player_display_name(player)
        player_id = str(player.get("player_id") or "").strip()
        matched = player_id == legacy_id or legacy_id in _output_protocol_ids(player)
        if not matched and name_prefix and display.startswith(name_prefix):
            matched = True
        if not matched or not display:
            continue
        if _is_own_bridge_player(display, own_bridge_name):
            continue
        return display
    return None


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

    warnings: list[DuplicateDeviceWarning] = []
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        mac = _normalize_mac(dev.get("mac"))
        if not mac:
            continue
        device_name = str(dev.get("name") or mac)
        existing_name = _matching_ma_name(
            players, mac=mac, device_name=device_name, own_bridge_name=bridge_name
        )
        if not existing_name:
            continue
        warnings.append(
            DuplicateDeviceWarning(
                mac=mac,
                device_name=device_name,
                other_bridge_name=existing_name,
                player_id=_player_id_from_mac(mac),
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

    conflicts: dict[str, str] = {}
    for raw_mac in macs:
        mac = _normalize_mac(raw_mac)
        if not mac:
            continue
        existing_name = _matching_ma_name(
            players, mac=mac, device_name="", own_bridge_name=own_bridge_name
        )
        if not existing_name:
            continue
        conflicts[mac] = f"Already registered as '{existing_name}' in Music Assistant"

    return conflicts
