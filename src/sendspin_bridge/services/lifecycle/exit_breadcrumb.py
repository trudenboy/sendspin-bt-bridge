"""Persist boot/exit breadcrumbs across restarts to help diagnose ungraceful exits.

The bridge's :class:`_RingLogHandler` is in-memory only — it is wiped on
every restart, so when a user reports "the addon keeps restarting" we
have nothing to point at.  This module owns two complementary breadcrumb
files under ``<CONFIG_DIR>/breadcrumbs/`` that survive restarts:

* ``boot.json`` — written incrementally by Python during startup and on
  graceful shutdown.  Captures *what the bridge was doing* (which phase
  it reached, which version, PID, demo mode, etc.).
* ``exit.json`` — written by the s6 ``finish`` script (rootfs/etc/...).
  Captures *how the supervised process died* — exit code, signal,
  wall-clock timestamp.  Authoritative for SIGKILL / OOM cases where
  Python never gets a chance to run an exit handler.

On the next boot, :meth:`BreadcrumbStore.init_boot` rotates both files
to ``*.prev.json`` and writes a fresh ``boot.json``.
:meth:`BreadcrumbStore.read_previous` then pairs the rotated files into
a derived ``exit_kind`` (``graceful`` / ``sigkill`` /
``crash_unhandled_exception`` / ``terminated_during_startup`` /
``unknown_no_finish`` / ``unknown_corrupt`` / ``unknown_schema``) so a
single WARNING log line and a "LAST RUN SUMMARY" diagnostics section
can describe the prior run.

All write paths are best-effort: any :class:`OSError` is logged at
DEBUG and swallowed so a read-only or full filesystem cannot prevent
the bridge from starting.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_BREADCRUMBS_DIRNAME = "breadcrumbs"
_BOOT_FILENAME = "boot.json"
_EXIT_FILENAME = "exit.json"
_BOOT_PREV_FILENAME = "boot.prev.json"
_EXIT_PREV_FILENAME = "exit.prev.json"


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _pid_alive(pid: int) -> bool:
    """Best-effort check whether *pid* names a still-running process."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive.
        return True
    except OSError:
        return False
    return True


