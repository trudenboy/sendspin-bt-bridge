"""Music Assistant API helpers for group discovery and playback control."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def _norm(s: str) -> str:
    """Strip non-alphanumeric chars for fuzzy name matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


async def _normalize_ma_url(url: str) -> str:
    """Ensure MA URL has http:// scheme."""
    url = url.strip()
    if url and "://" not in url:
        url = f"http://{url}"
    return url


async def _fetch_all_players(ma_url: str, ma_token: str) -> list[dict]:
    """Connect to MA API and return the full players/all list."""
    from music_assistant_client import MusicAssistantClient

    async with MusicAssistantClient(await _normalize_ma_url(ma_url), None, token=ma_token) as client:
        await client.connect()
        return await client.send_command("players/all")


async def discover_ma_groups(ma_url: str, ma_token: str, bridge_player_names: list[str]) -> tuple[dict, list[dict]]:
    """Query MA API and return two things:

    1. name_map: dict player_name_lower → {"id", "name"} for bridge players that belong to a syncgroup.
    2. all_groups: list of all MA syncgroup dicts {"id", "name", "members": [{"id", "name"}]}

    Both are derived from a single players/all call.
    """
    try:
        import music_assistant_client as _mac  # noqa: F401
    except ImportError:
        logger.warning("music-assistant-client not installed — MA API group discovery disabled")
        return {}, []

    try:
        players = await _fetch_all_players(ma_url, ma_token)
    except Exception as exc:
        logger.warning("MA API players/all failed: %s", exc)
        return {}, []

    # Build MA player_id → display name map
    id_to_name: dict[str, str] = {p["player_id"]: (p.get("display_name") or p.get("name") or "") for p in players}

    all_groups: list[dict] = []
    name_map: dict[str, dict] = {}

    for p in players:
        if p.get("type") != "group" or p.get("provider") != "sync_group":
            continue

        syncgroup_id = p["player_id"]
        syncgroup_name = p.get("display_name") or p.get("name") or syncgroup_id
        raw_members = p.get("group_members") or []
        # Build rich member info from the full player list
        member_by_id = {pl["player_id"]: pl for pl in players}
        members = [
            {
                "id": mid,
                "name": id_to_name.get(mid, mid),
                "state": member_by_id.get(mid, {}).get("playback_state"),
                "volume": member_by_id.get(mid, {}).get("volume_level"),
                "available": member_by_id.get(mid, {}).get("available", True),
            }
            for mid in raw_members
        ]
        member_names_lower = {id_to_name.get(mid, "").lower() for mid in raw_members}

        all_groups.append({"id": syncgroup_id, "name": syncgroup_name, "members": members})

        # Match bridge players → this syncgroup by name (case-insensitive, ignoring punctuation)
        for bridge_name in bridge_player_names:
            b = bridge_name.lower()
            b_norm = _norm(bridge_name)
            if any(b in mn or mn in b or _norm(mn) in b_norm or b_norm in _norm(mn) for mn in member_names_lower if mn):
                name_map[b] = {"id": syncgroup_id, "name": syncgroup_name}
                logger.debug(
                    "Mapped bridge player '%s' → MA syncgroup '%s' (%s)",
                    bridge_name,
                    syncgroup_name,
                    syncgroup_id,
                )

    logger.info(
        "MA API: found %d syncgroup(s), matched %d bridge player(s)",
        len(all_groups),
        len(name_map),
    )
    return name_map, all_groups


async def ma_group_play(ma_url: str, ma_token: str, syncgroup_id: str) -> bool:
    """Send play command to a MA persistent syncgroup player. Returns True on success."""
    try:
        from music_assistant_client import MusicAssistantClient
    except ImportError:
        logger.warning("music-assistant-client not installed — MA API play disabled")
        return False

    try:
        async with MusicAssistantClient(await _normalize_ma_url(ma_url), None, token=ma_token) as client:
            await client.connect()
            await client.players.play(syncgroup_id)
        logger.info("MA group play → %s", syncgroup_id)
        return True
    except Exception as exc:
        logger.warning("MA group play failed for %s: %s", syncgroup_id, exc)
        return False
