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
import itertools
import json
import logging
import time
from typing import TYPE_CHECKING

import state as _state
from services.device_registry import get_device_registry_snapshot
from services.ma_artwork import build_artwork_proxy_url

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 15  # seconds between polling cycles when events unavailable
_GROUPS_REFRESH_INTERVAL = 60  # seconds between syncgroup cache refreshes
_RECONNECT_BASE = 2  # seconds — first reconnect delay
_RECONNECT_MAX = 60  # seconds — max reconnect delay


def _active_bridge_clients() -> list:
    """Return the current active bridge clients from the registry snapshot."""
    return get_device_registry_snapshot().active_clients


def solo_queue_candidates(player_id: str | None) -> list[str]:
    """Return ordered MA solo queue IDs compatible with current and legacy players."""
    raw_player_id = str(player_id or "").strip()
    if not raw_player_id:
        return []

    if raw_player_id.startswith(("media_player.", "ma_", "syncgroup_")):
        return [raw_player_id]

    legacy_queue_id = "up" + raw_player_id.replace("-", "")
    if raw_player_id.startswith("sendspin-"):
        return [raw_player_id, legacy_queue_id]

    return [legacy_queue_id, raw_player_id]


class _AuthFailed(Exception):
    """Raised when MA WebSocket authentication fails."""


def _build_queue_item_summary(item: dict) -> dict[str, str]:
    """Return compact track metadata for a queue item."""
    if not isinstance(item, dict):
        return {}
    media_item = item.get("media_item") or {}
    artists = media_item.get("artists") or []
    artist = artists[0].get("name", "") if artists else ""
    album_obj = media_item.get("album") or {}
    album = album_obj.get("name", "") if isinstance(album_obj, dict) else ""
    track = media_item.get("name") or item.get("name", "")
    if not track:
        return {}
    return {
        "track": track,
        "artist": artist,
        "album": album,
    }


def _build_queue_neighbors(queue: dict) -> tuple[dict[str, str], dict[str, str]]:
    """Return previous/next queue item summaries when available."""
    previous_item = queue.get("previous_item") or {}
    next_item = queue.get("next_item") or {}
    items = queue.get("items")
    if isinstance(items, list):
        current_index = queue.get("current_index", 0)
        if not previous_item and isinstance(current_index, int) and current_index > 0:
            previous_item = items[current_index - 1] if current_index - 1 < len(items) else {}
        if not next_item and isinstance(current_index, int) and current_index + 1 < len(items):
            next_item = items[current_index + 1]
    return _build_queue_item_summary(previous_item), _build_queue_item_summary(next_item)


def _set_queue_neighbor(result: dict, prefix: str, item: dict[str, str]) -> None:
    """Store queue-neighbor summary on the now-playing payload."""
    if not item:
        return
    result[f"{prefix}_track"] = item["track"]
    result[f"{prefix}_artist"] = item["artist"]
    result[f"{prefix}_album"] = item["album"]


