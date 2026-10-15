"""Sessions by id, one per group; restore with backoff; the snapshot survives a restart.

Threading: session methods block on Music Assistant I/O, so nothing here runs on the
event loop. API worker threads call ``create``/``end``/``get``; a daemon thread calls
``tick()`` once a second; the orchestrator runs ``restore_persisted``/``shutdown`` in an
executor. ``on_change`` (``publish_change``) runs on whichever thread changed the session,
with the session's lock held: it snapshots ``to_dict()`` there, writes the snapshot file
under a leaf lock and posts the event to the loop — it never takes the store lock, which
``tick()`` holds while it walks the sessions.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_sync.models import snapshot_from_dict, to_dict
from sendspin_sync.ports import ControlError
from sendspin_sync.session import SyncSession

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sendspin_sync.ports import ClickTrackHost, GroupControl
    from sendspin_sync.session import Mode, Point

logger = logging.getLogger(__name__)

SNAPSHOT_FILE = "sync_snapshot.json"
RESTORE_BACKOFF_S = (1.0, 2.0, 5.0, 10.0, 30.0)
FINISHED_TTL_S = 60.0
TICK_INTERVAL_S = 1.0
_ENDED = ("done", "cancelled", "failed")


def _backoff(attempt: int) -> float:
    return RESTORE_BACKOFF_S[min(attempt, len(RESTORE_BACKOFF_S) - 1)]


def _close(control: object) -> None:
    close = getattr(control, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            logger.warning("Closing a sync group control failed", exc_info=True)


class SessionStore:
    def __init__(
        self,
        *,
        control_factory: Callable[[], GroupControl],
        host: ClickTrackHost,
        publish: Callable[[dict[str, Any]], None],
        snapshot_dir: Callable[[], Path],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._factory = control_factory
        self._host = host
        self._publish = publish
        self._dir = snapshot_dir
        self._clock = clock
        self._lock = threading.RLock()  # sessions and their timers
        self._tick_lock = threading.Lock()  # one tick (or shutdown) at a time
        self._file_lock = threading.Lock()  # leaf: the persisted entries and the file
        self._sessions: dict[str, SyncSession] = {}
        self._controls: dict[str, GroupControl] = {}
        self._closed: set[str] = set()
        self._next_restore: dict[str, float] = {}
        self._attempts: dict[str, int] = {}
        self._finished_at: dict[str, float] = {}
        self._hold_until: dict[str, float] = {}  # end of the undo window a finished session had
        self._entries: dict[str, dict[str, Any]] = {}  # what sync_snapshot.json holds, by session id
        self._pending: dict[str, dict[str, Any]] = {}  # entries from before a restart, not restored yet
        self._pending_next: dict[str, float] = {}
        self._pending_attempts: dict[str, int] = {}
        self._stop = threading.Event()
        self._ticker: threading.Thread | None = None

    # -- persistence ----------------------------------------------------------------
    def _path(self) -> Path:
        return self._dir() / SNAPSHOT_FILE

    def _write_file(self) -> None:
        """Caller holds ``_file_lock``."""
        path = self._path()
        try:
            if not self._entries:
                path.unlink(missing_ok=True)
                return
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"sessions": list(self._entries.values())}))
            tmp.replace(path)
        except OSError:
            logger.warning("Could not write %s", path, exc_info=True)

    def _put_entry(self, session_id: str, entry: dict[str, Any] | None) -> None:
        with self._file_lock:
            if entry is None:
                if self._entries.pop(session_id, None) is None:
                    return
            else:
                self._entries[session_id] = entry
            self._write_file()

    def publish_change(self, session: SyncSession) -> None:
        """``on_change`` of every session: persist its snapshot and publish its state."""
        data = session.to_dict()
        snapshot = session.snapshot
        if data["state"] in _ENDED or snapshot is None:
            self._put_entry(session.id, None)
        else:
            self._put_entry(
                session.id,
                {
                    "session_id": session.id,
                    "group_id": session.group_id,
                    "signal_running": data["signal_running"],
                    "soloed": data["soloed"],
                    "snapshot": to_dict(snapshot),
                },
            )
        try:
            self._publish(data)
        except Exception:
            logger.warning("Publishing a sync event failed", exc_info=True)

    # -- sessions -------------------------------------------------------------------
    def active_for(self, group_id: str) -> SyncSession | None:
        with self._lock:
            return next(
                (s for s in self._sessions.values() if s.group_id == group_id and s.state not in _ENDED),
                None,
            )

    def create(self, group_id: str, mode: Mode, point: Point) -> SyncSession:
        with self._lock:
            if self.active_for(group_id) is not None:
                raise ControlError("session_active", "tuning in progress for this group")
            try:
                control = self._factory()
            except MaApiError as exc:
                raise ControlError(exc.code, exc.detail) from exc
            session = SyncSession(
                session_id=uuid.uuid4().hex,
                group_id=group_id,
                mode=mode,
                point=point,
                control=control,
                host=self._host,
                clock=self._clock,
                on_change=self.publish_change,
            )
            self._sessions[session.id] = session  # reserves the group while start() talks to MA
            self._controls[session.id] = control
        try:
            session.start()
        except BaseException:
            if session.signal_running:  # the click track plays: restore it like any other session
                session.cancel()
                raise
            with self._lock:
                self._sessions.pop(session.id, None)
                self._controls.pop(session.id, None)
            self._host.release(session.id)
            self._put_entry(session.id, None)
            _close(control)
            raise
        return session

    def get(self, session_id: str) -> SyncSession:
        with self._lock:
            s = self._sessions.get(session_id)
        if s is None:
            raise ControlError("session_expired", "no such session")
        return s

    def end(self, session_id: str) -> None:
        """Cancel the session; the next tick restores the group (then with backoff)."""
        s = self.get(session_id)
        s.cancel()
        with self._lock:
            self._next_restore.setdefault(s.id, self._clock())

    # -- the 1 s tick ----------------------------------------------------------------
    def tick(self) -> None:
        with self._tick_lock:
            now = self._clock()
            with self._lock:
                sessions = list(self._sessions.values())
            for s in sessions:
                self._tick_session(s, now)
            with self._lock:
                due = [sid for sid in self._pending if now >= self._pending_next.get(sid, 0.0)]
            for sid in due:
                self._try_pending(sid)

    def _tick_session(self, s: SyncSession, now: float) -> None:
        if s.state not in _ENDED and s.state != "restoring" and s.expired():
            logger.info("Sync session %s idle for too long; ending it", s.id)
            s.cancel()
        if s.state == "restoring" and now >= self._next_restore.get(s.id, 0.0):
            try:
                ok = s.restore_tick()
            except Exception:
                logger.warning("Restoring sync session %s failed", s.id, exc_info=True)
                ok = False
            with self._lock:
                if ok:
                    self._next_restore.pop(s.id, None)
                    self._attempts.pop(s.id, None)
                else:
                    n = self._attempts.get(s.id, 0)
                    self._attempts[s.id] = n + 1
                    self._next_restore[s.id] = now + _backoff(n)
        if s.state in _ENDED:
            self._tick_finished(s, now)

    def _tick_finished(self, s: SyncSession, now: float) -> None:
        with self._lock:
            if s.id not in self._finished_at:
                self._finished_at[s.id] = now
                self._hold_until[s.id] = max(now, s.undo_until or now)
            undo_open = s.undo_until is not None and now <= s.undo_until
            close = s.id not in self._closed and not undo_open
            evict = now - self._hold_until[s.id] > FINISHED_TTL_S
            control = self._controls.get(s.id)
            if close:
                self._closed.add(s.id)
            if evict:
                for d in (self._next_restore, self._attempts, self._finished_at, self._hold_until, self._controls):
                    d.pop(s.id, None)
                self._sessions.pop(s.id, None)
                self._closed.discard(s.id)
        if close and control is not None:
            _close(control)

    # -- restore after a restart -------------------------------------------------------
    def restore_persisted(self) -> None:
        """On boot: restore what a session left behind (stop the signal, clear the solo, re-assert
        volume and mute) and delete the file; while Music Assistant is unreachable, retry with backoff."""
        path = self._path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text())
            entries = [e for e in raw["sessions"] if isinstance(e, dict)]
            for e in entries:
                snapshot_from_dict(e["snapshot"])
                str(e["session_id"]), str(e["group_id"])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("Unreadable %s; removing it", path, exc_info=True)
            path.unlink(missing_ok=True)
            return
        with self._lock:
            for e in entries:
                sid = str(e["session_id"])
                if sid not in self._sessions:
                    self._pending[sid] = e
        with self._file_lock:
            for e in entries:
                self._entries.setdefault(str(e["session_id"]), e)
        with self._tick_lock:
            for sid in list(self._pending):
                self._try_pending(sid)

    def _try_pending(self, sid: str) -> None:
        with self._lock:
            entry = self._pending.get(sid)
        if entry is None:
            return
        group = str(entry["group_id"])
        control: GroupControl | None = None
        try:
            control = self._factory()
            if entry.get("soloed") is not None:
                try:
                    control.solo(group, None)
                except ControlError as exc:
                    if exc.code != "member_gone":  # the group is gone: restore() still unmutes
                        raise
                entry["soloed"] = None
            if entry.get("signal_running"):
                control.stop_signal(group)
                entry["signal_running"] = False
            control.restore(snapshot_from_dict(entry["snapshot"]))
        except Exception as exc:
            with self._lock:
                n = self._pending_attempts.get(sid, 0)
                self._pending_attempts[sid] = n + 1
                self._pending_next[sid] = self._clock() + _backoff(n)
            self._put_entry(sid, entry)  # keep what already succeeded
            log = logger.warning if n == 0 else logger.debug
            log("Restoring the group after a restart failed (%s); retrying", exc)
            return
        finally:
            if control is not None:
                _close(control)
        with self._lock:
            self._pending.pop(sid, None)
            self._pending_next.pop(sid, None)
            self._pending_attempts.pop(sid, None)
        self._put_entry(sid, None)
        logger.info("Sync session %s: group %s restored after a restart", sid, group)

    # -- lifecycle --------------------------------------------------------------------
    def start_ticker(self, interval: float = TICK_INTERVAL_S) -> None:
        with self._lock:
            if self._ticker is not None and self._ticker.is_alive():
                return
            self._stop.clear()
            self._ticker = threading.Thread(target=self._run, args=(interval,), name="sync-session-tick", daemon=True)
            self._ticker.start()

    def _run(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                self.tick()
            except Exception:
                logger.exception("Sync session tick failed")

    def shutdown(self) -> None:
        """Cancel live sessions and try one restore each; a session that cannot be restored keeps
        its entry in the snapshot file, so the next boot restores it."""
        self._stop.set()
        ticker = self._ticker
        if ticker is not None and ticker is not threading.current_thread():
            ticker.join(timeout=5.0)
        with self._tick_lock:
            with self._lock:
                sessions = list(self._sessions.values())
                controls = dict(self._controls)
            for s in sessions:
                try:
                    s.cancel()
                    if s.state == "restoring":
                        s.restore_tick()
                except Exception:
                    logger.warning("Restoring sync session %s at shutdown failed", s.id, exc_info=True)
            for s in sessions:
                if s.state not in _ENDED:  # an ended session released its token in restore_tick()
                    self._host.release(s.id)
            with self._lock:
                to_close = [c for sid, c in controls.items() if sid not in self._closed]
                self._closed.update(controls)
            for control in to_close:
                _close(control)


# -- module singleton ----------------------------------------------------------------
_store: SessionStore | None = None
_store_lock = threading.Lock()
_hub: Any = None  # the API's EventHub, attached by create_app()


def attach_event_hub(hub: Any) -> None:
    global _hub
    _hub = hub


def _publish_to_hub(data: dict[str, Any]) -> None:
    hub = _hub
    if hub is None:
        return
    from sendspin_bridge.api.events import envelope

    hub.publish_threadsafe(envelope("sync", data))


def get_store() -> SessionStore:
    global _store
    with _store_lock:
        if _store is None:
            from sendspin_bridge import config as cfg
            from sendspin_bridge.sync_tuning.click_track_host import FastApiClickTrackHost
            from sendspin_bridge.sync_tuning.ma_group_control import default_group_control

            _store = SessionStore(
                control_factory=default_group_control,
                host=FastApiClickTrackHost(),
                publish=_publish_to_hub,
                snapshot_dir=lambda: cfg.CONFIG_DIR,
            )
            _store.start_ticker()
        return _store


def shutdown_store() -> None:
    """Shut the store down if one was built (blocking: run it off the event loop)."""
    store = _store
    if store is not None:
        store.shutdown()
