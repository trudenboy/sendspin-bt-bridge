"""
Music Assistant WebSocket monitor.

Maintains a persistent WS connection to the MA server, subscribes to
player_queue_updated and player_updated events, and updates the now-playing
cache in state.py in real time.

Falls back to polling every POLL_INTERVAL seconds if event subscription fails.
Auto-reconnects with exponential backoff on connection loss.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import state as _state

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 15  # seconds between polling cycles when events unavailable
_RECONNECT_BASE = 2  # seconds — first reconnect delay
_RECONNECT_MAX = 60  # seconds — max reconnect delay


def _build_now_playing(queue: dict) -> dict:
    """Extract now-playing metadata from a player_queues/all queue entry."""
    ci = queue.get("current_item") or {}
    mi = ci.get("media_item") or {}
    artists = mi.get("artists") or []
    artist = artists[0].get("name", "") if artists else ""
    album_obj = mi.get("album") or {}
    album = album_obj.get("name", "") if isinstance(album_obj, dict) else ""

    # Album art: try metadata → provider_mappings thumbnail
    image_url = ""
    metadata = mi.get("metadata") or {}
    images = metadata.get("images") or []
    if images:
        image_url = images[0].get("path", "")
    if not image_url:
        for pm in mi.get("provider_mappings") or []:
            if pm.get("thumbnail_url"):
                image_url = pm["thumbnail_url"]
                break

    result = {
        "connected": True,
        "state": queue.get("state", "idle"),
        "track": mi.get("name") or ci.get("name", ""),
        "artist": artist,
        "album": album,
        "image_url": image_url,
        "elapsed": queue.get("elapsed_time", 0),
        "elapsed_updated_at": queue.get("elapsed_time_last_updated", time.time()),
        "duration": mi.get("duration") or 0,
        "shuffle": queue.get("shuffle_enabled", False),
        "repeat": queue.get("repeat_mode", "off"),
        "queue_index": queue.get("current_index", 0),
        "queue_total": queue.get("items", 0),
        "syncgroup_id": queue.get("queue_id", ""),
    }
    # Enrich with syncgroup name from state
    syncgroup_id = result["syncgroup_id"]
    if syncgroup_id:
        for g in _state.get_ma_groups():
            if g["id"] == syncgroup_id:
                result["syncgroup_name"] = g.get("name", "")
                break
    return result


async def _find_syncgroup_queues(queues: list[dict]) -> list[dict]:
    """Return all queue entries for known MA syncgroups.

    Includes groups discovered via API *and* group_ids reported live by Sendspin devices.
    """
    groups = _state.get_ma_groups()
    group_ids = {g["id"] for g in groups}
    # Also include any group_id reported by Sendspin-connected bridge devices
    for client in _state.clients:
        gid = client.status.get("group_id") if hasattr(client, "status") else None
        if gid:
            group_ids.add(gid)
    result = [q for q in queues if q.get("queue_id") in group_ids and q.get("active")]
    if not result:
        result = [q for q in queues if q.get("queue_id") in group_ids]
    return result


def _find_solo_player_queues(queues: list[dict]) -> list[tuple[str, dict]]:
    """Return (player_id, queue) pairs for ungrouped bridge clients with their own MA queue."""
    groups = _state.get_ma_groups()
    group_ids = {g["id"] for g in groups}
    result = []
    for client in _state.clients:
        pid = getattr(client, "player_id", "")
        if not pid or pid in group_ids:
            continue  # already handled as syncgroup member
        # MA uses "up" + uuid_no_hyphens as queue_id for individual players
        pid_ma = "up" + pid.replace("-", "")
        for q in queues:
            qid = q.get("queue_id", "")
            if qid == pid or qid == pid_ma:
                result.append((pid, q))
                break
    return result


async def _send(ws, msg_id: int, command: str, args: dict) -> None:
    await ws.send(json.dumps({"command": command, "args": args, "message_id": msg_id}))


async def _recv(ws, timeout: float = 10.0) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(raw)


class MaMonitor:
    """Persistent MA WebSocket monitor task."""

    def __init__(self, ma_url: str, ma_token: str) -> None:
        self._url = ma_url
        self._token = ma_token
        # Ensure URL has scheme before building WS URL
        _url = ma_url.strip()
        if _url and "://" not in _url:
            _url = f"http://{_url}"
        self._url = _url
        self._ws_url = _url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        self._running = False
        self._msg_id = 0

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    async def _send_queue_cmd(self, ws, command: str, args: dict) -> dict:
        mid = self._next_id()
        await _send(ws, mid, command, args)
        # Read messages until we get the one with our message_id
        for _ in range(10):
            resp = await _recv(ws, timeout=5.0)
            if str(resp.get("message_id")) == str(mid):
                return resp
        return {}

    async def _refresh_stale_player_metadata(self, ws) -> None:
        """Compare bridge player device_info in MA with current version/hostname.

        If MA has stale metadata (e.g. after a version upgrade) and the player
        is not actively playing, trigger a sendspin reconnect so the subprocess
        sends a fresh client_hello with the updated product_name and manufacturer.
        """
        import socket as _socket

        from config import VERSION

        expected_product = f"Sendspin BT Bridge v{VERSION}"
        expected_host = _socket.gethostname()

        # Fetch all players from MA
        mid = self._next_id()
        await _send(ws, mid, "players/all", {})
        players: list[dict] = []
        for _ in range(30):
            try:
                resp = await _recv(ws, timeout=10.0)
            except Exception:
                break
            if str(resp.get("message_id")) == str(mid):
                players = resp.get("result") or []
                break

        if not players:
            return

        # Build bridge player lookup by name (lowercase)
        bridge_clients = {getattr(c, "player_name", "").lower(): c for c in _state.clients}

        for p in players:
            pname = (p.get("display_name") or p.get("name") or "").lower()
            matched_client = None
            for bname, c in bridge_clients.items():
                if bname and (bname in pname or pname in bname):
                    matched_client = c
                    break
            if matched_client is None:
                continue

            device_info = p.get("device_info") or {}
            product_name = device_info.get("product_name", "")
            manufacturer = device_info.get("manufacturer", "")

            if product_name == expected_product and manufacturer == expected_host:
                continue  # already up to date

            # Stale — only reconnect if player is not actively playing
            if matched_client.status.get("playing"):
                logger.info(
                    "MA: player '%s' has stale device_info (product='%s', host='%s') — skipping reconnect (playing)",
                    p.get("display_name"),
                    product_name,
                    manufacturer,
                )
                continue

            logger.info(
                "MA: player '%s' has stale device_info (product='%s' expected='%s', "
                "host='%s' expected='%s') — triggering reconnect",
                p.get("display_name"),
                product_name,
                expected_product,
                manufacturer,
                expected_host,
            )
            asyncio.create_task(matched_client.send_reconnect())

    async def _connect_and_run(self) -> None:
        """Single connection session: auth, subscribe events, poll loop."""
        try:
            import websockets
        except ImportError:
            logger.warning("websockets not installed — MA monitor disabled")
            return

        async with websockets.connect(self._ws_url) as ws:
            # Server info
            await _recv(ws, timeout=10.0)

            # Auth
            mid = self._next_id()
            await _send(ws, mid, "auth", {"token": self._token})
            auth_resp = await _recv(ws, timeout=10.0)
            if not auth_resp.get("result", {}).get("authenticated"):
                logger.warning("MA monitor: authentication failed — check MA_API_TOKEN")
                _state.set_ma_connected(False)
                return

            logger.info("MA monitor: connected and authenticated")
            _state.set_ma_connected(True)

            # Check and refresh stale player metadata (once per connect session)
            await self._refresh_stale_player_metadata(ws)

            # Initial poll
            await self._poll_queues(ws)

            # Subscribe to events
            events_ok = False
            try:
                mid = self._next_id()
                await _send(ws, mid, "subscribe_events", {"event_types": ["player_queue_updated", "player_updated"]})
                sub_resp = await _recv(ws, timeout=5.0)
                events_ok = sub_resp.get("error") is None
            except Exception:
                events_ok = False

            if events_ok:
                logger.info("MA monitor: subscribed to MA events")
                await self._event_loop(ws)
            else:
                logger.info("MA monitor: events unavailable, using polling every %ds", _POLL_INTERVAL)
                await self._polling_loop(ws)

    async def _poll_queues(self, ws) -> None:
        """Fetch player_queues/all and update now-playing cache per syncgroup and solo player."""
        try:
            mid = self._next_id()
            await _send(ws, mid, "player_queues/all", {})
            for _ in range(20):
                resp = await _recv(ws, timeout=10.0)
                if str(resp.get("message_id")) == str(mid):
                    queues = resp.get("result") or []
                    # Syncgroup players
                    syncgroup_queues = await _find_syncgroup_queues(queues)
                    for q in syncgroup_queues:
                        np = _build_now_playing(q)
                        _state.set_ma_now_playing_for_group(np["syncgroup_id"], np)
                    # Solo (ungrouped) players — keyed by their own player_id
                    for player_id, q in _find_solo_player_queues(queues):
                        np = _build_now_playing(q)
                        np["syncgroup_id"] = player_id
                        _state.set_ma_now_playing_for_group(player_id, np)
                    return
        except Exception as exc:
            logger.debug("MA monitor poll error: %s", exc)

    async def _event_loop(self, ws) -> None:
        """Process incoming MA events until connection drops."""
        _poll_deadline = time.monotonic() + _POLL_INTERVAL
        while self._running:
            try:
                timeout = max(1.0, _poll_deadline - time.monotonic())
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except TimeoutError:
                    # Periodic re-poll to keep elapsed_time fresh
                    await self._poll_queues(ws)
                    _poll_deadline = time.monotonic() + _POLL_INTERVAL
                    continue

                data = json.loads(msg)
                event = data.get("event")
                if event in ("player_queue_updated",):
                    await self._poll_queues(ws)
                    _poll_deadline = time.monotonic() + _POLL_INTERVAL
                elif event == "player_updated":
                    await self._handle_player_updated(data.get("data") or {})
            except Exception as exc:
                logger.debug("MA monitor event error: %s", exc)
                raise  # bubble up to reconnect loop

    async def _polling_loop(self, ws) -> None:
        """Fallback: poll every POLL_INTERVAL seconds."""
        while self._running:
            await asyncio.sleep(_POLL_INTERVAL)
            await self._poll_queues(ws)

    async def _handle_player_updated(self, player: dict) -> None:
        """Sync MA player volume → BT sink volume."""
        player_id = player.get("player_id", "")
        volume_level = player.get("volume_level")
        if volume_level is None:
            return

        # Find the bridge client matching this MA player_id
        for client in _state.clients:
            ma_info = _state.get_ma_group_for_player(getattr(client, "player_name", ""))
            if not ma_info:
                continue
            # Check if this MA player is a member of the client's syncgroup
            groups = _state.get_ma_groups()
            for g in groups:
                if g["id"] == ma_info["id"]:
                    member_ids = [m["id"] for m in g.get("members", [])]
                    if player_id in member_ids:
                        await self._sync_bt_volume(client, volume_level)

    async def _sync_bt_volume(self, client, volume_level: float) -> None:
        """Apply MA volume_level (0-100) to the client's BT sink."""
        bt_mgr = getattr(client, "bt_manager", None)
        if not bt_mgr:
            return
        sink = getattr(bt_mgr, "_bt_sink_name", None)
        if not sink:
            return
        # Avoid feedback loop: skip if bridge itself triggered this update
        if getattr(client, "_volume_sync_pending", False):
            return
        target = int(round(volume_level))
        current = getattr(client, "_last_bt_volume", None)
        if current is not None and abs(current - target) < 2:
            return  # no meaningful change
        try:
            proc = await asyncio.create_subprocess_exec(
                "pactl",
                "set-sink-volume",
                sink,
                f"{target}%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            logger.debug("MA volume sync: %s → %d%%", sink, target)
        except Exception as exc:
            logger.debug("MA volume sync error: %s", exc)

    async def run(self) -> None:
        """Main entry point — reconnect loop with exponential backoff."""
        self._running = True
        delay = _RECONNECT_BASE
        while self._running:
            try:
                await self._connect_and_run()
            except Exception as exc:
                logger.warning("MA monitor disconnected: %s — reconnecting in %ds", exc, delay)
            _state.set_ma_connected(False)
            _state.clear_ma_now_playing()
            if not self._running:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, _RECONNECT_MAX)

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Module-level singleton access for send_queue_cmd from Flask routes
# ---------------------------------------------------------------------------
_monitor_instance: MaMonitor | None = None


