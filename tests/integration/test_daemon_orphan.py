"""A daemon must not outlive the bridge that spawned it.

Cleanup lived entirely in the graceful stop path, so a parent killed outright
— OOM in a 1 GB LXC, `docker kill`, the HA Supervisor watchdog — left one
daemon per speaker alive, each still holding its Sendspin listen port. The
codebase already knew: the stderr classifier carries a dedicated EADDRINUSE
hint telling operators to hunt the lingering process down with `lsof`.

This test spawns a real intermediate process, has it spawn a child through
the same helper the bridge uses, then kills the intermediate outright and
watches whether the grandchild goes with it.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux-only")

_PARENT_SCRIPT = """
import asyncio, sys
from sendspin_bridge.services.ipc.daemon_handle import spawn_kwargs

async def main():
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(300)",
        **spawn_kwargs(),
    )
    print(child.pid, flush=True)
    await asyncio.sleep(300)

asyncio.run(main())
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_a_killed_parent_takes_its_daemon_with_it():
    parent = subprocess.Popen(
        [sys.executable, "-c", _PARENT_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": "src"},
    )
    try:
        line = parent.stdout.readline()
        assert line.strip().isdigit(), f"intermediate never reported a child pid: {line!r}"
        child_pid = int(line.strip())
        assert _alive(child_pid)

        parent.send_signal(signal.SIGKILL)
        parent.wait(timeout=10)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not _alive(child_pid):
                break
            time.sleep(0.1)

        assert not _alive(child_pid), "the daemon outlived the bridge that spawned it"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
