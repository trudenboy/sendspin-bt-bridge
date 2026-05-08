"""Lock ``BridgeDaemon`` overrides to their upstream ``SendspinDaemon`` signatures.

The bridge subclasses ``sendspin.daemon.daemon.SendspinDaemon`` to inject
WebSocket heartbeats and cross-cutting status hooks.  Whenever upstream
changes the signature of an overridden method, our override must change
too — otherwise the daemon crashes with ``TypeError`` on the very next
connection attempt.  This module checks the contract by spawning a
fresh Python process so that no other test's mocked ``sendspin`` module
in ``sys.modules`` can poison the import.

The post-2.69.0-rc.3 HAOS regression (``BridgeDaemon._run_server_initiated()
missing 1 required positional argument: 'static_delay_ms'``) shows what
this test catches: upstream dropped the parameter in 7.3.0 and our
subclass had to drop it too.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC_DIR = _REPO_ROOT / "src"


_PROBE = """
import inspect
import sys

from sendspin.daemon.daemon import SendspinDaemon
from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon


def _named(sig):
    return [name for name, _p in sig.parameters.items() if name != "self"]


u = _named(inspect.signature(SendspinDaemon._run_server_initiated))
o = _named(inspect.signature(BridgeDaemon._run_server_initiated))
if u != o:
    print(f"DRIFT upstream={u!r} override={o!r}", file=sys.stderr)
    sys.exit(1)
print("OK")
"""


def test_run_server_initiated_signature_matches_upstream():
    """sendspin 7.3.0 dropped ``static_delay_ms`` from ``SendspinDaemon``.
    Run the import-and-compare in a clean subprocess so other test
    modules' ``sys.modules`` mocks can't leak in via xdist scheduling.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(_SRC_DIR), "PATH": "/usr/bin:/bin"},
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"BridgeDaemon._run_server_initiated signature drifted from upstream.\n"
        f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
    )
