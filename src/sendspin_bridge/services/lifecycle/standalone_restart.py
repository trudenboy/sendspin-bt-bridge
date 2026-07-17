"""Detached relaunch helper for bridge instances started without a supervisor."""

from __future__ import annotations

import os
import sys
import time


def _process_has_exited(pid: int) -> bool:
    """Return true when *pid* is gone or only remains as a zombie."""
    try:
        with open(f"/proc/{pid}/stat") as stat_file:
            stat = stat_file.read()
    except FileNotFoundError:
        return True
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False
    closing_paren = stat.rfind(")")
    state = stat[closing_paren + 2 : closing_paren + 3] if closing_paren >= 0 else ""
    return state == "Z"


def main() -> int:
    """Wait for the old bridge PID, then replace this helper with a new bridge."""
    try:
        old_pid = int(sys.argv[1])
    except (IndexError, TypeError, ValueError):
        return 2
    if old_pid <= 1 or old_pid == os.getpid():
        return 2

    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if _process_has_exited(old_pid):
            os.execvpe(
                sys.executable,
                [sys.executable, "-m", "sendspin_bridge"],
                os.environ.copy(),
            )
        time.sleep(0.1)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
