"""GroupControl over Music Assistant: members, delays, volume-based solo and the signal path.

Stage 0 (option A, checked on Music Assistant dev 2026-10-16): the click track plays as one
announcement on the group per measurement round. Music Assistant restores music and volumes when
the clip ends; ``players/cmd/stop`` does not end an announcement (it blocks until the clip ends)
and web kiosks refuse ``players/cmd/volume_mute``, while ``players/cmd/volume_set`` 0 silences a
member mid-announcement. So solo and stop work by volume only: solo sets every other member to 0
and the soloed one to its level from the session's snapshot, stop sets every member to 0, and
restore puts the snapshot's volumes back once the clip has ended.

E5 (2026-10-16): a member's Sendspin protocol player keeps a volume of its own. A volume set on a web
kiosk member during an announcement did not always reach its protocol player, and Music Assistant's
end-of-announcement restore never touches it, so solo, stop and restore set both levels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from sendspin_bridge.services.music_assistant.ma_runtime_state import get_ma_groups, get_ma_player_ids
from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_sync.models import Member, Origin, Snapshot
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sendspin_bridge.sync_tuning.ma_api import MaApi

DELAY_KEY = "sendspin_static_delay"
_ANNOUNCING = "announcement_in_progress"  # Music Assistant's ATTR_ANNOUNCEMENT_IN_PROGRESS
SPLIT = "||protocol||"


def _bridge_ids() -> set[str]:
    return set(get_ma_player_ids().values())


def _wrap(exc: MaApiError) -> ControlError:
    return ControlError(exc.code, exc.detail)


class SignalStrategy(Protocol):
    def start(self, group_id: str, url: str) -> None: ...


class AnnouncementSignal:
    """Music Assistant plays the clip as an announcement on the group.

    ``play_announcement`` replies only after the clip has played out, so it is fired,
    never awaited; ``pre_announce`` is always off so no chime shifts the clicks. There is no
    stop: an announcement cannot be ended early, the control silences the members instead.
    """

    def __init__(self, api: MaApi, volume_level: int | None = None) -> None:
        self._api = api
        self._volume = volume_level

    def start(self, group_id: str, url: str) -> None:
        args: dict[str, Any] = {"player_id": group_id, "url": url, "pre_announce": False}
        if self._volume is not None:
            args["volume_level"] = self._volume
        self._api.fire("players/cmd/play_announcement", args)


def _volume(state: dict[str, Any]) -> int | None:
    value = state.get("volume_level")
    return None if value is None else int(value)


def _protocol_ids(members: Sequence[Member]) -> dict[str, str]:
    """Member id -> its separate Sendspin protocol player id (a member that is the Sendspin player
    itself has none)."""
    return {m.ma_player_id: m.client_id for m in members if m.client_id and m.client_id != m.ma_player_id}


class MaGroupControl:
    def __init__(
        self,
        api: MaApi,
        *,
        signal: SignalStrategy,
        groups: Callable[[], list[dict[str, Any]]] = get_ma_groups,
        bridge_player_ids: Callable[[], set[str]] = _bridge_ids,
        owns_api: bool = True,
    ) -> None:
        self._api = api
        self._owns_api = owns_api
        self._signal = signal
        self._groups = groups
        self._bridge_ids = bridge_player_ids
        self._members_cache: dict[str, list[Member]] = {}
        self._levels: dict[str, dict[str, int | None]] = {}  # group -> member volumes at the snapshot
        self._protocol_levels: dict[str, dict[str, int | None]] = {}  # group -> protocol player volumes

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
                    raise _wrap(exc) from exc
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
        ids = [m.ma_player_id for m in members]
        volumes = {i: _volume(states.get(i, {})) for i in ids}
        protocol_volumes = {pid: _volume(states.get(pid, {})) for pid in _protocol_ids(members).values()}
        self._levels[group_id] = dict(volumes)
        self._protocol_levels[group_id] = dict(protocol_volumes)
        return Snapshot(
            group_id=group_id,
            delays={m.ma_player_id: m.delay_ms or 0 for m in members},
            volumes=volumes,
            muted={i: bool(states.get(i, {}).get("volume_muted")) for i in ids},
            playing=states.get(group_id, {}).get("playback_state") == "playing",
            protocol_volumes=protocol_volumes,
        )

    def start_signal(self, group_id: str, url: str) -> None:
        try:
            self._signal.start(group_id, url)
        except MaApiError as exc:
            raise _wrap(exc) from exc

    def _set_volumes(self, targets: dict[str, int]) -> None:
        """Set each member's volume where Music Assistant reports a different one."""
        states = self._states()
        try:
            for pid, level in targets.items():
                if _volume(states.get(pid, {})) != level:
                    self._api.call("players/cmd/volume_set", {"player_id": pid, "volume_level": level})
        except MaApiError as exc:
            raise _wrap(exc) from exc

    def _set_levels(self, members: Sequence[Member], targets: dict[str, int], protocol_levels: dict[str, int]) -> None:
        """Set each member to its target, then (read back) its Sendspin protocol player: to 0 with a
        silenced member, else to *protocol_levels*' level. Both, because during an announcement a
        member's volume does not always reach its protocol player (E5)."""
        self._set_volumes(targets)
        protocols = _protocol_ids(members)
        fix: dict[str, int] = {}
        for mid, level in targets.items():
            proto = protocols.get(mid)
            if proto is None:
                continue
            if level == 0:
                fix[proto] = 0
            elif protocol_levels.get(proto) is not None:
                fix[proto] = protocol_levels[proto]
        if fix:
            self._set_volumes(fix)

    def stop_signal(self, group_id: str) -> None:
        """Silence every member and its Sendspin protocol player (volume 0): the announcement plays on
        until its end, when Music Assistant restores music and the members' volumes.
        ``players/cmd/stop`` is never sent: it would block until the clip ends anyway."""
        states = self._states()
        members = self._cached_members(group_id)
        targets = {m.ma_player_id: 0 for m in members if _volume(states.get(m.ma_player_id, {})) is not None}
        self._set_levels(members, targets, {})

    def solo(self, group_id: str, member: str | None) -> None:
        """Only *member* audible: every other member (and its protocol player) at volume 0 and *member*
        at its levels from the snapshot; ``None`` puts every member back to its levels. Members without
        a volume are left alone."""
        members = self._cached_members(group_id)
        if member is not None and member not in {m.ma_player_id for m in members}:
            raise ControlError("member_gone", f"{member} is not in group {group_id}")
        if group_id not in self._levels:
            self.snapshot(group_id)
        levels = self._levels[group_id]
        protocol_levels = {k: v for k, v in self._protocol_levels.get(group_id, {}).items() if v is not None}
        targets: dict[str, int] = {}
        for m in members:
            level = levels.get(m.ma_player_id)
            if level is not None:
                targets[m.ma_player_id] = level if member is None or m.ma_player_id == member else 0
        self._set_levels(members, targets, protocol_levels)

    def announcement_active(self, group_id: str) -> bool:
        """Whether Music Assistant still plays an announcement on the group or one of its members.

        Music Assistant dev marks it in the player's ``extra_data["announcement_in_progress"]``, which
        is not on the API today; the flag is read wherever a version exposes it (top level or
        ``extra_attributes``). Until then the evidence is ``current_media`` of type ``announcement``
        (reported when no queue item masks it). Only positive evidence counts: ``False`` means
        "not seen", so the session's clock still bounds the clip."""
        states = self._states()
        ids = [group_id, *(m.ma_player_id for m in self._cached_members(group_id))]
        ids += list(_protocol_ids(self._cached_members(group_id)).values())
        for pid in ids:
            st = states.get(pid) or {}
            extra_raw, media_raw = st.get("extra_attributes"), st.get("current_media")
            extra: dict[str, Any] = extra_raw if isinstance(extra_raw, dict) else {}
            media: dict[str, Any] = media_raw if isinstance(media_raw, dict) else {}
            if st.get(_ANNOUNCING) or extra.get(_ANNOUNCING) or media.get("media_type") == "announcement":
                return True
        return False

    def restore(self, snapshot: Snapshot) -> None:
        """Put the snapshot's volumes back where they differ: the members' first, then (read back) their
        Sendspin protocol players', which Music Assistant's own restore does not reach. Delays and mutes
        are never touched (the session never mutes). Runs after the clip ended."""
        self._set_volumes({mid: vol for mid, vol in snapshot.volumes.items() if vol is not None})
        self._set_volumes({pid: vol for pid, vol in snapshot.protocol_volumes.items() if vol is not None})

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

    Contract: one instance per session — the instance caches the session's members and the
    volumes its solo returns to, so it must not be shared between sessions. Wires the stage 0
    choice (option A): announcement signal + volume solo.
    """
    from sendspin_bridge.sync_tuning.ma_api import ma_api_from_config

    api = ma_api_from_config()
    return MaGroupControl(api, signal=AnnouncementSignal(api))


def shared_group_control() -> MaGroupControl:
    """A control for one request outside a session, over the long-lived shared connection.

    Built per request (its member cache and solo levels are not shared); ``close()`` leaves
    the connection open for the next request.
    """
    from sendspin_bridge.sync_tuning import ma_api

    api = ma_api.shared_ma_api()
    return MaGroupControl(api, signal=AnnouncementSignal(api), owns_api=False)
