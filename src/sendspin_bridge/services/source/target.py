"""Play the Bluetooth source on a chosen Music Assistant player or group.

The Sendspin source appears in Music Assistant as a live input.  Its URI is
derived from the source player's own ``source_list``; the fallback shape is
the provider's stable URI template.  The operator can leave the target unset
(the default: choose in Music Assistant) or pin one queue, in which case the
bridge starts the live input there whenever a device begins streaming.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "SOURCE_URI_TEMPLATE",
    "play_source_on",
    "source_uri_from_players",
    "targets_from_players",
]

SOURCE_URI_TEMPLATE = "sendspin_source://audio_source/{client_id}"

_TARGET_TYPES = {"player", "group"}


def source_uri_from_players(players: list[dict[str, Any]], client_id: str | None) -> str:
    """The live-input URI, preferably the one MA itself advertises for this client."""
    for player in players:
        if player.get("type") != "source":
            continue
        if client_id and client_id not in {
            player.get("player_id"),
            player.get("client_id"),
            player.get("source_id"),
        }:
            continue
        for item in player.get("source_list") or ():
            uri = item.get("id") if isinstance(item, dict) else item
            if isinstance(uri, str) and "sendspin_source" in uri:
                return uri
    return SOURCE_URI_TEMPLATE.format(client_id=client_id or "")


def _target_type(player: dict[str, Any]) -> str:
    return "group" if player.get("type") == "group" else "player"


def targets_from_players(players: list[dict[str, Any]], client_id: str | None) -> tuple[list[dict[str, Any]], str]:
    """The MA players/groups that can receive the source, and its live-input URI."""
    targets: list[dict[str, Any]] = []
    for player in players:
        player_type = player.get("type")
        if player_type not in _TARGET_TYPES:
            continue
        player_id = str(player.get("player_id") or "").strip()
        if not player_id:
            continue
        name = str(player.get("display_name") or player.get("name") or player_id).strip()
        targets.append(
            {
                "player_id": player_id,
                "name": name,
                "type": _target_type(player),
                "available": bool(player.get("available", True)),
            }
        )
    targets.sort(key=lambda target: (target["type"] != "group", target["name"].lower(), target["player_id"]))
    return targets, source_uri_from_players(players, client_id)


async def _client(ma_url: str, ma_token: str) -> Any:
    from music_assistant_client import MusicAssistantClient

    url = ma_url.strip()
    if url and "://" not in url:
        url = f"http://{url}"
    client = MusicAssistantClient(url, None, token=ma_token)
    await client.connect()
    return client


async def fetch_targets(ma_url: str, ma_token: str, client_id: str | None) -> tuple[list[dict[str, Any]], str]:
    """Read the live target list and the source URI from Music Assistant."""
    client = await _client(ma_url, ma_token)
    try:
        players = await client.send_command("players/all")
    finally:
        await client.disconnect()
    return targets_from_players(list(players or []), client_id)


async def play_source_on(ma_url: str, ma_token: str, queue_id: str, source_uri: str) -> None:
    """Start (or move) the live input on *queue_id* through MA's queue API."""
    if not queue_id:
        raise ValueError("A target player is required")
    client = await _client(ma_url, ma_token)
    try:
        await client.player_queues.play_media(queue_id, source_uri)
    finally:
        await client.disconnect()