@dataclass
class PreviousRun:
    """Derived summary of the prior run, paired from boot.prev + exit.prev."""

    exit_kind: str
    bridge_version: str | None = None
    pid: int | None = None
    started_at: str | None = None
    last_phase: str | None = None
    last_phase_status: str | None = None
    last_message: str | None = None
    runtime: str | None = None
    demo_mode: bool | None = None
    shutdown_started: bool = False
    shutdown_completed: bool = False
    exit_recorded_at: str | None = None
    exit_code: int | None = None
    exit_signal: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BreadcrumbStore:
    """Read/write breadcrumb files under ``<config_dir>/breadcrumbs/``.

    The store is best-effort: failures while writing or reading are
    logged at DEBUG and never propagate.  A single :class:`threading.Lock`
    serializes rewrites so concurrent ``mark_phase`` calls from the
    asyncio loop and Flask threads cannot tear the file.
    """

    def __init__(self, config_dir: Path | str):
        self._config_dir = Path(config_dir)
        self._dir = self._config_dir / _BREADCRUMBS_DIRNAME
        self._boot_path = self._dir / _BOOT_FILENAME
        self._exit_path = self._dir / _EXIT_FILENAME
        self._boot_prev_path = self._dir / _BOOT_PREV_FILENAME
        self._exit_prev_path = self._dir / _EXIT_PREV_FILENAME
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}
        self._last_phase_key: tuple[str, str, str] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def init_boot(
        self,
        *,
        bridge_version: str,
        pid: int,
        runtime: str,
        hostname: str,
        demo_mode: bool,
    ) -> None:
        """Rotate prior breadcrumbs and write a fresh ``boot.json``.

        Rotation happens here (not on shutdown) because this is the
        only moment when prior-run files are guaranteed to be final.
        Any write/rotate error is caught and logged at DEBUG so a
        read-only filesystem or disk-full condition cannot prevent
        bridge startup.
        """
        with self._lock:
            self._ensure_dir()
            self._rotate_locked()
            now = _utcnow_iso()
            self._state = {
                "schema_version": _SCHEMA_VERSION,
                "bridge_version": bridge_version,
                "pid": int(pid),
                "started_at": now,
                "host": {"runtime": runtime, "hostname": hostname},
                "demo_mode": bool(demo_mode),
                "last_phase": None,
                "last_phase_status": None,
                "last_message": None,
                "phase_timestamps": {},
                "shutdown_started": False,
                "shutdown_completed": False,
                "double_instance_warned": False,
            }
            self._last_phase_key = None
            self._write_state_locked()

    def mark_phase(
        self,
        phase: str,
        status: str = "running",
        message: str = "",
    ) -> None:
        """Record a phase transition, rewriting ``boot.json`` if changed.

        Coalesces consecutive calls with the same ``(phase, status)`` —
        startup paths sometimes call into the lifecycle publisher more
        than once per phase, and we don't want N redundant rewrites.
        """
        if not phase:
            return
        with self._lock:
            if not self._state:
                # ``init_boot`` was never called (e.g. read-only fs at
                # startup); silently accept the call and skip the write.
                return
            already_seen = phase in self._state.get("phase_timestamps", {})
            current_message = self._state.get("last_message") or ""
            new_message = message or current_message
            # Coalesce only when the entire (phase, status, message)
            # tuple matches what we already have — including the
            # message, because that's what the diagnostics surface
            # uses to describe what the bridge was last doing.
            key = (str(phase), str(status), str(new_message))
            if already_seen and key == self._last_phase_key:
                return
            self._last_phase_key = key
            self._state["last_phase"] = phase
            self._state["last_phase_status"] = status
            if message:
                self._state["last_message"] = message
            self._state.setdefault("phase_timestamps", {})[phase] = _utcnow_iso()
            self._write_state_locked()

    def mark_shutdown_started(self) -> None:
        """Flip the ``shutdown_started`` flag and rewrite ``boot.json``."""
        with self._lock:
            if not self._state:
                return
            if self._state.get("shutdown_started"):
                return
            self._state["shutdown_started"] = True
            self._state.setdefault("phase_timestamps", {})["shutdown_started"] = _utcnow_iso()
            self._write_state_locked()

    def mark_shutdown_complete(self) -> None:
        """Flip the ``shutdown_completed`` flag and rewrite ``boot.json``."""
        with self._lock:
            if not self._state:
                return
            self._state["shutdown_completed"] = True
            self._state["shutdown_started"] = True
            self._state.setdefault("phase_timestamps", {})["shutdown_completed"] = _utcnow_iso()
            self._write_state_locked()

    def read_previous(self) -> PreviousRun | None:
        """Pair ``boot.prev.json`` and ``exit.prev.json`` into a summary.

        Returns ``None`` only when both files are absent (first ever
        boot of this CONFIG_DIR).  Corrupt files yield a ``PreviousRun``
        with ``exit_kind="unknown_corrupt"`` so callers always have a
        well-typed result.  Never raises.
        """
        boot = self._safe_load(self._boot_prev_path)
        exit_ = self._safe_load(self._exit_prev_path)
        if boot is None and exit_ is None:
            if not self._boot_prev_path.exists() and not self._exit_prev_path.exists():
                return None
            # At least one file existed but failed to parse.
            return PreviousRun(exit_kind="unknown_corrupt")

        notes: list[str] = []
        boot_schema = boot.get("schema_version") if isinstance(boot, dict) else None
        exit_schema = exit_.get("schema_version") if isinstance(exit_, dict) else None
        if (boot is not None and boot_schema != _SCHEMA_VERSION) or (
            exit_ is not None and exit_schema != _SCHEMA_VERSION
        ):
            return PreviousRun(
                exit_kind="unknown_schema",
                notes=[f"boot_schema={boot_schema}, exit_schema={exit_schema}"],
            )

        boot = boot or {}
        exit_ = exit_ or {}

        shutdown_started = bool(boot.get("shutdown_started"))
        shutdown_completed = bool(boot.get("shutdown_completed"))
        exit_present = bool(exit_)
        exit_code = exit_.get("exit_code") if exit_present else None
        exit_signal = exit_.get("exit_signal") if exit_present else None
        try:
            exit_code_int: int | None = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            exit_code_int = None
            notes.append(f"unparseable exit_code={exit_code!r}")
        try:
            exit_signal_int: int | None = int(exit_signal) if exit_signal is not None else None
        except (TypeError, ValueError):
            exit_signal_int = None
            notes.append(f"unparseable exit_signal={exit_signal!r}")

        kind = _derive_exit_kind(
            boot_present=bool(boot),
            shutdown_completed=shutdown_completed,
            shutdown_started=shutdown_started,
            exit_present=exit_present,
            exit_code=exit_code_int,
            exit_signal=exit_signal_int,
        )

        host = boot.get("host") if isinstance(boot.get("host"), dict) else {}

        return PreviousRun(
            exit_kind=kind,
            bridge_version=boot.get("bridge_version"),
            pid=int(boot["pid"]) if isinstance(boot.get("pid"), int) else None,
            started_at=boot.get("started_at"),
            last_phase=boot.get("last_phase"),
            last_phase_status=boot.get("last_phase_status"),
            last_message=boot.get("last_message"),
            runtime=host.get("runtime") if isinstance(host, dict) else None,
            demo_mode=bool(boot.get("demo_mode")) if "demo_mode" in boot else None,
            shutdown_started=shutdown_started,
            shutdown_completed=shutdown_completed,
            exit_recorded_at=exit_.get("exit_recorded_at") if exit_present else None,
            exit_code=exit_code_int,
            exit_signal=exit_signal_int,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ensure_dir(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug("breadcrumbs: mkdir(%s) failed: %s", self._dir, exc)

    def _rotate_locked(self) -> None:
        """Move existing boot/exit → *.prev.json. Best-effort."""
        for src, dst in (
            (self._boot_path, self._boot_prev_path),
            (self._exit_path, self._exit_prev_path),
        ):
            try:
                if src.exists():
                    os.replace(src, dst)
            except OSError as exc:
                logger.debug("breadcrumbs: rotate %s -> %s failed: %s", src, dst, exc)

    def _write_state_locked(self) -> None:
        try:
            payload = json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True)
        except (TypeError, ValueError) as exc:
            logger.debug("breadcrumbs: serialize boot.json failed: %s", exc)
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug("breadcrumbs: mkdir(%s) for boot.json failed: %s", self._dir, exc)
            return

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._dir,
                prefix=".boot.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_path, self._boot_path)
        except OSError as exc:
            logger.debug("breadcrumbs: write boot.json failed: %s", exc)
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _safe_load(self, path: Path) -> dict[str, Any] | None:
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("breadcrumbs: load %s failed: %s", path, exc)
            return None
        if isinstance(data, dict):
            return data
        logger.debug("breadcrumbs: %s contains non-object payload, ignoring", path)
        return None

    # Test / debugging helpers
    @property
    def boot_path(self) -> Path:
        return self._boot_path

    @property
    def exit_path(self) -> Path:
        return self._exit_path

    @property
    def boot_prev_path(self) -> Path:
        return self._boot_prev_path

    @property
    def exit_prev_path(self) -> Path:
        return self._exit_prev_path

    def warn_if_pid_collision(self, current_pid: int) -> str | None:
        """Best-effort detection of a second bridge against the same CONFIG_DIR.

        If ``boot.prev.json`` references a PID that still appears alive
        (and isn't this process), return a one-line warning string.
        Callers may log it and continue — we don't try to coordinate.
        """
        boot = self._safe_load(self._boot_prev_path)
        if not isinstance(boot, dict):
            return None
        pid = boot.get("pid")
        if not isinstance(pid, int) or pid <= 0 or pid == int(current_pid):
            return None
        if not _pid_alive(pid):
            return None
        return (
            f"Another sendspin bridge appears to be running with pid={pid} "
            f"against this CONFIG_DIR ({self._config_dir}); breadcrumbs may interleave"
        )


def _derive_exit_kind(
    *,
    boot_present: bool,
    shutdown_completed: bool,
    shutdown_started: bool,
    exit_present: bool,
    exit_code: int | None,
    exit_signal: int | None,
) -> str:
    """Map (boot.prev, exit.prev) state into a stable ``exit_kind`` token.

    Ordering of clauses matters — earlier clauses are checked first.
    """
    if not boot_present and not exit_present:
        return "unknown"
    if not boot_present:
        return "unknown_no_boot"
    if shutdown_completed:
        if exit_present and (exit_code or 0) == 0 and (exit_signal or 0) in (0, 15):
            return "graceful"
        return "graceful_with_anomaly"
    if not exit_present:
        # Python recorded a boot but the s6 finish script never wrote an
        # exit record.  Possible kernel panic, host VM force-stop, or
        # the bridge runs without s6 (unit-test mode, dev laptop).
        return "unknown_no_finish"
    if exit_signal == 9:
        return "sigkill"
    if exit_signal == 15 and not shutdown_started:
        return "terminated_during_startup"
    if (exit_code or 0) != 0 and (exit_signal or 0) == 0:
        return "crash_unhandled_exception"
    if shutdown_started and not shutdown_completed:
        return "shutdown_interrupted"
    return "unknown"
