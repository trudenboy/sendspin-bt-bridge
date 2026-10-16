"""GroupControl over Music Assistant: members, delays, the group's playback, the clicker and mutes.

Option C (user decision 2026-10-16, checked on Music Assistant dev): no music plays during a
session. ``players/cmd/stop`` on the group stops it at once; the click track is queued on the group
with ``player_queues/play_media`` (option ``replace``) and plays as a normal item on every member;
solo mutes the other members with ``players/cmd/volume_mute``. A player that cannot be muted (web
kiosks today) is named in a ``sync_mute_unsupported`` error: there is no volume fallback.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_groups, get_ma_player_ids
from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_sync.models import GroupPlayback, Member, Origin, Snapshot
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sendspin_bridge.sync_tuning.ma_api import MaApi

logger = logging.getLogger(__name__)

DELAY_KEY = "sendspin_static_delay"
SPLIT = "||protocol||"
MUTE_FEATURE = "volume_mute"
# play_media answers once Music Assistant loaded the item and started it
CLICKER_START_TIMEOUT_S = 30.0


def _bridge_ids() -> set[str]:
    return set(get_ma_player_ids().values())


def _wrap(exc: MaApiError) -> ControlError:
    return ControlError(exc.code, exc.detail)


def _volume(state: dict[str, Any]) -> int | None:
    value = state.get("volume_level")
    return None if value is None else int(value)


def _can_mute(state: dict[str, Any]) -> bool:
    """Music Assistant lists ``volume_mute`` in ``supported_features``; without the list, a player that
    reports a mute state is tried (and a refusal still names it)."""
    features = state.get("supported_features")
    if isinstance(features, list):
        return MUTE_FEATURE in features
    return state.get("volume_muted") is not None


class MaGroupControl:
    def __init__(
        self,
        api: MaApi,
        *,
        groups: Callable[[], list[dict[str, Any]]] = get_ma_groups,
        bridge_player_ids: Callable[[], set[str]] = _bridge_ids,
        owns_api: bool = True,
    ) -> None:
        self._api = api
        self._owns_api = owns_api
        self._groups = groups
        self._bridge_ids = bridge_player_ids
        self._members_cache: dict[str, list[Member]] = {}

    def _group(self, group_id: str) -> dict[str, Any]:
        for g in self._groups():
            if g.get("id") == group_id:
                return g
        raise ControlError("member_gone", f"group {group_id} is not known")

    def _states(self) -> dict[str, dict[str, Any]]:
        try:
            players = self._api.call("players/all", {"return_protocol_players": True}) or []
        except MaApiError as exc:
            raise _wrap(exc) from exc
        return {str(p["player_id"]): p for p in players if isinstance(p, dict) and p.get("player_id")}

    def _read_delay(self, client_id: str) -> int:
        value = self._api.call("config/players/get_value", {"player_id": client_id, "key": DELAY_KEY, "default": 0})
        return int(value or 0)

    def members(self, group_id: str) -> list[Member]:
        group, states, bridge_ids = self._group(group_id), self._states(), self._bridge_ids()
        out: list[Member] = []
        for entry in group.get("members") or []:
            mid = str(entry.get("id"))
            st = states.get(mid, {})
            name = st.get("name") or entry.get("name") or mid
            client_id: str | None = None
            if st.get("provider") == "sendspin":
                client_id = mid
            else:
                for proto in st.get("output_protocols") or []:
                    if proto.get("protocol_domain") == "sendspin" and proto.get("output_protocol_id"):
                        client_id = str(proto["output_protocol_id"])
                        break
            origin: Origin = "other" if client_id is None else ("bridge" if mid in bridge_ids else "sendspin")
            adjustable, delay = False, None
            if client_id is not None:
                try:
                    entries = self._api.call("config/players/get_entries", {"player_id": client_id}) or []
                    adjustable = any(isinstance(e, dict) and e.get("key") == DELAY_KEY for e in entries)
                    if adjustable:
                        delay = self._read_delay(client_id)
                except MaApiError as exc:
                    if exc.code != "ma_error":  # unreachable or forbidden: the whole group is unknown
                        raise _wrap(exc) from exc
                    # its Sendspin player left Music Assistant (a closed kiosk page): listed, not adjustable
                    logger.warning("Sync: %s has no reachable Sendspin player in Music Assistant: %s", name, exc)
                    adjustable, delay = False, None
            out.append(
                Member(
                    ma_player_id=mid,
                    name=str(name),
                    adjustable=adjustable,
                    origin=origin,
                    client_id=client_id,
                    delay_ms=delay,
                )
            )
        self._members_cache[group_id] = out
        return out

    def _cached_members(self, group_id: str) -> list[Member]:
        return self._members_cache.get(group_id) or self.members(group_id)

    def delays(self, members: Sequence[Member]) -> dict[str, int | None]:
        """Current values read from Music Assistant; ``None`` for members that have no delay."""
        try:
            return {
                m.ma_player_id: (self._read_delay(m.client_id) if m.adjustable and m.client_id else None)
                for m in members
            }
        except MaApiError as exc:
            raise _wrap(exc) from exc

    def set_delay(self, member: Member, ms: int) -> None:
        if not member.adjustable or member.client_id is None:
            raise ControlError("not_adjustable", member.name)
        key = DELAY_KEY if member.client_id == member.ma_player_id else f"{member.client_id}{SPLIT}{DELAY_KEY}"
        try:
            self._api.call("config/players/save", {"player_id": member.ma_player_id, "values": {key: int(ms)}})
        except MaApiError as exc:
            raise _wrap(exc) from exc

    def snapshot(self, group_id: str) -> Snapshot:
        states = self._states()
        members = self._cached_members(group_id)
        return Snapshot(
            group_id=group_id,
            delays={m.ma_player_id: m.delay_ms or 0 for m in members},
            muted={m.ma_player_id: bool(states.get(m.ma_player_id, {}).get("volume_muted")) for m in members},
        )

    def playback(self, group_id: str) -> GroupPlayback:
        """The group's playback state, its current media and every member's volume."""
        states = self._states()
        group = states.get(group_id)
        if group is None:
            raise ControlError("member_gone", f"group {group_id} is not known")
        media = group.get("current_media")
        uri = media.get("uri") if isinstance(media, dict) else None
        ids = [m.ma_player_id for m in self._cached_members(group_id)]
        return GroupPlayback(
            state=None if group.get("playback_state") is None else str(group["playback_state"]),
            media_uri=None if uri is None else str(uri),
            volumes={i: _volume(states.get(i, {})) for i in ids},
        )

    def stop_group(self, group_id: str) -> None:
        try:
            self._api.call("players/cmd/stop", {"player_id": group_id})
        except MaApiError as exc:
            raise _wrap(exc) from exc

    def start_clicker(self, group_id: str, url: str) -> None:
        """Queue the click track on the group in place of what it holds; it plays as a normal item."""
        try:
            self._api.call(
                "player_queues/play_media",
                {"queue_id": group_id, "media": url, "option": "replace"},
                timeout=CLICKER_START_TIMEOUT_S,
            )
        except MaApiError as exc:
            raise _wrap(exc) from exc

    def mute(self, member: str, muted: bool) -> None:
        """Mute or unmute one player. One that cannot be muted (or whose mute Music Assistant refuses)
        raises ``sync_mute_unsupported`` naming it; unmuting a player that cannot be muted, or that
        left Music Assistant, is a no-op (the session never muted it)."""
        state = self._states().get(member)
        if state is None:
            if muted:
                raise ControlError("member_gone", f"{member} is not known", member=member)
            return
        name = str(state.get("name") or member)
        if not _can_mute(state):
            if muted:
                raise ControlError("sync_mute_unsupported", f"{name} cannot be muted", member=member)
            return
        try:
            self._api.call("players/cmd/volume_mute", {"player_id": member, "muted": muted})
        except MaApiError as exc:
            if exc.code == "ma_error" and muted:
                raise ControlError(
                    "sync_mute_unsupported", f"{name} cannot be muted: {exc.detail}", member=member
                ) from exc
            raise ControlError(exc.code, f"{name}: {exc.detail}", member=member) from exc

    def set_volume(self, member: str, level: int) -> None:
        try:
            self._api.call("players/cmd/volume_set", {"player_id": member, "volume_level": int(level)})
        except MaApiError as exc:
            raise _wrap(exc) from exc

    def close(self) -> None:
        """Release the Music Assistant connection this control owns (the session store calls it);
        a control over the shared connection leaves it open."""
        if not self._owns_api:
            return
        close = getattr(self._api, "close", None)
        if callable(close):
            close()


def default_group_control() -> MaGroupControl:
    """Build a fresh control for one tuning session.

    Contract: one instance per session — the instance caches the session's members, so it must
    not be shared between sessions.
    """
    from sendspin_bridge.sync_tuning.ma_api import ma_api_from_config

    return MaGroupControl(ma_api_from_config())


def shared_group_control() -> MaGroupControl:
    """A control for one request outside a session, over the long-lived shared connection.

    Built per request (its member cache is not shared); ``close()`` leaves the connection open
    for the next request.
    """
    from sendspin_bridge.sync_tuning import ma_api

    return MaGroupControl(ma_api.shared_ma_api(), owns_api=False)
