"""Deterministic background simulator for the demo screenshot stand.

The simulator keeps the canonical six-player layout stable across runs while
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
        return player_id or None

    _track_index: dict[str, int] = {
        client.player_name: _find_track_index(
            client.status.get("current_track"),
            client.status.get("current_artist"),
        )
        for client in clients
    }

    def _sync_ma_now_playing() -> None:
        """Sync MA now-playing state for sync groups and solo demo queues."""
        import state as _st

        if not _st.is_ma_connected():
            return

        queue_members: dict[str, list] = {}
        for client in clients:
            queue_key = _queue_key_for_client(client)
            if queue_key:
                queue_members.setdefault(queue_key, []).append(client)

        for queue_id, template in DEMO_MA_NOW_PLAYING.items():
            np = dict(_st.get_ma_now_playing_for_group(queue_id) or template)
            members = queue_members.get(queue_id, [])
            playing_member = next((client for client in members if client.status.get("playing")), None)
            representative = playing_member or next(
                (
                    client
                    for client in members
                    if client.status.get("bluetooth_connected") or client.status.get("server_connected")
                ),
                None,
            )

            if representative is None:
                np.update({"connected": False, "state": "paused", "elapsed_updated_at": time.time()})
                _st.set_ma_now_playing_for_group(queue_id, np)
                continue

            track_idx = _track_index.get(
                representative.player_name,
                _find_track_index(
                    representative.status.get("current_track"),
                    representative.status.get("current_artist"),
                ),
            )
            progress_ms = representative.status.get("track_progress_ms") or int(float(np.get("elapsed", 0)) * 1000)
            np.update(
                _ma_now_playing_entry(
                    queue_id,
                    str(np.get("syncgroup_name") or template.get("syncgroup_name") or queue_id),
                    track_idx,
                    state="playing" if representative.status.get("playing") else "paused",
                    connected=bool(
                        representative.status.get("bluetooth_connected")
                        or representative.status.get("server_connected")
                    ),
                    elapsed_seconds=int(progress_ms / 1000),
                    shuffle=bool(np.get("shuffle", template.get("shuffle", False))),
                    repeat=str(np.get("repeat", template.get("repeat", "off"))),
                )
            )
            np["elapsed_updated_at"] = time.time()
            _st.set_ma_now_playing_for_group(queue_id, np)

    async def _advance_tracks() -> None:
        """Increment track progress for playing devices and rotate tracks deterministically."""
        for client in clients:
            if not client.status.get("playing"):
                continue

            progress = client.status.get("track_progress_ms") or 0
            track_idx = _track_index.get(
                client.player_name,
                _find_track_index(client.status.get("current_track"), client.status.get("current_artist")),
            )
            track = DEMO_TRACKS[track_idx]
            duration = int(track["duration_ms"])
            progress += 5000

            if progress >= duration:
                track_idx = (track_idx + 1) % len(DEMO_TRACKS)
                _track_index[client.player_name] = track_idx
                next_track = DEMO_TRACKS[track_idx]
                client._update_status(
                    {
                        "current_track": next_track["title"],
                        "current_artist": next_track["artist"],
                        "track_duration_ms": next_track["duration_ms"],
                        "track_progress_ms": 0,
                    }
                )
            else:
                client._update_status({"track_progress_ms": progress})

        _sync_ma_now_playing()

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
