"""In-memory fakes of the sync core's ports: a group control that records calls and a click-track host."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sendspin_sync.models import GroupPlayback, Member, Snapshot
from sendspin_sync.ports import ControlError

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeControl:
    """A group that plays music until the session stops it; the clicker plays until it ends (``clicker_ends``)."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.delays_now: dict[str, int] = {"a": 0, "b": 0}
        self.fail_unmute = 0  # this many unmute calls fail with ma_unavailable
        self.group_state: str | None = "playing"
        self.media_uri: str | None = "library://track/1"
        self.volumes: dict[str, int | None] = {"a": 40, "b": 40, "cast": 20}
        self.muted_now: dict[str, bool] = {"a": False, "b": False, "cast": False}
        self.no_mute: set[str] = set()  # members whose player cannot be muted
        self.gone: set[str] = set()  # players Music Assistant does not list (e.g. after a restart)
        self.late_mute: set[str] = set()  # their mute takes effect, but the answer times out
        self.fail_clicker = False  # play_media starts the clicker, but the answer times out
        self.fail_volume = 0  # this many volume calls fail with ma_unavailable
        self.member_list = [
            Member("a", "A", True, "bridge", delay_ms=0),
            Member("b", "B", True, "sendspin", delay_ms=0),
            Member("cast", "Cast", False, "other"),
        ]

    def members(self, group_id: str) -> list[Member]:
        return list(self.member_list)

    def delays(self, members: Sequence[Member]) -> dict[str, int | None]:
        return {m.ma_player_id: self.delays_now.get(m.ma_player_id) for m in members}

    def snapshot(self, group_id: str) -> Snapshot:
        return Snapshot(group_id=group_id, delays=dict(self.delays_now), muted=dict(self.muted_now))

    def playback(self, group_id: str) -> GroupPlayback:
        return GroupPlayback(
            state=self.group_state, media_uri=self.media_uri, volumes=dict(self.volumes), muted=dict(self.muted_now)
        )

    def stop_group(self, group_id: str) -> None:
        self.calls.append(("stop_group",))
        self.group_state, self.media_uri = "idle", None

    def start_clicker(self, group_id: str, url: str) -> None:
        self.calls.append(("clicker", url))
        self.group_state, self.media_uri = "playing", f"builtin://sound_effect/{url}"  # as live MA reports it
        if self.fail_clicker:
            raise ControlError("ma_unavailable", "MA play_media timed out")

    def clicker_ends(self) -> None:
        """The click track played to its end (or someone stopped it in Music Assistant)."""
        self.group_state, self.media_uri = "idle", None

    def mute(self, member: str, muted: bool) -> None:
        """Like the adapter: an absent player is ``member_gone``; an unmute is sent only to a player that is
        muted now."""
        if member in self.gone:
            raise ControlError("member_gone", f"{member} is not known", member=member)
        if muted and member in self.no_mute:
            raise ControlError("sync_mute_unsupported", f"{member} cannot be muted", member=member)
        if not muted and not self.muted_now.get(member):
            return
        if not muted and self.fail_unmute:
            self.fail_unmute -= 1
            raise ControlError("ma_unavailable")
        self.calls.append(("mute", member, muted))
        self.muted_now[member] = muted
        if muted and member in self.late_mute:
            raise ControlError("ma_unavailable", "MA volume_mute timed out", member=member)

    def set_volume(self, member: str, level: int) -> None:
        if member in self.gone:
            raise ControlError("member_gone", f"{member} is not known", member=member)
        if self.fail_volume:
            self.fail_volume -= 1
            raise ControlError("ma_unavailable")
        self.calls.append(("volume", member, level))
        self.volumes[member] = level

    def set_delay(self, member: Member, ms: int) -> None:
        self.delays_now[member.ma_player_id] = ms
        self.calls.append(("set", member.ma_player_id, ms))


class FakeHost:
    def __init__(self) -> None:
        self.released: list[str] = []

    def url_for(self, session_id: str, track: str = "measure") -> str:
        name = session_id if track == "measure" else f"{session_id}/{track}"
        return f"http://bridge/api/v1/sync/signal/{name}.wav"

    def release(self, session_id: str) -> None:
        self.released.append(session_id)


GROUPS: list[dict[str, Any]] = [
    {
        "id": "grp",
        "name": "Downstairs",
        "members": [{"id": m, "name": m} for m in ("spk-bridge", "spk-kiosk", "uni", "cast")],
    }
]


