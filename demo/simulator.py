"""Background state simulator for demo mode.

Periodically changes device states to make the demo feel alive:
- Toggles play/idle on random devices
- Simulates disconnect/reconnect cycles
- Advances track progress while playing
- Drains battery for connected devices
- Cycles through fake tracks
- Updates MA now-playing state in sync with device state
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone

from demo.fixtures import DEMO_MA_NOW_PLAYING, DEMO_TRACKS

logger = logging.getLogger(__name__)

UTC = timezone.utc


async def run_simulator(clients: list) -> None:
    """Long-running async task that mutates client statuses for realism."""
    if not clients:
        return

    _track_index: dict[str, int] = {}  # player_name → current track index

    def _sync_ma_now_playing() -> None:
        """Sync MA now-playing state per syncgroup from its member devices."""
        import state as _st

        if not _st.is_ma_connected():
            return

        # Build a map: group_id → list of clients in that group
        groups: dict[str, list] = {}
        for c in clients:
            gid = c.status.get("group_id")
            if gid:
                groups.setdefault(gid, []).append(c)

        for sg_id in DEMO_MA_NOW_PLAYING:
            np = _st.get_ma_now_playing_for_group(sg_id) or {}
            members = groups.get(sg_id, [])
            playing_member = next((c for c in members if c.status.get("playing")), None)

            if playing_member:
                prog_ms = playing_member.status.get("track_progress_ms", 0) or 0
                dur_ms = playing_member.status.get("track_duration_ms", 180_000) or 180_000
                np.update(
                    {
                        "connected": True,
                        "state": "playing",
                        "track": playing_member.status.get("current_track", ""),
                        "artist": playing_member.status.get("current_artist", ""),
                        "album": "Demo Album",
                        "elapsed": prog_ms / 1000,
                        "elapsed_updated_at": time.time(),
                        "duration": dur_ms / 1000,
                        "queue_index": _track_index.get(playing_member.player_name, 0),
                        "queue_total": len(DEMO_TRACKS),
                    }
                )
            else:
                np["state"] = "paused"
                np["elapsed_updated_at"] = time.time()

            _st.set_ma_now_playing_for_group(sg_id, np)

    async def _advance_tracks() -> None:
        """Increment track progress for playing devices, switch tracks when done."""
        for c in clients:
            if not c.status.get("playing"):
                continue
            progress = c.status.get("track_progress_ms") or 0
            duration = c.status.get("track_duration_ms") or 180_000
            progress += 5000  # 5 seconds per tick

            if progress >= duration:
                # Next track
                idx = _track_index.get(c.player_name, 0)
                idx = (idx + 1) % len(DEMO_TRACKS)
                _track_index[c.player_name] = idx
                title, artist, dur = DEMO_TRACKS[idx]
                c._update_status(
                    {
                        "current_track": title,
                        "current_artist": artist,
                        "track_duration_ms": dur,
                        "track_progress_ms": 0,
                    }
                )
            else:
                c._update_status({"track_progress_ms": progress})

        _sync_ma_now_playing()

    async def _drain_battery() -> None:
        """Decrease battery by 1% for connected devices."""
        for c in clients:
            if not c.status.get("bluetooth_connected"):
                continue
            level = c.status.get("battery_level")
            if level is not None and level > 5:
                c._update_status({"battery_level": level - 1})

    async def _random_play_toggle() -> None:
        """Randomly toggle play/idle on one connected device."""
        connected = [c for c in clients if c.status.get("server_connected")]
        if not connected:
            return
        target = random.choice(connected)
        is_playing = target.status.get("playing", False)
        if is_playing:
            target._update_status({"playing": False, "audio_streaming": False})
            logger.debug("[demo-sim] %s → idle", target.player_name)
        else:
            idx = _track_index.get(target.player_name, random.randint(0, len(DEMO_TRACKS) - 1))
            _track_index[target.player_name] = idx
            title, artist, dur = DEMO_TRACKS[idx]
            target._update_status(
                {
                    "playing": True,
                    "audio_streaming": True,
                    "current_track": title,
                    "current_artist": artist,
                    "track_duration_ms": dur,
                    "track_progress_ms": 0,
                    "audio_format": "FLAC 44100Hz 16bit 2ch",
                }
            )
            logger.debug("[demo-sim] %s → playing '%s'", target.player_name, title)
        _sync_ma_now_playing()

    async def _random_disconnect_cycle() -> None:
        """Simulate a disconnect→reconnect on one device."""
        connected = [c for c in clients if c.status.get("bluetooth_connected")]
        if not connected:
            return
        target = random.choice(connected)
        logger.debug("[demo-sim] %s → simulating disconnect", target.player_name)
        target._update_status(
            {
                "bluetooth_connected": False,
                "server_connected": False,
                "playing": False,
                "audio_streaming": False,
                "battery_level": None,
                "reconnecting": True,
            }
        )
        await asyncio.sleep(random.uniform(3.0, 6.0))
        target._update_status(
            {
                "bluetooth_connected": True,
                "server_connected": True,
                "reconnecting": False,
                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                "battery_level": random.randint(20, 95),
            }
        )
        logger.debug("[demo-sim] %s → reconnected", target.player_name)

    logger.info("[demo-sim] Background simulator started for %d device(s)", len(clients))
    tick = 0
    try:
        while True:
            await asyncio.sleep(5)
            tick += 1

            # Every 5s: advance track progress
            await _advance_tracks()

            # Every ~45s: random play/idle toggle
            if tick % 9 == 0:
                await _random_play_toggle()

            # Every ~5 min: battery drain
            if tick % 60 == 0:
                await _drain_battery()

            # Every ~3 min: disconnect/reconnect cycle
            if tick % 36 == 0 and tick > 36:
                await _random_disconnect_cycle()

    except asyncio.CancelledError:
        logger.info("[demo-sim] Simulator stopped")