async def _hydrate_missing_queue_neighbors(
    fetch_items: Callable[[str, int, int], Awaitable[list[dict]]],
    queue: dict,
    result: dict,
) -> None:
    """Fetch missing previous/next queue items when player_queues/all omits them."""
    queue_id = queue.get("queue_id", "")
    current_index = queue.get("current_index")
    queue_total = result.get("queue_total")
    if not queue_id or not isinstance(current_index, int):
        return

    if not result.get("prev_track") and current_index > 0:
        prev_items = await fetch_items(queue_id, 1, current_index - 1)
        if prev_items:
            _set_queue_neighbor(result, "prev", _build_queue_item_summary(prev_items[0]))

    if (
        not result.get("next_track")
        and isinstance(queue_total, int)
        and queue_total > 0
        and current_index + 1 < queue_total
    ):
        next_items = await fetch_items(queue_id, 1, current_index + 1)
        if next_items:
            _set_queue_neighbor(result, "next", _build_queue_item_summary(next_items[0]))


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

    previous_item, next_item = _build_queue_neighbors(queue)
    queue_items = queue.get("items")
    queue_total = len(queue_items) if isinstance(queue_items, list) else queue.get("items", 0)

    result = {
        "connected": True,
        "state": queue.get("state", "idle"),
        "track": mi.get("name") or ci.get("name", ""),
        "artist": artist,
        "album": album,
        "image_url": build_artwork_proxy_url(image_url),
        "elapsed": queue.get("elapsed_time", 0),
        "elapsed_updated_at": queue.get("elapsed_time_last_updated", time.time()),
        "duration": mi.get("duration") or 0,
        "shuffle": queue.get("shuffle_enabled", False),
        "repeat": queue.get("repeat_mode", "off"),
        "queue_index": queue.get("current_index", 0),
        "queue_total": queue_total,
        "syncgroup_id": queue.get("queue_id", ""),
    }
    _set_queue_neighbor(result, "prev", previous_item)
    _set_queue_neighbor(result, "next", next_item)
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
    for client in _active_bridge_clients():
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
    for client in _active_bridge_clients():
        pid = getattr(client, "player_id", "")
        if not pid or pid in group_ids:
            continue  # already handled as syncgroup member
        queue_candidates = set(solo_queue_candidates(pid))
        for q in queues:
            qid = q.get("queue_id", "")
            if qid in queue_candidates:
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
        self._msg_id = itertools.count(1)
        self._cmd_queue: asyncio.Queue[tuple[str, dict, asyncio.Future]] = asyncio.Queue()
        self._ws = None  # current websocket, set while connected
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_queue_refresh = False
        self._pending_groups_refresh = False
        self._wake_event = asyncio.Event()

    def _next_id(self) -> int:
        return next(self._msg_id)

    def is_connected(self) -> bool:
        """Return True when the persistent MA monitor WS is available."""
        return self._running and self._ws is not None

    def _defer_incoming_event(self, event: str) -> None:
        """Remember interleaved MA events so they can be processed after command ack."""
        if event == "player_queue_updated":
            self._pending_queue_refresh = True
        elif event == "player_updated":
            self._pending_groups_refresh = True

    async def _flush_deferred_updates(self, ws) -> None:
        """Process interleaved events after the current command round-trip finishes."""
        if self._pending_queue_refresh:
            self._pending_queue_refresh = False
            logger.debug("MA monitor: flushing deferred queue refresh")
            await self._poll_queues(ws)
        if self._pending_groups_refresh:
            self._pending_groups_refresh = False
            logger.debug("MA monitor: flushing deferred groups refresh")
            await self._refresh_groups_via_ws(ws)

    @staticmethod
    def _detect_ha_addon(server_info: dict) -> None:
        """Auto-set MA_AUTH_PROVIDER=ha when MA reports homeassistant_addon."""
        try:
            is_addon = server_info.get("server_info", server_info).get("homeassistant_addon")
            if not is_addon:
                return
            from config import load_config, update_config

            cfg = load_config()
            if cfg.get("MA_AUTH_PROVIDER") == "ha":
                return
            logger.info("MA is running as HA addon — setting MA_AUTH_PROVIDER=ha")
            update_config(lambda c: c.__setitem__("MA_AUTH_PROVIDER", "ha"))
        except Exception:
            logger.debug("Failed to detect HA addon from server_info", exc_info=True)

    async def execute_cmd(self, command: str, args: dict) -> dict:
        """Send a command through the persistent WS. Returns response or raises."""
        if not self._running or self._ws is None:
            raise RuntimeError("Monitor not connected")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        await self._cmd_queue.put((command, args, fut))
        self._wake_event.set()
        return await asyncio.wait_for(fut, timeout=3.0)

    async def request_queue_refresh(self, syncgroup_id: str | None = None) -> bool:
        """Wake the monitor and refresh queue state as soon as possible."""
        if not self._running or self._ws is None:
            return False
        if syncgroup_id:
            logger.debug("MA monitor: fast queue refresh requested for %s", syncgroup_id)
        self._pending_queue_refresh = True
        self._wake_event.set()
        return True

    async def _drain_cmd_queue(self, ws) -> None:
        """Process any pending commands from the command queue."""
        while not self._cmd_queue.empty():
            try:
                command, args, fut = self._cmd_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                mid = self._next_id()
                await _send(ws, mid, command, args)
                # Match response by message_id; forward interleaved events
                for _ in range(10):
                    resp = await _recv(ws, timeout=5.0)
                    if str(resp.get("message_id")) == str(mid):
                        if not fut.done():
                            fut.set_result(resp)
                        break
                    evt = resp.get("event")
                    if evt:
                        logger.debug("MA monitor: interleaved event '%s' during cmd drain", evt)
                        self._defer_incoming_event(evt)
                    else:
                        logger.debug("MA monitor: non-matching msg (id=%s) during cmd drain", resp.get("message_id"))
                else:
                    if not fut.done():
                        fut.set_exception(TimeoutError(f"No matching response for {command}"))
            except Exception as e:
                if not fut.done():
                    fut.set_exception(e)

    async def _process_local_work(self, ws) -> None:
        """Handle queued commands and deferred refreshes immediately."""
        await self._drain_cmd_queue(ws)
        await self._flush_deferred_updates(ws)

    async def _send_queue_cmd(self, ws, command: str, args: dict) -> dict:
        return await self._request_command(ws, command, args)

    async def _request_command(self, ws, command: str, args: dict) -> dict:
        """Send a WS command and return the matching response payload."""
        mid = self._next_id()
        await _send(ws, mid, command, args)
        # Read messages until we get the one with our message_id
        for _ in range(10):
            resp = await _recv(ws, timeout=5.0)
            if str(resp.get("message_id")) == str(mid):
                await self._flush_deferred_updates(ws)
                return resp
            evt = resp.get("event")
            if evt:
                logger.debug("MA monitor: interleaved event '%s' during queue cmd", evt)
                self._defer_incoming_event(evt)
            else:
                logger.debug("MA monitor: non-matching msg (id=%s) during queue cmd", resp.get("message_id"))
        await self._flush_deferred_updates(ws)
        return {}

    async def _fetch_queue_items(self, ws, queue_id: str, limit: int = 1, offset: int = 0) -> list[dict]:
        """Fetch a slice of queue items for a queue via MA WebSocket API."""
        if not queue_id or limit < 1 or offset < 0:
            return []
        resp = await self._request_command(
            ws,
            "player_queues/items",
            {"queue_id": queue_id, "limit": limit, "offset": offset},
        )
        result = resp.get("result")
        return result if isinstance(result, list) else []

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
            evt = resp.get("event")
            if evt:
                logger.debug("MA monitor: interleaved event '%s' during metadata refresh", evt)
            else:
                logger.debug("MA monitor: non-matching msg (id=%s) during metadata refresh", resp.get("message_id"))

        if not players:
            return

        # Build bridge player lookup by name (lowercase)
        bridge_clients = {getattr(c, "player_name", "").lower(): c for c in _active_bridge_clients()}

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
            _task = asyncio.create_task(matched_client.send_reconnect())
            _task.add_done_callback(
                lambda t: logger.debug("MA send_reconnect error: %s", t.exception()) if t.exception() else None
            )

    async def _refresh_groups_via_ws(self, ws) -> None:
        """Fetch players/all via WS and rebuild the syncgroup cache in state."""
        try:
            mid = self._next_id()
            await _send(ws, mid, "players/all", {})
            for _ in range(30):
                resp = await _recv(ws, timeout=10.0)
                if str(resp.get("message_id")) == str(mid):
                    players = resp.get("result") or []
                    break
            else:
                return

            id_to_name: dict[str, str] = {
                p["player_id"]: (p.get("display_name") or p.get("name") or "") for p in players
            }
            all_groups: list[dict] = []
            id_map: dict[str, dict] = {}

            clients = _active_bridge_clients()
            bridge_info = [
                {"player_id": getattr(c, "player_id", ""), "player_name": getattr(c, "player_name", "")}
                for c in clients
                if getattr(c, "player_id", "")
            ]

            member_set_by_group: dict[str, set[str]] = {}

            for p in players:
                if p.get("type") != "group" or p.get("provider") != "sync_group":
                    continue
                sg_id = p["player_id"]
                sg_name = p.get("display_name") or p.get("name") or sg_id
                raw_members = p.get("group_members") or []
                member_ids = set(raw_members)
                member_set_by_group[sg_id] = member_ids
                members = [{"id": mid_m, "name": id_to_name.get(mid_m, mid_m)} for mid_m in raw_members]
                all_groups.append({"id": sg_id, "name": sg_name, "members": members})
                sg_info = {"id": sg_id, "name": sg_name}
                for bp in bridge_info:
                    pid = bp["player_id"]
                    if pid in member_ids and pid not in id_map:
                        id_map[pid] = sg_info

            # Fallback: fuzzy name matching for unmatched bridge players
            if bridge_info and all_groups:
                member_names_by_group: dict[str, set[str]] = {}
                for sg_id, mids in member_set_by_group.items():
                    member_names_by_group[sg_id] = {id_to_name.get(mid_m, "").lower() for mid_m in mids}
                for bp in bridge_info:
                    pid = bp["player_id"]
                    if pid in id_map:
                        continue
                    bname = bp["player_name"]
                    if not bname:
                        continue
                    b = bname.lower()
                    for g in all_groups:
                        mn_set = member_names_by_group.get(g["id"], set())
                        if any(b in mn or mn in b for mn in mn_set if mn):
                            id_map[pid] = {"id": g["id"], "name": g["name"]}
                            break

            _state.set_ma_groups(id_map, all_groups)
        except Exception as exc:
            logger.debug("MA monitor groups refresh error: %s", exc)

    async def _connect_and_run(self) -> None:
        """Single connection session: auth, subscribe events, poll loop."""
        # Re-read credentials from state — they may have been updated by
        # silent auth or manual login since the monitor was created.
        fresh_url, fresh_token = _state.get_ma_api_credentials()
        if fresh_url and fresh_token:
            self._token = fresh_token
            _url = fresh_url.strip()
            if _url and "://" not in _url:
                _url = f"http://{_url}"
            self._url = _url
            self._ws_url = _url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

        try:
            import websockets
        except ImportError:
            logger.warning("websockets not installed — MA monitor disabled")
            return

        _ws_kw: dict = {"proxy": None} if int(websockets.__version__.split(".")[0]) >= 15 else {}
        async with websockets.connect(self._ws_url, **_ws_kw) as ws:
            # Server info
            server_info = await _recv(ws, timeout=10.0)
            self._detect_ha_addon(server_info)
            # Cache MA server version
            si = server_info.get("server_info", server_info)
            _ma_ver = si.get("server_version", "")
            if _ma_ver:
                _state.set_ma_server_version(_ma_ver)

            # Auth
            mid = self._next_id()
            await _send(ws, mid, "auth", {"token": self._token})
            auth_resp = await _recv(ws, timeout=10.0)
            if not auth_resp.get("result", {}).get("authenticated"):
                logger.warning(
                    "MA monitor: authentication failed — token invalid or expired. "
                    "Reconfigure via web UI → Music Assistant section (login or paste new token)"
                )
                _state.set_ma_connected(False)
                raise _AuthFailed("check MA_API_TOKEN")

            logger.info("MA monitor: connected and authenticated")
            _state.set_ma_connected(True)
            self._ws = ws

            try:
                # Check and refresh stale player metadata (once per connect session)
                await self._refresh_stale_player_metadata(ws)

                # Initial poll
                await self._poll_queues(ws)

                # Subscribe to events
                events_ok = False
                try:
                    mid = self._next_id()
                    await _send(
                        ws, mid, "subscribe_events", {"event_types": ["player_queue_updated", "player_updated"]}
                    )
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
            finally:
                self._ws = None

    async def _poll_queues(self, ws) -> None:
        """Fetch player_queues/all and update now-playing cache per syncgroup and solo player."""
        try:
            mid = self._next_id()
            await _send(ws, mid, "player_queues/all", {})
            for _ in range(20):
                resp = await _recv(ws, timeout=10.0)
                if str(resp.get("message_id")) == str(mid):
                    queues = resp.get("result") or []
                    fresh: dict[str, dict] = {}

                    async def fetch_items(queue_id: str, limit: int = 1, offset: int = 0) -> list[dict]:
                        return await self._fetch_queue_items(ws, queue_id, limit=limit, offset=offset)

                    # Syncgroup players
                    syncgroup_queues = await _find_syncgroup_queues(queues)
                    for q in syncgroup_queues:
                        np = _build_now_playing(q)
                        await _hydrate_missing_queue_neighbors(fetch_items, q, np)
                        fresh[np["syncgroup_id"]] = np
                    # Solo (ungrouped) players — keyed by their own player_id
                    for player_id, q in _find_solo_player_queues(queues):
                        np = _build_now_playing(q)
                        await _hydrate_missing_queue_neighbors(fetch_items, q, np)
                        np["syncgroup_id"] = player_id
                        fresh[player_id] = np
                    # Atomically replace to clear stale entries
                    if fresh:
                        _state.replace_ma_now_playing(fresh)
                    return
        except Exception as exc:
            logger.debug("MA monitor poll error: %s", exc)

    async def _event_loop(self, ws) -> None:
        """Process incoming MA events until connection drops."""
        _poll_deadline = time.monotonic() + _POLL_INTERVAL
        _groups_deadline = time.monotonic() + _GROUPS_REFRESH_INTERVAL
        while self._running:
            try:
                await self._process_local_work(ws)

                now = time.monotonic()
                next_deadline = min(_poll_deadline, _groups_deadline)
                timeout = max(0.5, next_deadline - now)

                recv_task = asyncio.create_task(ws.recv())
                wake_task = asyncio.create_task(self._wake_event.wait())
                timer_task = asyncio.create_task(asyncio.sleep(timeout))
                done, pending = await asyncio.wait(
                    {recv_task, wake_task, timer_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

                if wake_task in done:
                    self._wake_event.clear()
                    await self._process_local_work(ws)
                    continue

                if timer_task in done:
                    now = time.monotonic()
                    if now >= _poll_deadline:
                        await self._poll_queues(ws)
                        _poll_deadline = time.monotonic() + _POLL_INTERVAL
                    if now >= _groups_deadline:
                        await self._refresh_groups_via_ws(ws)
                        _groups_deadline = time.monotonic() + _GROUPS_REFRESH_INTERVAL
                    continue

                msg = recv_task.result()
                data = json.loads(msg)
                event = data.get("event")
                if event == "player_queue_updated":
                    self._defer_incoming_event(event)
                    await self._flush_deferred_updates(ws)
                    _poll_deadline = time.monotonic() + _POLL_INTERVAL
                elif event == "player_updated":
                    self._defer_incoming_event(event)
                    await self._flush_deferred_updates(ws)
                    _groups_deadline = time.monotonic() + _GROUPS_REFRESH_INTERVAL
            except Exception as exc:
                logger.debug("MA monitor event error: %s", exc)
                raise  # bubble up to reconnect loop

    async def _polling_loop(self, ws) -> None:
        """Fallback: poll every POLL_INTERVAL seconds."""
        while self._running:
            await self._process_local_work(ws)
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=_POLL_INTERVAL)
                self._wake_event.clear()
                await self._process_local_work(ws)
                continue
            except TimeoutError:
                await self._poll_queues(ws)

    async def run(self) -> None:
        """Main entry point — reconnect loop with exponential backoff."""
        self._running = True
        self._loop = asyncio.get_running_loop()
        delay = _RECONNECT_BASE
        _prev_token = self._token
        while self._running:
            disconnect_error = "connection lost"
            try:
                await self._connect_and_run()
                delay = _RECONNECT_BASE  # reset on successful connection
            except Exception as exc:
                disconnect_error = str(exc)
                logger.warning("MA monitor disconnected: %s — reconnecting in %ds", exc, delay)
            self._ws = None
            # Cancel any pending command futures
            while not self._cmd_queue.empty():
                try:
                    _, _, fut = self._cmd_queue.get_nowait()
                    if not fut.done():
                        fut.cancel()
                except asyncio.QueueEmpty:
                    break
            _state.set_ma_connected(False)
            _state.mark_ma_now_playing_stale(disconnect_error)
            if not self._running:
                break
            await asyncio.sleep(delay)
            # Reset backoff if credentials changed (e.g. silent auth obtained new token)
            _, fresh_token = _state.get_ma_api_credentials()
            if fresh_token and fresh_token != _prev_token:
                delay = _RECONNECT_BASE
                _prev_token = fresh_token
            else:
                delay = min(delay * 2, _RECONNECT_MAX)

    def stop(self) -> None:
        self._running = False
        ws = self._ws
        if ws:
            loop = self._loop
            if loop:
                try:
                    asyncio.run_coroutine_threadsafe(ws.close(), loop)
                except Exception:
                    pass


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


async def send_queue_cmd(action: str, value=None, syncgroup_id: str | None = None) -> dict:
    """Send a queue command through the persistent MA monitor only.

    Supported actions: next, previous, shuffle, repeat, seek.
    syncgroup_id: target specific group; falls back to first known group.
    Returns metadata describing MA acknowledgement on success.
    """
    if syncgroup_id:
        queue_id = syncgroup_id
    else:
        groups = _state.get_ma_groups()
        if not groups:
            return {"accepted": False, "queue_id": "", "error": "no queue available"}
        queue_id = groups[0]["id"]  # fallback: first syncgroup

    command: str
    args: dict[str, object]
    if action == "next":
        command, args = "player_queues/next", {"queue_id": queue_id}
    elif action == "previous":
        command, args = "player_queues/previous", {"queue_id": queue_id}
    elif action == "shuffle":
        command, args = "player_queues/shuffle", {"queue_id": queue_id, "shuffle_enabled": bool(value)}
    elif action == "repeat":
        command, args = "player_queues/repeat", {"queue_id": queue_id, "repeat_mode": str(value)}
    elif action == "seek":
        command, args = "player_queues/seek", {"queue_id": queue_id, "position": int(value) if value is not None else 0}
    else:
        logger.warning("Unknown MA queue action: %s", action)
        return {"accepted": False, "queue_id": queue_id, "error": "unknown action"}

    mon = _monitor_instance
    if mon is None or not mon.is_connected():
        logger.info("MA queue cmd skipped: monitor unavailable for %s → %s", action, queue_id)
        return {"accepted": False, "queue_id": queue_id, "error": "monitor unavailable"}

    try:
        started = time.monotonic()
        resp = await mon.execute_cmd(command, args)
        accepted_at = time.time()
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.get("error") is None:
            logger.info("MA queue cmd (monitor): %s value=%s → %s ack=%dms", action, value, queue_id, latency_ms)
            return {
                "accepted": True,
                "queue_id": queue_id,
                "ack_latency_ms": latency_ms,
                "accepted_at": accepted_at,
            }
        logger.warning("MA queue cmd rejected: %s value=%s → %s resp=%s", action, value, queue_id, resp)
    except Exception as exc:
        logger.warning("MA queue cmd %s failed via monitor: %s", action, exc)
        return {"accepted": False, "queue_id": queue_id, "error": str(exc)}
    return {"accepted": False, "queue_id": queue_id, "error": "command rejected"}


async def request_queue_refresh(syncgroup_id: str | None = None) -> bool:
    """Ask the persistent MA monitor to refresh queue state immediately."""
    mon = _monitor_instance
    if mon is None or not mon.is_connected():
        return False
    return await mon.request_queue_refresh(syncgroup_id)


async def send_player_cmd(command: str, args: dict) -> bool:
    """Send a player command to MA, preferring the monitor's persistent WS.

    Useful commands:
      players/cmd/volume_set      {player_id, volume_level}
      players/cmd/group_volume    {player_id, volume_level}  (delta approach)
      players/cmd/volume_mute     {player_id, muted}
      players/cmd/group_volume_mute {player_id, muted}

    Returns True on success.
    """
    # Try the persistent monitor connection first (lower latency)
    mon = _monitor_instance
    if mon and mon._ws is not None:
        try:
            resp = await mon.execute_cmd(command, args)
            if resp.get("error") is None:
                logger.info("MA player cmd (monitor): %s args=%s", command, args)
                return True
        except Exception as exc:
            logger.debug("MA player cmd via monitor failed, falling back: %s", exc)

    # Fallback: fresh WS connection
    from services.ma_client import _normalize_ma_url

    ma_url, ma_token = _state.get_ma_api_credentials()
    if not ma_url or not ma_token:
        return False

    try:
        import websockets

        _ws_kw: dict = {"proxy": None} if int(websockets.__version__.split(".")[0]) >= 15 else {}
        normalized = await _normalize_ma_url(ma_url)
        ws_url = normalized.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        async with websockets.connect(ws_url, **_ws_kw) as ws:
            await _recv(ws, timeout=5.0)  # server info
            await _send(ws, 1, "auth", {"token": ma_token})
            await _recv(ws, timeout=5.0)  # auth result
            await _send(ws, 2, command, args)
            await _recv(ws, timeout=5.0)  # ack
        logger.info("MA player cmd: %s args=%s", command, args)
        return True
    except Exception as exc:
        logger.warning("MA player cmd %s failed: %s", command, exc)
        return False
