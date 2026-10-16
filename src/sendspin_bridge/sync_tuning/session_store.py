"""Sessions by id, one per group; restore with backoff; what a session did to the group survives a restart.

A session's entry in the snapshot file names the members it muted and the click track it started,
written whenever that changes: after a restart the members are unmuted and the group is stopped
if it still plays that click track.

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
import os
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

from sendspin_bridge.sync_tuning.ma_api import MaApiError
from sendspin_sync.ports import ControlError
from sendspin_sync.session import RESTORE_GIVE_UP_S, SyncSession

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
        self._entries: dict[str, dict[str, Any]] = {}  # what sync_snapshot.json holds, by session id
        self._pending: dict[str, dict[str, Any]] = {}  # entries from before a restart, not restored yet
        self._pending_next: dict[str, float] = {}
        self._pending_attempts: dict[str, int] = {}
        self._pending_errors: dict[str, str] = {}
        self._pending_since: dict[str, float] = {}  # first restore attempt after the restart
        self._measured_at: dict[tuple[str, str], str] = {}  # (group, member) -> last ok measurement; this run only
        self._load_lock = threading.Lock()  # taken before _lock, never while holding it
        self._loaded = False
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
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps({"sessions": list(self._entries.values())}))
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(path)
            self._fsync_dir(path.parent)
        except OSError:
            logger.warning("Could not write %s", path, exc_info=True)

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        try:
            fd = os.open(directory, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _put_entry(self, session_id: str, entry: dict[str, Any] | None) -> None:
        with self._file_lock:
            if not self._loaded:  # never drop entries this store has not loaded (sessions only exist after a load)
                for e in self._read_file() or []:
                    self._entries.setdefault(str(e["session_id"]), e)
            if entry is None:
                if self._entries.pop(session_id, None) is None:
                    return
            else:
                if self._entries.get(session_id) == entry:
                    return  # unchanged: no write
                self._entries[session_id] = entry
            self._write_file()

    def _read_file(self) -> list[dict[str, Any]] | None:
        """The entries on disk; None (after moving the file aside) when it cannot be read."""
        path = self._path()
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text())
            entries = [e for e in raw["sessions"] if isinstance(e, dict)]
            for e in entries:
                str(e["session_id"]), str(e["group_id"])
                [str(m) for m in e.get("muted") or []]
        except (OSError, ValueError, KeyError, TypeError):
            corrupt = path.with_name(path.name + ".corrupt")
            logger.warning("Unreadable %s; moved aside to %s", path, corrupt.name, exc_info=True)
            try:
                path.replace(corrupt)
            except OSError:
                path.unlink(missing_ok=True)
            return None
        return entries

    def load_persisted(self) -> None:
        """Register what the snapshot file holds as pending restores, without touching Music
        Assistant. Must run before the API can create sessions (the orchestrator calls it before the
        web server starts); idempotent. Any operation that reads or writes sessions loads first."""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            tmp = self._path().with_suffix(".tmp")
            try:
                tmp.unlink(missing_ok=True)  # a write cut short by the last shutdown
            except OSError:
                # never leave the store unloaded over it: every create() would fail
                logger.warning("Could not remove %s", tmp, exc_info=True)
            entries = self._read_file() or []
            with self._lock:
                for e in entries:
                    sid = str(e["session_id"])
                    if sid not in self._sessions:
                        self._pending[sid] = e
            with self._file_lock:
                for e in entries:
                    self._entries.setdefault(str(e["session_id"]), e)
            self._loaded = True

    def publish_change(self, session: SyncSession) -> None:
        """``on_change`` of every session: persist what it did to the group and publish its state."""
        data = session.to_dict()
        clicker = session.clicker_url
        if data["state"] in _ENDED or (not data["muted"] and clicker is None):
            self._put_entry(session.id, None)  # nothing to put back
        else:
            self._put_entry(
                session.id,
                {
                    "session_id": session.id,
                    "group_id": session.group_id,
                    "muted": data["muted"],
                    "clicker_url": clicker,
                },
            )
        try:
            self._publish(data)
        except Exception:
            logger.warning("Publishing a sync event failed", exc_info=True)

    # -- last measurement per member ---------------------------------------------------
    def mark_measured(self, group_id: str, member: str, at: str) -> None:
        """Remember when *member* of *group_id* last gave a usable measurement (kept for this run)."""
        with self._lock:
            self._measured_at[(group_id, member)] = at

    def measured_at(self, group_id: str) -> dict[str, str]:
        """Member id -> time of its last usable measurement in *group_id*."""
        with self._lock:
            return {m: at for (g, m), at in self._measured_at.items() if g == group_id}

    # -- sessions -------------------------------------------------------------------
    def active_for(self, group_id: str) -> SyncSession | None:
        with self._lock:
            return next(
                (s for s in self._sessions.values() if s.group_id == group_id and s.state not in _ENDED),
                None,
            )

    def create(self, group_id: str, mode: Mode, point: Point) -> SyncSession:
        self.load_persisted()
        with self._lock:
            active = self.active_for(group_id)
            if active is not None:
                raise ControlError("session_active", "tuning in progress for this group")
            if self.pending_for(group_id) is not None:
                raise ControlError("restore_pending", "the group is still being restored from an earlier session")
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
            session.start()  # reads the group, writes nothing to it
        except BaseException:
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
        if s.state not in _ENDED and s.state != "restoring":
            try:
                s.tick()  # the group's playback, read every few seconds
            except Exception:
                logger.warning("Sync session %s tick failed", s.id, exc_info=True)
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
            close = s.id not in self._closed
            evict = now - self._finished_at[s.id] > FINISHED_TTL_S
            control = self._controls.get(s.id)
            if close:
                self._closed.add(s.id)
            if evict:
                for d in (self._next_restore, self._attempts, self._finished_at, self._controls):
                    d.pop(s.id, None)
                self._sessions.pop(s.id, None)
                self._closed.discard(s.id)
        if close and control is not None:
            _close(control)

    # -- restore after a restart -------------------------------------------------------
    def pending(self) -> list[dict[str, Any]]:
        """Restores left from before a restart that have not succeeded yet, with the last error."""
        self.load_persisted()
        with self._lock:
            return [
                {"session_id": sid, "group_id": str(e["group_id"]), "error": self._pending_errors.get(sid)}
                for sid, e in self._pending.items()
            ]

    def pending_for(self, group_id: str) -> dict[str, Any] | None:
        return next((p for p in self.pending() if p["group_id"] == group_id), None)

    def retry_pending(self, group_id: str) -> bool:
        """Drop the backoff of the group's pending restore so the next tick tries it at once."""
        with self._lock:
            sids = [sid for sid, e in self._pending.items() if str(e["group_id"]) == group_id]
            for sid in sids:
                self._pending_next[sid] = 0.0
            return bool(sids)

    def restore_persisted(self) -> None:
        """On boot: unmute what a session left muted and stop its click track if the group still
        plays it, then delete the entry; while Music Assistant is unreachable, retry with backoff."""
        self.load_persisted()
        with self._tick_lock:
            for sid in list(self._pending):
                self._try_pending(sid)

    def _try_pending(self, sid: str) -> None:
        with self._lock:
            found = self._pending.get(sid)
            entry = dict(found) if found is not None else None
        if entry is None:
            return
        group = str(entry["group_id"])
        with self._lock:
            since = self._pending_since.setdefault(sid, self._clock())
        control: GroupControl | None = None
        try:
            control = self._factory()
            muted = [str(m) for m in entry.get("muted") or []]
            for mid in list(muted):
                try:
                    control.mute(mid, False)
                except ControlError as exc:
                    # a player not listed yet (its daemon registers again after the restart) stays owed,
                    # until RESTORE_GIVE_UP_S after the first attempt
                    if exc.code != "member_gone" or self._clock() - since < RESTORE_GIVE_UP_S:
                        raise
                    logger.warning("Giving up unmuting %s after a restart: Music Assistant does not list it", mid)
                muted.remove(mid)
                entry["muted"] = list(muted)  # keep what already succeeded
            url = entry.get("clicker_url")
            if isinstance(url, str) and url:
                try:
                    playing = control.playback(group).plays(url)
                except ControlError as exc:
                    if exc.code != "member_gone":  # the group is gone: nothing plays on it
                        raise
                    playing = False
                if playing:
                    control.stop_group(group)
        except Exception as exc:
            with self._lock:
                n = self._pending_attempts.get(sid, 0)
                self._pending_attempts[sid] = n + 1
                self._pending_next[sid] = self._clock() + _backoff(n)
                self._pending_errors[sid] = str(getattr(exc, "code", None) or type(exc).__name__)
                if sid in self._pending:
                    self._pending[sid] = entry
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
            self._pending_errors.pop(sid, None)
            self._pending_since.pop(sid, None)
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

    def shutdown(self, *, restore: bool = True) -> None:
        """Cancel live sessions and try one restore each; a session that cannot be restored keeps
        its entry in the snapshot file, so the next boot restores it. With ``restore=False``
        (Music Assistant not connected) no network call is made at all."""
        self._stop.set()
        ticker = self._ticker
        if ticker is not None and ticker is not threading.current_thread():
            ticker.join(timeout=5.0)
        with self._tick_lock:
            with self._lock:
                sessions = list(self._sessions.values())
                controls = dict(self._controls)
            for s in sessions if restore else ():
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
            from sendspin_bridge.sync_tuning.click_track_host import FastApiClickTrackHost
            from sendspin_bridge.sync_tuning.ma_group_control import default_group_control

            _store = SessionStore(
                control_factory=default_group_control,
                host=FastApiClickTrackHost(),
                publish=_publish_to_hub,
                snapshot_dir=lambda: _default_snapshot_dir(),
            )
            _store.start_ticker()
        return _store


def shutdown_store() -> None:
    """Shut the store down if one was built (blocking: run it off the event loop). Without a
    Music Assistant connection it skips the restore; the snapshot file restores on the next boot."""
    from sendspin_bridge.services.music_assistant import ma_runtime_state

    store = _store
    if store is not None:
        store.shutdown(restore=ma_runtime_state.is_ma_connected())


def _default_snapshot_dir() -> Path:
    from sendspin_bridge import config as cfg

    return cfg.CONFIG_DIR


_background: list[threading.Thread] = []


def run_in_background(fn: Callable[[], None], name: str) -> threading.Thread:
    """Run blocking sync-tuning work on a daemon thread (never holds process exit)."""
    thread = threading.Thread(target=fn, name=name, daemon=True)
    _background[:] = [t for t in _background if t.is_alive()] + [thread]
    thread.start()
    return thread


def reset_store() -> None:
    """Wait for background work, then forget the singleton and the hub (tests)."""
    global _store, _hub
    for thread in list(_background):
        thread.join(timeout=5.0)
    _background.clear()
    store, _store, _hub = _store, None, None
    if store is not None:
        store.shutdown(restore=False)
