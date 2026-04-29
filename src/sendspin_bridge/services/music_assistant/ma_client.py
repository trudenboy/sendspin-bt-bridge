"""Music Assistant API helpers for group discovery and playback control."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def fetch_all_players_snapshot(ma_url: str, ma_token: str) -> list[dict]:
    """Return the current MA players/all payload from sync Flask code.

    Config validation runs in regular Flask request threads, so expose a tiny
    synchronous wrapper instead of making routes manage their own event loops.
    """

    return asyncio.run(_fetch_all_players(ma_url, ma_token))


async def discover_ma_groups(
    ma_url: str,
    ma_token: str,
    bridge_players: Sequence[dict | str],
) -> tuple[dict, list[dict]]:
    """Query MA API and return two things:

    1. id_map: dict player_id → {"id", "name"} for bridge players that belong to a syncgroup.
    2. all_groups: list of all MA syncgroup dicts {"id", "name", "members": [{"id", "name"}]}

    ``bridge_players`` accepts either a list of ``{"player_id", "player_name"}``
    dicts (preferred) or plain name strings (legacy/demo fallback).
    Matching uses player_id (exact, stable) with fuzzy name as fallback.
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

    # Normalise legacy callers that pass plain strings
    bp_list: list[dict] = []
    for item in bridge_players:
        if isinstance(item, str):
            bp_list.append({"player_id": "", "player_name": item})
        else:
            bp_list.append(item)

    # Build MA player_id → display name map
    id_to_name: dict[str, str] = {p["player_id"]: (p.get("display_name") or p.get("name") or "") for p in players}

    all_groups: list[dict] = []
    id_map: dict[str, dict] = {}

    member_set_by_group: dict[str, set[str]] = {}  # syncgroup_id → set of member player_ids

    for p in players:
        if p.get("type") != "group" or p.get("provider") != "sync_group":
            continue

        syncgroup_id = p["player_id"]
        syncgroup_name = p.get("display_name") or p.get("name") or syncgroup_id
        raw_members = p.get("group_members") or []
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
        member_ids = set(raw_members)
        member_set_by_group[syncgroup_id] = member_ids

        all_groups.append({"id": syncgroup_id, "name": syncgroup_name, "members": members})

        sg_info = {"id": syncgroup_id, "name": syncgroup_name}

        # Primary: match by player_id (exact, stable)
        for bp in bp_list:
            pid = bp.get("player_id", "")
            if pid and pid in member_ids and pid not in id_map:
                id_map[pid] = sg_info
                logger.debug(
                    "Mapped bridge player_id '%s' (%s) → MA syncgroup '%s'",
                    pid,
                    bp.get("player_name", ""),
                    syncgroup_name,
                )

    # Fallback: fuzzy name matching for players not yet matched by ID
    member_names_lower_by_group: dict[str, set[str]] = {}
    for sg_id, mids in member_set_by_group.items():
        member_names_lower_by_group[sg_id] = {id_to_name.get(mid, "").lower() for mid in mids}

    for bp in bp_list:
        pid = bp.get("player_id", "")
        if pid and pid in id_map:
            continue
        bname = bp.get("player_name", "")
        if not bname:
            continue
        b = bname.lower()
        b_norm = _norm(bname)
        for g in all_groups:
            mn_set = member_names_lower_by_group.get(g["id"], set())
            if any(b in mn or mn in b or _norm(mn) in b_norm or b_norm in _norm(mn) for mn in mn_set if mn):
                key = pid if pid else b
                id_map[key] = {"id": g["id"], "name": g["name"]}
                logger.debug(
                    "Mapped bridge player '%s' → MA syncgroup '%s' (name fallback)",
                    bname,
                    g["name"],
                )
                break

    logger.info(
        "MA API: found %d syncgroup(s), matched %d bridge player(s)",
        len(all_groups),
        len(id_map),
    )
    return id_map, all_groups


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
