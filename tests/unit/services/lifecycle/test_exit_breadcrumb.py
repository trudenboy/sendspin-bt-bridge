"""Tests for ``services.lifecycle.exit_breadcrumb.BreadcrumbStore``."""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING
from unittest import mock

import pytest

from sendspin_bridge.services.lifecycle.exit_breadcrumb import (
    BreadcrumbStore,
    PreviousRun,
    _derive_exit_kind,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> BreadcrumbStore:
    return BreadcrumbStore(tmp_path)


def _init(store: BreadcrumbStore, *, pid: int = 4711, version: str = "9.9.9") -> None:
    store.init_boot(
        bridge_version=version,
        pid=pid,
        runtime="test",
        hostname="test-host",
        demo_mode=False,
    )


def _write_exit(store: BreadcrumbStore, *, code: int, signal: int, schema: int = 1) -> None:
    """Simulate the s6 finish script writing exit.json."""
    store.exit_path.parent.mkdir(parents=True, exist_ok=True)
    store.exit_path.write_text(
        json.dumps(
            {
                "schema_version": schema,
                "exit_recorded_at": "2026-04-30T13:00:00Z",
                "exit_code": code,
                "exit_signal": signal,
                "wall_clock_unix": 1745944938,
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Roundtrip & rotation
# ---------------------------------------------------------------------------


def test_init_boot_creates_dir_and_writes_boot_json(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    _init(store)
    payload = json.loads(store.boot_path.read_text())
    assert payload["bridge_version"] == "9.9.9"
    assert payload["pid"] == 4711
    assert payload["host"]["runtime"] == "test"
    assert payload["host"]["hostname"] == "test-host"
    assert payload["shutdown_started"] is False
    assert payload["shutdown_completed"] is False
    assert payload["phase_timestamps"] == {}


def test_mark_phase_records_timestamp_and_message(store: BreadcrumbStore):
    _init(store)
    store.mark_phase("config", status="running", message="Loading configuration")
    payload = json.loads(store.boot_path.read_text())
    assert payload["last_phase"] == "config"
    assert payload["last_phase_status"] == "running"
    assert payload["last_message"] == "Loading configuration"
    assert "config" in payload["phase_timestamps"]


def test_mark_phase_coalesces_repeated_calls(store: BreadcrumbStore):
    _init(store)
    store.mark_phase("config", status="running", message="m1")
    mtime_after_first = store.boot_path.stat().st_mtime_ns
    # Same (phase, status, message) should not rewrite. Sleep skipped — mtime
    # comparison alone is fine because os.replace assigns a fresh inode.
    store.mark_phase("config", status="running", message="m1")
    mtime_after_second = store.boot_path.stat().st_mtime_ns
    assert mtime_after_first == mtime_after_second
    # Different status MUST rewrite.
    store.mark_phase("config", status="ready")
    payload = json.loads(store.boot_path.read_text())
    assert payload["last_phase_status"] == "ready"


def test_mark_phase_rewrites_when_message_changes(store: BreadcrumbStore):
    """Repeated (phase, status) with a *different* message must rewrite.

    Regression: ``last_message`` is the diagnostics surface that
    describes what the bridge was last doing. Coalescing by
    ``(phase, status)`` alone would drop later messages and leave
    stale forensic data in ``boot.json``.
    """
    _init(store)
    store.mark_phase("config", status="running", message="opening config.json")
    payload = json.loads(store.boot_path.read_text())
    assert payload["last_message"] == "opening config.json"
    # Same phase+status, different message — must update.
    store.mark_phase("config", status="running", message="parsing config.json")
    payload = json.loads(store.boot_path.read_text())
    assert payload["last_message"] == "parsing config.json"


def test_init_boot_rotates_existing_files(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    _init(store, pid=1)
    store.mark_phase("config")
    _write_exit(store, code=0, signal=15)

    # Second boot (simulated) — same dir, fresh store.
    store2 = BreadcrumbStore(tmp_path)
    store2.init_boot(
        bridge_version="9.9.9",
        pid=2,
        runtime="test",
        hostname="test-host",
        demo_mode=False,
    )
    assert store2.boot_prev_path.exists()
    assert store2.exit_prev_path.exists()
    # The new boot.json must reflect the new PID, not the prior.
    new_boot = json.loads(store2.boot_path.read_text())
    assert new_boot["pid"] == 2
    prev_boot = json.loads(store2.boot_prev_path.read_text())
    assert prev_boot["pid"] == 1


def test_read_previous_returns_none_on_first_boot(store: BreadcrumbStore):
    assert store.read_previous() is None


def test_read_previous_pairs_boot_and_exit(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    _init(store, pid=42)
    store.mark_phase("config", status="running", message="Loading")
    store.mark_phase("ready", status="running", message="ok")
    store.mark_shutdown_started()
    store.mark_shutdown_complete()
    _write_exit(store, code=0, signal=15)

    # Rotate via a fresh init_boot.
    store2 = BreadcrumbStore(tmp_path)
    store2.init_boot(
        bridge_version="9.9.9",
        pid=99,
        runtime="test",
        hostname="test-host",
        demo_mode=False,
    )
    prev = store2.read_previous()
    assert isinstance(prev, PreviousRun)
    assert prev.exit_kind == "graceful"
    assert prev.pid == 42
    assert prev.last_phase == "ready"
    assert prev.exit_code == 0
    assert prev.exit_signal == 15


# ---------------------------------------------------------------------------
# Corruption / schema / read-only resilience
# ---------------------------------------------------------------------------


def test_read_previous_handles_corrupt_boot(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    store.boot_prev_path.parent.mkdir(parents=True, exist_ok=True)
    store.boot_prev_path.write_text("{not valid json", encoding="utf-8")
    prev = store.read_previous()
    assert prev is not None
    assert prev.exit_kind == "unknown_corrupt"


def test_read_previous_unknown_schema(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    store.boot_prev_path.parent.mkdir(parents=True, exist_ok=True)
    store.boot_prev_path.write_text(json.dumps({"schema_version": 999, "pid": 1}), encoding="utf-8")
    prev = store.read_previous()
    assert prev is not None
    assert prev.exit_kind == "unknown_schema"


def test_init_boot_swallows_oserror(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    with mock.patch("os.replace", side_effect=PermissionError("ro fs")):
        # Must not raise; mark_phase afterwards must also be a no-op.
        store.init_boot(
            bridge_version="9.9.9",
            pid=1,
            runtime="test",
            hostname="h",
            demo_mode=False,
        )
        store.mark_phase("config")
        store.mark_shutdown_complete()


def test_mark_phase_before_init_boot_is_noop(store: BreadcrumbStore):
    # No init_boot — mark_phase must silently no-op.
    store.mark_phase("config")
    assert not store.boot_path.exists()


# ---------------------------------------------------------------------------
# exit_kind matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("boot_present", "completed", "started", "exit_present", "code", "signal", "expected"),
    [
        (False, False, False, False, None, None, "unknown"),
        (False, False, False, True, 0, 15, "unknown_no_boot"),
        (True, True, True, True, 0, 15, "graceful"),
        (True, True, True, True, 0, 0, "graceful"),
        (True, True, True, True, 137, 9, "graceful_with_anomaly"),
        (True, False, False, True, 137, 9, "sigkill"),
        (True, False, False, True, 1, 0, "crash_unhandled_exception"),
        (True, False, False, True, 0, 15, "terminated_during_startup"),
        (True, False, False, False, None, None, "unknown_no_finish"),
        (True, False, True, True, 0, 15, "shutdown_interrupted"),
    ],
)
def test_derive_exit_kind_matrix(boot_present, completed, started, exit_present, code, signal, expected):
    assert (
        _derive_exit_kind(
            boot_present=boot_present,
            shutdown_completed=completed,
            shutdown_started=started,
            exit_present=exit_present,
            exit_code=code,
            exit_signal=signal,
        )
        == expected
    )


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_mark_phase_keeps_file_valid(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    _init(store)

    def _worker(prefix: str, count: int) -> None:
        for i in range(count):
            store.mark_phase(f"{prefix}-{i}", status="running")

    threads = [threading.Thread(target=_worker, args=(f"t{n}", 25)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    payload = json.loads(store.boot_path.read_text())
    assert payload["schema_version"] == 1
    # 4 threads x 25 unique phases each -> 100 entries.
    assert len(payload["phase_timestamps"]) == 100


# ---------------------------------------------------------------------------
# Double-instance detection
# ---------------------------------------------------------------------------


def test_warn_if_pid_collision_returns_none_when_pid_dead(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    store.boot_prev_path.parent.mkdir(parents=True, exist_ok=True)
    store.boot_prev_path.write_text(
        json.dumps({"schema_version": 1, "pid": 1}),
        encoding="utf-8",
    )
    with mock.patch(
        "sendspin_bridge.services.lifecycle.exit_breadcrumb._pid_alive",
        return_value=False,
    ):
        assert store.warn_if_pid_collision(os.getpid()) is None


def test_warn_if_pid_collision_returns_string_when_pid_alive(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    store.boot_prev_path.parent.mkdir(parents=True, exist_ok=True)
    store.boot_prev_path.write_text(
        json.dumps({"schema_version": 1, "pid": 12345}),
        encoding="utf-8",
    )
    with mock.patch(
        "sendspin_bridge.services.lifecycle.exit_breadcrumb._pid_alive",
        return_value=True,
    ):
        msg = store.warn_if_pid_collision(os.getpid())
        assert msg is not None
        assert "pid=12345" in msg


def test_warn_if_pid_collision_ignores_self(tmp_path: Path):
    store = BreadcrumbStore(tmp_path)
    me = os.getpid()
    store.boot_prev_path.parent.mkdir(parents=True, exist_ok=True)
    store.boot_prev_path.write_text(
        json.dumps({"schema_version": 1, "pid": me}),
        encoding="utf-8",
    )
    assert store.warn_if_pid_collision(me) is None
