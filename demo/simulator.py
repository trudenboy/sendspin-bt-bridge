"""Deterministic background simulator for the demo screenshot stand.

The simulator keeps the canonical nine-player layout stable across runs while
still making the UI feel alive:
- advances track progress for the devices that start in a playing state
- rolls over to the next demo track when playback reaches the end
- keeps MA now-playing data in sync for both sync groups and solo players
- slowly drains battery on connected devices without changing role/state
"""

from __future__ import annotations

import asyncio
import logging
import time

from demo.fixtures import DEMO_MA_NOW_PLAYING, DEMO_TRACKS, _ma_now_playing_entry

logger = logging.getLogger(__name__)


async def run_simulator(clients: list) -> None:
    """Long-running async task that mutates client statuses predictably."""
    if not clients:
        return

    def _find_track_index(track: str | None, artist: str | None) -> int:
        for idx, item in enumerate(DEMO_TRACKS):
            if item["title"] == track and item["artist"] == artist:
                return idx
        return 0

    def _queue_key_for_client(client) -> str | None:
        group_id = client.status.get("group_id")
        if group_id:
            return str(group_id)
        player_id = getattr(client, "player_id", "")
        return player_id or getattr(client, "player_name", None) or None

    def _group_clients_by_queue() -> dict[str, list]:
        queue_members: dict[str, list] = {}
        for client in clients:
            queue_key = _queue_key_for_client(client)
            if queue_key:
                queue_members.setdefault(queue_key, []).append(client)
        return queue_members

    def _queue_state_from_members(members: list, fallback: dict[str, int] | None = None) -> dict[str, int]:
        representative = next((client for client in members if client.status.get("playing")), None) or next(
            (
                client
                for client in members
                if client.status.get("bluetooth_connected") or client.status.get("server_connected")
            ),
            members[0] if members else None,
        )
        if representative is None:
            return {"track_idx": 0, "progress_ms": 0}
        track_idx = _find_track_index(
            representative.status.get("current_track"),
            representative.status.get("current_artist"),
        )
        progress_ms = representative.status.get("track_progress_ms")
        if progress_ms is None and fallback is not None:
            progress_ms = fallback.get("progress_ms", 0)
        return {
            "track_idx": track_idx,
            "progress_ms": int(progress_ms or 0),
        }

    def _apply_queue_snapshot(queue_state: dict[str, dict[str, int]], queue_members: dict[str, list]) -> None:
        for queue_id, members in queue_members.items():
            state = queue_state.get(queue_id, {"track_idx": 0, "progress_ms": 0})
            track = DEMO_TRACKS[state["track_idx"] % len(DEMO_TRACKS)]
            queue_playing = any(client.status.get("playing") for client in members)
            for client in members:
                connected = bool(client.status.get("bluetooth_connected") or client.status.get("server_connected"))
                is_buffering = bool(client.status.get("buffering"))
                is_playing = (queue_playing and connected) if not is_buffering else False
                client._update_status(
                    {
                        "current_track": track["title"],
                        "current_artist": track["artist"],
                        "track_duration_ms": track["duration_ms"],
                        "track_progress_ms": state["progress_ms"],
                        "playing": is_playing,
                        "audio_streaming": is_playing and client.status.get("server_connected"),
                    }
                )

    def _sync_ma_now_playing(queue_members: dict[str, list], queue_state: dict[str, dict[str, int]]) -> None:
        """Sync MA now-playing state for sync groups and solo demo queues."""
        import state as _st

        if not _st.is_ma_connected():
            return

        queue_ids = set(DEMO_MA_NOW_PLAYING) | set(queue_members)
        for queue_id in queue_ids:
            template = DEMO_MA_NOW_PLAYING.get(queue_id, {})
            np = dict(_st.get_ma_now_playing_for_group(queue_id) or template)
            members = queue_members.get(queue_id, [])
            state = queue_state.get(queue_id, {"track_idx": 0, "progress_ms": int(float(np.get("elapsed", 0)) * 1000)})
            if not members:
                np.update({"connected": False, "state": "paused", "elapsed_updated_at": time.time()})
                _st.set_ma_now_playing_for_group(queue_id, np)
                continue

            track_idx = state["track_idx"]
            progress_ms = state["progress_ms"]
            queue_playing = any(client.status.get("playing") for client in members)
            queue_buffering = any(client.status.get("buffering") for client in members)
            queue_connected = any(
                client.status.get("bluetooth_connected") or client.status.get("server_connected") for client in members
            )
            np.update(
                _ma_now_playing_entry(
                    queue_id,
                    str(np.get("syncgroup_name") or template.get("syncgroup_name") or queue_id),
                    track_idx,
                    state="playing" if (queue_playing or queue_buffering) else "paused",
                    connected=queue_connected,
                    elapsed_seconds=int(progress_ms / 1000),
                    shuffle=bool(np.get("shuffle", template.get("shuffle", False))),
                    repeat=str(np.get("repeat", template.get("repeat", "off"))),
                )
            )
            np["elapsed_updated_at"] = time.time()
            _st.set_ma_now_playing_for_group(queue_id, np)

    async def _advance_tracks() -> None:
        """Increment track progress for playing devices and rotate tracks deterministically."""
        queue_members = _group_clients_by_queue()
        queue_state: dict[str, dict[str, int]] = {}
        for queue_id, members in queue_members.items():
            state = _queue_state_from_members(members)
            if any(client.status.get("playing") for client in members):
                track = DEMO_TRACKS[state["track_idx"] % len(DEMO_TRACKS)]
                state["progress_ms"] += 5000
                if state["progress_ms"] >= int(track["duration_ms"]):
                    state["track_idx"] = (state["track_idx"] + 1) % len(DEMO_TRACKS)
                    state["progress_ms"] = 0
            queue_state[queue_id] = state

        _apply_queue_snapshot(queue_state, queue_members)
        _sync_ma_now_playing(queue_members, queue_state)

    async def _drain_battery() -> None:
        """Decrease battery by 1% for connected devices."""
        for client in clients:
            if not client.status.get("bluetooth_connected"):
                continue
            level = client.status.get("battery_level")
            if level is not None and level > 5:
                client._update_status({"battery_level": level - 1})

    logger.info("[demo-sim] Canonical simulator started for %d device(s)", len(clients))
    tick = 0
    try:
        while True:
            await asyncio.sleep(5)
            tick += 1
            await _advance_tracks()
            if tick % 60 == 0:
                await _drain_battery()
    except asyncio.CancelledError:
        logger.info("[demo-sim] Simulator stopped")