class FakeMa:
    """An in-memory Music Assistant API: records every call and models the member-resolution facts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.timeouts: dict[str, float] = {}  # last timeout asked for, per command
        self.forbid_write = False
        # the group's queue: the user's music, positioned on its second item
        self.queue: list[dict[str, Any]] = [
            {"queue_item_id": f"q{i}", "name": f"Song {i}", "media_item": {"uri": f"library://track/{i}"}}
            for i in (1, 2, 3)
        ]
        self.queue_index = 1
        self._next_item = 10
        self.players: dict[str, dict[str, Any]] = {
            "spk-bridge": {
                "player_id": "spk-bridge",
                "provider": "sendspin",
                "type": "player",
                "name": "Kitchen",
                "volume_level": 40,
                "volume_muted": False,
                "supported_features": ["volume_set", "volume_mute"],
                "playback_state": "playing",
                "output_protocols": [],
            },
            "spk-kiosk": {
                # a web kiosk: no mute (stage 0, MA dev 2026-10-16)
                "player_id": "spk-kiosk",
                "provider": "sendspin",
                "type": "player",
                "name": "Kiosk",
                "volume_level": 30,
                "volume_muted": None,
                "supported_features": ["volume_set"],
                "playback_state": "playing",
                "output_protocols": [],
            },
            "uni": {
                "player_id": "uni",
                "provider": "universal_player",
                "type": "player",
                "name": "Hall",
                "volume_level": 50,
                "volume_muted": False,
                "supported_features": ["volume_set", "volume_mute"],
                "playback_state": "playing",
                "output_protocols": [
                    {"output_protocol_id": "uni-sendspin", "protocol_domain": "sendspin", "is_native": False}
                ],
            },
            "uni-sendspin": {
                "player_id": "uni-sendspin",
                "provider": "sendspin",
                "type": "protocol",
                "name": "Hall (Sendspin)",
                "volume_level": 50,
            },
            "cast": {
                "player_id": "cast",
                "provider": "chromecast",
                "type": "player",
                "name": "TV",
                "volume_level": 20,
                "volume_muted": False,
                "supported_features": ["volume_set", "volume_mute"],
                "playback_state": "playing",
                "output_protocols": [],
            },
            "grp": {
                "player_id": "grp",
                "provider": "sync_group",
                "type": "group",
                "name": "Downstairs",
                "playback_state": "playing",
                "current_media": {"uri": "library://track/7", "media_type": "track"},
                "group_members": ["spk-bridge", "spk-kiosk", "uni", "cast"],
            },
        }
        self.entries: dict[str, list[str]] = {
            "spk-bridge": ["sendspin_static_delay"],
            "spk-kiosk": [],
            "uni-sendspin": ["sendspin_static_delay"],
            "cast": [],
        }
        self.values: dict[tuple[str, str], int] = {
            ("spk-bridge", "sendspin_static_delay"): 120,
            ("uni-sendspin", "sendspin_static_delay"): 0,
        }

    def call(self, command: str, args: dict[str, Any], *, timeout: float = 10.0) -> Any:
        from sendspin_bridge.sync_tuning.ma_api import MaApiError

        self.calls.append((command, args))
        self.timeouts[command] = timeout
        if command == "players/all":
            return list(self.players.values())
        if command == "players/get":
            return self.players.get(args["player_id"])
        if command == "config/players/get_entries":
            return [{"key": k} for k in self.entries.get(args["player_id"], [])]
        if command == "config/players/get_value":
            return self.values.get((args["player_id"], args["key"]), args.get("default", 0))
        if command == "config/players/save":
            if self.forbid_write:
                raise MaApiError("ma_forbidden", "scope config.players.write required")
            for key, value in args["values"].items():
                if "||protocol||" in key:
                    pid, _, real = key.partition("||protocol||")
                else:
                    pid, real = args["player_id"], key
                self.values[(pid, real)] = int(value)
            return {}
        if command == "players/cmd/volume_mute":
            player = self.players[args["player_id"]]
            if "volume_mute" not in (player.get("supported_features") or []):
                raise MaApiError("ma_error", f"Player {player['name']} does not support feature volume_mute")
            player["volume_muted"] = args["muted"]
            return None
        if command == "players/cmd/volume_set":
            self.players[args["player_id"]]["volume_level"] = args["volume_level"]
            return None
        if command == "players/cmd/stop":
            player = self.players[args["player_id"]]
            player["playback_state"] = "idle"
            player.pop("current_media", None)
            return None
        if command == "player_queues/play_media":
            # a URL plays as a sound_effect item on the group (stage 0, MA dev 2026-10-16); "play" inserts
            # it after the current item and plays it, keeping the queue; "replace" swaps the whole queue
            group = self.players[args["queue_id"]]
            uri = f"builtin://sound_effect/{args['media']}"
            self._next_item += 1
            item = {"queue_item_id": f"q{self._next_item}", "name": "Sendspin sync clicks", "media_item": {"uri": uri}}
            if args.get("option") == "replace":
                self.queue, self.queue_index = [item], 0
            else:
                self.queue_index += 1
                self.queue.insert(self.queue_index, item)
            group["playback_state"] = "playing"
            group["current_media"] = {"uri": uri, "media_type": "sound_effect"}
            return None
        raise MaApiError("ma_error", f"unknown {command}")
