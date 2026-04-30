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
@pytest.mark.parametrize(
    ("raw_exit_code", "signal", "expected_code"),
    [
        # Already-normalised exit code → passes through unchanged.
        (137, 9, 137),
        # s6 sentinel for SIGKILL → must normalise to 128+9 = 137.
        (256, 9, 137),
        # s6 sentinel for SIGTERM → must normalise to 128+15 = 143.
        (256, 15, 143),
        # Plain non-zero exit (no signal) → unchanged.
        (1, 0, 1),
        # 256 with no signal (shouldn't happen in practice but the
        # guard ``[ $SIGNAL -gt 0 ]`` must keep it as-is).
        (256, 0, 256),
    ],
)
def test_finish_writes_valid_exit_json(tmp_path: Path, raw_exit_code: int, signal: int, expected_code: int):
    """Execute the in-tree ``finish`` script and verify its breadcrumb output.

    Sources the actual file at ``rootfs/.../sendspin/finish`` rather than a
    duplicated copy so any drift in the production script is caught
    immediately.  Stubs out ``sleep`` so the supervisor restart-delay
    branch returns instantly during tests, and bypasses the
    ``/command/with-contenv`` shebang (not present outside the container)
    by sourcing under a plain bash invocation.
    """
    env = {**os.environ, "CONFIG_DIR": str(tmp_path)}
    # ``sleep() { :; }`` shadows both the bash builtin and the binary,
    # so the script's ``sleep 5`` on the unexpected-exit branch is a
    # no-op in tests but unchanged in production.
    wrapper = f"sleep() {{ :; }}; . {FINISH_SCRIPT}"
    subprocess.run(
        ["bash", "-c", wrapper, "_", str(raw_exit_code), str(signal)],
        check=True,
        env=env,
        timeout=10,
    )

    exit_path = tmp_path / "breadcrumbs" / "exit.json"
    assert exit_path.exists(), "finish script should have written exit.json"
    payload = json.loads(exit_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["exit_code"] == expected_code
    assert payload["exit_signal"] == signal
    assert payload["exit_recorded_at"]


@pytest.mark.skipif(not FINISH_SCRIPT.exists(), reason="s6 finish script not present in checkout")
def test_finish_script_passes_bash_n(tmp_path: Path):
    """Defence in depth: the file in tree must parse cleanly under bash -n."""
    subprocess.run(["bash", "-n", str(FINISH_SCRIPT)], check=True, timeout=10)


@pytest.mark.skipif(not FINISH_SCRIPT.exists(), reason="s6 finish script not present in checkout")
def test_finish_script_contains_exit_code_normalization():
    """Cheap explicit guard: the production script must keep the 256→128+signal
    normalization. ``test_finish_writes_valid_exit_json`` already asserts the
    behaviour by sourcing the real file, but this string-level assertion
    surfaces a much clearer failure if the line is accidentally deleted in
    a refactor.
    """
    src = FINISH_SCRIPT.read_text()
    assert "EXIT_CODE=$((128 + SIGNAL))" in src, "rootfs/.../finish lost the s6 exit-code normalization line"
    assert "-eq 256" in src, "rootfs/.../finish lost the 256 sentinel guard"


# ---------------------------------------------------------------------------
# Regression: the warning must describe the IMMEDIATELY PRIOR run, not the
# one before that.  Original wiring called read_previous() *before*
# init_boot() rotated the files, so the warning either reported nothing
# (first crash) or a run two restarts old.
# ---------------------------------------------------------------------------


def test_read_previous_after_init_boot_describes_immediate_prior_run(tmp_path: Path):
    sys.path.insert(0, str(SRC_ROOT))
    try:
        from sendspin_bridge.services.lifecycle.exit_breadcrumb import BreadcrumbStore
    finally:
        sys.path.pop(0)

    # --- Boot 1: writes boot.json + exit.json (simulating s6 finish) ---
    store1 = BreadcrumbStore(tmp_path)
    store1.init_boot(
        bridge_version="boot-1",
        pid=111,
        runtime="test",
        hostname="ci",
        demo_mode=False,
    )
    store1.mark_phase("config", message="cfg")
    store1.mark_phase("runtime", message="rt")
    breadcrumbs = tmp_path / "breadcrumbs"
    (breadcrumbs / "exit.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "exit_recorded_at": "2026-04-30T10:00:00Z",
                "exit_code": 137,
                "exit_signal": 9,
                "wall_clock_unix": 1745944938,
            }
        ),
        encoding="utf-8",
    )

    # --- Boot 2: rotates boot.json -> boot.prev.json, then reads ---
    store2 = BreadcrumbStore(tmp_path)
    store2.init_boot(
        bridge_version="boot-2",
        pid=222,
        runtime="test",
        hostname="ci",
        demo_mode=False,
    )
    prev = store2.read_previous()
    assert prev is not None
    # Must describe boot 1, not nothing.  Original wiring read
    # boot.prev.json BEFORE init_boot rotated it — at this point
    # boot.prev.json didn't exist yet, so read_previous() returned
    # None and the WARNING line was never emitted.
    assert prev.exit_kind == "sigkill"
    assert prev.bridge_version == "boot-1"
    assert prev.pid == 111
    assert prev.last_phase == "runtime"
