"""GroupControl over Music Assistant: members, delays, volume-based solo and the signal path.

Stage 0 (option A, checked on Music Assistant dev 2026-10-16): the click track plays as one
announcement on the group per measurement round. Music Assistant restores music and volumes when
the clip ends; ``players/cmd/stop`` does not end an announcement (it blocks until the clip ends)
and web kiosks refuse ``players/cmd/volume_mute``, while ``players/cmd/volume_set`` 0 silences a
member mid-announcement. So solo and stop work by volume only: solo sets every other member to 0
and the soloed one to its level from the session's snapshot, stop sets every member to 0, and
restore puts the snapshot's volumes back once the clip has ended.
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
        self._levels[group_id] = dict(volumes)
        return Snapshot(
            group_id=group_id,
            delays={m.ma_player_id: m.delay_ms or 0 for m in members},
            volumes=volumes,
            muted={i: bool(states.get(i, {}).get("volume_muted")) for i in ids},
            playing=states.get(group_id, {}).get("playback_state") == "playing",
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

    def stop_signal(self, group_id: str) -> None:
        """Silence every member (volume 0): the announcement plays on until its end, when Music
        Assistant restores music and volumes. ``players/cmd/stop`` is never sent: it would block
        until the clip ends anyway."""
        states = self._states()
        members = self._cached_members(group_id)
        self._set_volumes({m.ma_player_id: 0 for m in members if _volume(states.get(m.ma_player_id, {})) is not None})

    def solo(self, group_id: str, member: str | None) -> None:
        """Only *member* audible: every other member at volume 0 and *member* at its level from the
        snapshot; ``None`` puts every member back to its level. Members without a volume are left alone."""
        members = self._cached_members(group_id)
        if member is not None and member not in {m.ma_player_id for m in members}:
            raise ControlError("member_gone", f"{member} is not in group {group_id}")
        levels = self._levels.get(group_id)
        if levels is None:
            levels = self.snapshot(group_id).volumes
        targets: dict[str, int] = {}
        for m in members:
            level = levels.get(m.ma_player_id)
            if level is not None:
                targets[m.ma_player_id] = level if member is None or m.ma_player_id == member else 0
        self._set_volumes(targets)

    def restore(self, snapshot: Snapshot) -> None:
        """Put the snapshot's volumes back where they differ; delays and mutes are never touched
        (the session never mutes). Runs after the clip ended, so Music Assistant's own restore is done."""
        self._set_volumes({mid: vol for mid, vol in snapshot.volumes.items() if vol is not None})

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