def get_monitor() -> MaMonitor | None:
    return _monitor_instance


def start_monitor(ma_url: str, ma_token: str) -> MaMonitor:
    """Create and return a MaMonitor instance (caller must schedule .run() as asyncio task)."""
    global _monitor_instance
    _monitor_instance = MaMonitor(ma_url, ma_token)
    return _monitor_instance


async def send_queue_cmd(action: str, value=None, syncgroup_id: str | None = None) -> bool:
    """Send a queue command to MA. Uses a fresh connection (monitor shared conn not easily accessible).

    Supported actions: next, previous, shuffle, repeat, seek.
    syncgroup_id: target specific group; falls back to first known group.
    Returns True on success.
    """
    from services.ma_client import _normalize_ma_url

    ma_url, ma_token = _state.get_ma_api_credentials()
    if not ma_url or not ma_token:
        return False

    groups = _state.get_ma_groups()
    if not groups:
        return False

    if syncgroup_id:
        queue_id = syncgroup_id
    else:
        queue_id = groups[0]["id"]  # fallback: first syncgroup

    try:
        import websockets

        normalized = await _normalize_ma_url(ma_url)
        ws_url = normalized.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        async with websockets.connect(ws_url) as ws:
            await _recv(ws, timeout=5.0)  # server info
            await _send(ws, 1, "auth", {"token": ma_token})
            await _recv(ws, timeout=5.0)  # auth

            if action == "next":
                await _send(ws, 2, "player_queues/next", {"queue_id": queue_id})
            elif action == "previous":
                await _send(ws, 2, "player_queues/previous", {"queue_id": queue_id})
            elif action == "shuffle":
                await _send(ws, 2, "player_queues/shuffle", {"queue_id": queue_id, "shuffle_enabled": bool(value)})
            elif action == "repeat":
                await _send(ws, 2, "player_queues/repeat", {"queue_id": queue_id, "repeat_mode": str(value)})
            elif action == "seek":
                await _send(ws, 2, "player_queues/seek", {"queue_id": queue_id, "position": int(value)})
            else:
                logger.warning("Unknown MA queue action: %s", action)
                return False

            await _recv(ws, timeout=5.0)  # ack
        logger.info("MA queue cmd: %s value=%s → %s", action, value, queue_id)
        return True
    except Exception as exc:
        logger.warning("MA queue cmd %s failed: %s", action, exc)
        return False
