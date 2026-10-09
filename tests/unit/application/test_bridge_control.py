"""Restarting the bridge under whatever supervises it."""

from __future__ import annotations

import os
import signal
import sys
import threading

import pytest

import sendspin_bridge.application.bridge_control as control


@pytest.fixture
def restart_now(monkeypatch):
    """Run the delayed restart at once and wait for it."""
    monkeypatch.setattr(control.time, "sleep", lambda _s: None)

    def _restart() -> str:
        before = set(threading.enumerate())
        runtime = control.restart()
        for thread in set(threading.enumerate()) - before:
            if thread.name == "bridge-restart":
                thread.join(timeout=2)
        return runtime

    return _restart


@pytest.mark.parametrize("error", [ProcessLookupError("No such process"), PermissionError("not permitted")])
def test_docker_falls_back_to_its_own_pid_when_pid_1_refuses(monkeypatch, restart_now, error):
    monkeypatch.setattr(control, "detect_runtime", lambda: "docker")
    monkeypatch.setattr(control, "running_in_container", lambda: True)
    killed: list[int] = []

    def _fake_kill(pid, _sig):
        if pid == 1:
            raise error
        killed.append(pid)

    monkeypatch.setattr(control.os, "kill", _fake_kill)

    assert restart_now() == "docker"
    assert killed == [os.getpid()]


def test_a_direct_launch_arranges_its_successor_before_exiting(monkeypatch, restart_now):
    monkeypatch.setattr(control, "detect_runtime", lambda: "docker")
    monkeypatch.setattr(control, "running_in_container", lambda: False)
    popen_calls: list[tuple[list[str], dict]] = []
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(control.subprocess, "Popen", lambda cmd, **kw: popen_calls.append((cmd, kw)) or object())
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert restart_now() == "standalone"

    command, kwargs = popen_calls[0]
    assert command == [sys.executable, "-m", "sendspin_bridge.services.lifecycle.standalone_restart", str(os.getpid())]
    assert kwargs["start_new_session"] is True
    assert killed == [(os.getpid(), signal.SIGTERM)]


def test_the_endpoint_answers_before_the_restart(monkeypatch, api_client):
    calls: list[str] = []
    monkeypatch.setattr(control, "restart_override", lambda: calls.append("restart"))

    resp = api_client.post("/api/v1/bridge/restart")

    assert resp.status_code == 202
    assert calls == ["restart"]
