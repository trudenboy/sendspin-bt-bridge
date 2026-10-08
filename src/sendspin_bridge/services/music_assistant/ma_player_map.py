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

__all__ = ["learn_ma_player_ids", "plan_group_identity_migrations"]


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
        # aiosendspin 9 says hello with the daemon's identity key, so that —
        # not the bridge's player id — is the output protocol MA lists.
        advertised = str(bridge_player.get("client_id") or "").strip()
        resolved = (
            (by_protocol.get(advertised) if advertised else None)
            or by_protocol.get(client_id)
            or by_exact_name.get(str(bridge_player.get("player_name") or "").strip())
        )
        if resolved:
            mapping[client_id] = resolved
    return mapping


def _legacy_ids(players: list[dict[str, Any]], client_id: str) -> set[str]:
    """Every id Music Assistant may have filed the speaker under before 2.76.

    The protocol player carried the MAC-derived client id itself; its wrapper
    was "up" + that id without dashes, or whichever player lists it as an
    output protocol.
    """
    ids = {client_id, "up" + client_id.replace("-", "")}
    for player in players or []:
        if client_id in _output_protocol_ids(player):
            ids.add(str(player.get("player_id") or ""))
    ids.discard("")
    return ids


def plan_group_identity_migrations(
    players: list[dict[str, Any]],
    bridge_players: list[dict[str, Any]],
    learned: dict[str, str],
) -> dict[str, list[str]]:
    """Sync groups to rewrite so they follow our speakers to their new id.

    Since 2.76 the daemon says hello with its identity key, so Music
    Assistant files each speaker under a new player and the sync groups still
    list the old one, which never comes back. Returns ``{group id: new static
    members}`` for the groups that need it.

    A legacy player that is still available is left alone: it is another
    bridge, still on the old version, serving the same speaker.
    """
    by_id = {str(p.get("player_id") or ""): p for p in players or []}
    plans: dict[str, list[str]] = {}
    for bridge_player in bridge_players or []:
        client_id = str(bridge_player.get("player_id") or "").strip()
        new_id = learned.get(client_id)
        if not client_id or not new_id:
            continue
        legacy = _legacy_ids(players, client_id) - {new_id}
        if any(by_id.get(old, {}).get("available") for old in legacy):
            continue
        for player in players or []:
            if player.get("provider") != "sync_group":
                continue
            group_id = str(player.get("player_id") or "")
            members = plans.get(group_id) or list(player.get("static_group_members") or [])
            if new_id in members or not legacy.intersection(members):
                continue
            moved: list[str] = []
            for member in members:
                replacement = new_id if member in legacy else member
                if replacement not in moved:
                    moved.append(replacement)
            plans[group_id] = moved
    return plans
