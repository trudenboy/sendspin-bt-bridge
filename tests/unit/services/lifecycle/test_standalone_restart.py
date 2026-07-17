from __future__ import annotations

import os
import sys

import pytest

from sendspin_bridge.services.lifecycle import standalone_restart


def test_process_exit_probe_distinguishes_current_and_missing_pid():
    assert standalone_restart._process_has_exited(os.getpid()) is False
    assert standalone_restart._process_has_exited(999_999_999) is True


def test_relauncher_execs_bridge_after_old_process_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["standalone_restart", "4242"])
    monkeypatch.setattr(standalone_restart, "_process_has_exited", lambda pid: pid == 4242)
    executed: list[tuple[str, list[str], dict[str, str]]] = []

    def _fake_execvpe(executable, arguments, environment):
        executed.append((executable, arguments, environment))
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(standalone_restart.os, "execvpe", _fake_execvpe)

    with pytest.raises(RuntimeError, match="exec intercepted"):
        standalone_restart.main()

    assert executed[0][0] == sys.executable
    assert executed[0][1] == [sys.executable, "-m", "sendspin_bridge"]
