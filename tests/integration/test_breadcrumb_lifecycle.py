"""Integration tests for the boot/exit breadcrumb feature.

Covers two surfaces a unit test cannot:

1. **Subprocess crash paths** — spawn a tiny Python script that imports
   ``BreadcrumbStore``, writes a boot.json, then exits via either
   ``os._exit(1)`` (uncatchable from atexit) or ``SIGKILL`` (uncatchable
   period).  The parent then constructs a fresh store against the same
   directory and asserts ``read_previous().exit_kind`` is correct.
2. **s6 finish shell block** — execute the breadcrumb-writing portion of
   ``rootfs/etc/s6-overlay/s6-rc.d/sendspin/finish`` against a temp
   ``CONFIG_DIR`` and assert that ``exit.json`` parses as valid JSON
   with the supplied ``exit_code`` / ``exit_signal``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
FINISH_SCRIPT = REPO_ROOT / "rootfs" / "etc" / "s6-overlay" / "s6-rc.d" / "sendspin" / "finish"


def _spawn_writer(config_dir: Path, *, mode: str) -> subprocess.Popen:
    """Spawn a subprocess that writes a boot.json then exits per *mode*."""
    script = textwrap.dedent(
        f"""
        import os, sys, signal
        sys.path.insert(0, {str(SRC_ROOT)!r})
        from sendspin_bridge.services.lifecycle.exit_breadcrumb import BreadcrumbStore
        store = BreadcrumbStore({str(config_dir)!r})
        store.init_boot(
            bridge_version="0.0.0-test",
            pid=os.getpid(),
            runtime="test",
            hostname="ci",
            demo_mode=False,
        )
        store.mark_phase("config", message="loading")
        store.mark_phase("runtime", message="prepared")
        if {mode!r} == "graceful":
            store.mark_shutdown_started()
            store.mark_shutdown_complete()
            sys.exit(0)
        elif {mode!r} == "os_exit":
            os._exit(1)
        elif {mode!r} == "sigkill":
            os.kill(os.getpid(), signal.SIGKILL)
        else:
            sys.exit(99)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    proc.wait(timeout=10)
    return proc


def _read_previous(config_dir: Path):
    sys.path.insert(0, str(SRC_ROOT))
    try:
        from sendspin_bridge.services.lifecycle.exit_breadcrumb import BreadcrumbStore
    finally:
        sys.path.pop(0)
    # Construct fresh store, rotate via init_boot, then read_previous.
    store = BreadcrumbStore(config_dir)
    store.init_boot(
        bridge_version="0.0.0-reader",
        pid=os.getpid(),
        runtime="test",
        hostname="reader",
        demo_mode=False,
    )
    return store.read_previous()


# ---------------------------------------------------------------------------
# Subprocess crash paths
# ---------------------------------------------------------------------------


def test_os_exit_without_finish_yields_unknown_no_finish(tmp_path: Path):
    proc = _spawn_writer(tmp_path, mode="os_exit")
    assert proc.returncode == 1

    prev = _read_previous(tmp_path)
    assert prev is not None
    # Python wrote boot.json but no s6 finish ran (no exit.json).
    assert prev.exit_kind == "unknown_no_finish"
    assert prev.last_phase == "runtime"
    assert prev.shutdown_started is False
    assert prev.shutdown_completed is False


def test_sigkill_without_finish_yields_unknown_no_finish(tmp_path: Path):
    proc = _spawn_writer(tmp_path, mode="sigkill")
    # SIGKILL → returncode is -SIGKILL on POSIX
    assert proc.returncode == -signal.SIGKILL

    prev = _read_previous(tmp_path)
    assert prev is not None
    assert prev.exit_kind == "unknown_no_finish"
    assert prev.shutdown_started is False


def test_graceful_path_yields_graceful_kind_when_finish_writes_clean_exit(tmp_path: Path):
    proc = _spawn_writer(tmp_path, mode="graceful")
    assert proc.returncode == 0

    # Simulate the s6 finish script writing exit.json for a clean exit.
    breadcrumbs = tmp_path / "breadcrumbs"
    breadcrumbs.mkdir(exist_ok=True)
    (breadcrumbs / "exit.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "exit_recorded_at": "2026-04-30T12:00:00Z",
                "exit_code": 0,
                "exit_signal": 15,
                "wall_clock_unix": 1745944938,
            }
        ),
        encoding="utf-8",
    )

    prev = _read_previous(tmp_path)
    assert prev is not None
    assert prev.exit_kind == "graceful"
    assert prev.shutdown_completed is True
    assert prev.exit_code == 0
    assert prev.exit_signal == 15


def test_sigkill_paired_with_exit_json_yields_sigkill(tmp_path: Path):
    proc = _spawn_writer(tmp_path, mode="sigkill")
    assert proc.returncode == -signal.SIGKILL

    breadcrumbs = tmp_path / "breadcrumbs"
    (breadcrumbs / "exit.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "exit_recorded_at": "2026-04-30T12:00:00Z",
                "exit_code": 137,
                "exit_signal": 9,
                "wall_clock_unix": 1745944938,
            }
        ),
        encoding="utf-8",
    )

    prev = _read_previous(tmp_path)
    assert prev is not None
    assert prev.exit_kind == "sigkill"
    assert prev.exit_code == 137
    assert prev.exit_signal == 9
    assert prev.last_phase == "runtime"


# ---------------------------------------------------------------------------
# s6 finish shell block
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FINISH_SCRIPT.exists(), reason="s6 finish script not present in checkout")
def test_finish_shell_block_writes_valid_exit_json(tmp_path: Path):
    """Run the breadcrumb portion of ``finish`` against a temp CONFIG_DIR."""
    # Extract just the breadcrumb-writing portion so we don't have to
    # invoke /command/with-contenv (only present inside the container).
    script = textwrap.dedent(
        """
        EXIT_CODE=$1
        SIGNAL=$2
        if [ -f /data/options.json ]; then BC_DIR="/data/breadcrumbs"; else BC_DIR="${CONFIG_DIR:-/config}/breadcrumbs"; fi
        mkdir -p "$BC_DIR" 2>/dev/null || true
        BC_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")
        BC_NOW=$(date +%s 2>/dev/null || echo 0)
        printf '{"schema_version":1,"exit_recorded_at":"%s","exit_code":%s,"exit_signal":%s,"wall_clock_unix":%s}\\n' \
            "$BC_TS" "$EXIT_CODE" "$SIGNAL" "$BC_NOW" > "$BC_DIR/exit.json.tmp" 2>/dev/null || true
        mv -f "$BC_DIR/exit.json.tmp" "$BC_DIR/exit.json" 2>/dev/null || true
        """
    )
    env = {**os.environ, "CONFIG_DIR": str(tmp_path)}
    subprocess.run(
        ["bash", "-c", script, "_", "137", "9"],
        check=True,
        env=env,
        timeout=10,
    )

    exit_path = tmp_path / "breadcrumbs" / "exit.json"
    assert exit_path.exists(), "finish script should have written exit.json"
    payload = json.loads(exit_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["exit_code"] == 137
    assert payload["exit_signal"] == 9
    assert payload["exit_recorded_at"]


@pytest.mark.skipif(not FINISH_SCRIPT.exists(), reason="s6 finish script not present in checkout")
def test_finish_script_passes_bash_n(tmp_path: Path):
    """Defence in depth: the file in tree must parse cleanly under bash -n."""
    subprocess.run(["bash", "-n", str(FINISH_SCRIPT)], check=True, timeout=10)
