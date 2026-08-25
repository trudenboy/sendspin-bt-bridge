"""Which Music Assistant player fronts each of our speakers.

Music Assistant wraps every output in a player of its own and gives it an id
we do not choose.  The bridge used to derive that id from its own client id —
`up` + the id with the dashes stripped — which was the numbering an older
server used.  Against a current one, every queue command aimed at an
ungrouped speaker addressed a queue that does not exist, and no queue state
ever came back for it.

The link is published rather than derivable: the player that fronts our
speaker lists our client id among its output protocols.  This module reads it
out of a ``players/all`` payload; nothing here guesses.
"""

from __future__ import annotations

from typing import Any

__all__ = ["learn_ma_player_ids"]


def _display_name(player: dict[str, Any]) -> str:
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


def learn_ma_player_ids(
    players: list[dict[str, Any]],
    bridge_players: list[dict[str, Any]],
) -> dict[str, str]:
    """Map ``{bridge client id → MA player id}`` for the clients MA knows.

    A client id carried as one of a player's output protocols is proof; an
    exact display-name match stands in for servers that publish no protocols.
    A name that merely resembles ours is not a match — two bridges serving the
    same speaker differ only by the suffix on that name.
    """
    by_protocol: dict[str, str] = {}
    by_exact_name: dict[str, str] = {}
    for player in players or []:
        player_id = str(player.get("player_id") or "").strip()
        if not player_id:
            continue
        for protocol_id in _output_protocol_ids(player):
            by_protocol.setdefault(protocol_id, player_id)
        name = _display_name(player)
        if name:
            by_exact_name.setdefault(name, player_id)

    mapping: dict[str, str] = {}
    for bridge_player in bridge_players or []:
        client_id = str(bridge_player.get("player_id") or "").strip()
        if not client_id:
            continue
        resolved = by_protocol.get(client_id) or by_exact_name.get(str(bridge_player.get("player_name") or "").strip())
        if resolved:
            mapping[client_id] = resolved
    return mapping
