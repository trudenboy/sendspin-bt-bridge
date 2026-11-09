"""Optimistic concurrency for player documents and their runtime-owned timing.

The file lock protects one read/write, not a document a browser has held for minutes. A revision
names the player settings that document saw, excluding live caches and read-only annotations.
Older callers may still edit other settings, but cannot replace timing maintained at runtime.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

TIMING_FIELDS = frozenset({"required_lead_time_ms", "min_buffer_ms", "air_to_sound_latency", "air_learned_latency"})


def players_revision(config: dict[str, Any]) -> str:
    players = [
        {key: value for key, value in player.items() if not key.startswith("_")}
        for player in config.get("players") or []
        if isinstance(player, dict)
    ]
    encoded = json.dumps(players, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def keep_player_timing(players: list[dict], stored: list[dict]) -> None:
    """Without a revision, existing players keep their timing as read under the commit lock.

    New players keep their supplied initial values. Player ids have already been resolved by
    the save pipeline (including Bluetooth address matching), so renaming cannot bypass this.
    """
    by_id = {player.get("id"): player for player in stored if isinstance(player, dict) and player.get("id")}
    for player in players:
        previous = by_id.get(player.get("id"))
        if previous is None:
            continue
        for key in set(player) | set(previous):
            if key in TIMING_FIELDS or key.startswith("static_delay_"):
                if key in previous:
                    player[key] = copy.deepcopy(previous[key])
                else:
                    player.pop(key, None)
