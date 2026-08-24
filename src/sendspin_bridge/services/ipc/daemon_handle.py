"""How a daemon subprocess is spawned so it cannot outlive the bridge.

Cleanup used to live entirely in the graceful stop path: a parent killed
outright — OOM in a 1 GB LXC, ``docker kill``, the Home Assistant Supervisor
watchdog — left one daemon per speaker running, each still holding its
Sendspin listen port, so the next start met EADDRINUSE.  The codebase knew
this happened; the stderr classifier carries a dedicated hint telling the
operator to hunt the lingering process down by hand.

The kernel will do it for us.  ``PR_SET_PDEATHSIG`` asks Linux to signal a
child when its parent dies, whatever killed the parent, which is the one
mechanism that survives SIGKILL.  It is per-thread and inherited across
``fork``, so it is armed in the child between fork and exec.
"""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)

__all__ = ["arm_parent_death_signal", "spawn_kwargs"]

#: ``prctl(2)`` option asking for a signal when the parent thread dies.
_PR_SET_PDEATHSIG = 1


def arm_parent_death_signal(sig: int = signal.SIGTERM) -> None:
    """Ask the kernel to send *sig* to this process when its parent dies.

    Called in the child between fork and exec.  Failures are deliberately
    silent: an unsupported platform or a missing libc symbol means the
    daemon simply keeps the old behaviour rather than failing to start.
    """
    if not sys.platform.startswith("linux"):
        return
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        if libc.prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0) != 0:
            return
    except Exception:  # a daemon that starts beats a daemon that reaps cleanly
        return

    # Between the fork and this call the parent may already have died, in
    # which case the death signal has been missed — check once.
    if os.getppid() == 1:
        os.kill(os.getpid(), sig)


def spawn_kwargs(*, death_signal: int = signal.SIGTERM) -> dict:
    """Keyword arguments every daemon spawn shares.

    Kept as one helper so a new spawn site cannot forget the part that stops
    orphans: there is nothing to remember beyond ``**spawn_kwargs()``.
    """
    if not sys.platform.startswith("linux"):
        return {}
    return {"preexec_fn": lambda: arm_parent_death_signal(death_signal)}
