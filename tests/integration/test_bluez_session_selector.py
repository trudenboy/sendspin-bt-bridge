"""Selector-branch coverage for BluezSession with real subprocesses.

``FakeBluez`` declares ``supports_select = False`` so unit tests exercise
the clock-driven ``readline()`` fallback; the selector branch therefore
needs at least one test driving a real short-lived subprocess.  ``cat``
provides that everywhere (it echoes stdin to stdout, same pipe + selector
mechanics, no bluetoothd dependency); a second smoke test runs against a
real ``bluetoothctl`` when a live bluetoothd is present (dev stand /
production hosts, not CI containers).
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from sendspin_bridge.bluetooth.bluez import BluezControl, LineKind

_PROMPT_TOKEN = "]# "


class _CatSpawner:
    """Real-subprocess spawner driving ``cat`` instead of bluetoothctl."""

    supports_select = True

    def run(self, argv, input=None, timeout=None):  # pragma: no cover - unused here
        raise NotImplementedError

    def popen(self, argv):
        return subprocess.Popen(
            ["cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


def test_selector_branch_reads_real_subprocess_output():
    control = BluezControl(spawner=_CatSpawner())
    with control.session() as session:
        session.send("hello-bluez-session")
        seen = list(session.lines(deadline=session._now() + 5.0))
    texts = [line.text for line in seen]
    assert any("hello-bluez-session" in text for text in texts)
    assert all(line.kind is LineKind.CONTENT for line in seen)


def test_real_bluetoothctl_session_when_daemon_present():
    if shutil.which("bluetoothctl") is None:
        pytest.skip("bluetoothctl not installed")
    probe = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
    if "Controller" not in probe.stdout:
        pytest.skip("no live bluetoothd with a controller")
    # bluetoothctl 5.72 only consumes its piped stdin when the process has
    # a controlling terminal; under a session without one (pytest-xdist
    # workers, cron, systemd) an interactive session accepts the connection,
    # prints its banner and then ignores every command written to it. That
    # is a property of bluetoothctl, not of the transport, so this test
    # skips rather than reporting a false failure.
    try:
        os.close(os.open("/dev/tty", os.O_RDONLY))
    except OSError:
        pytest.skip("no controlling terminal: interactive bluetoothctl ignores piped stdin")
    control = BluezControl()
    seen = []
    with control.session() as session:
        session.send("show")
        # Generous deadline: bluetoothd's handshake competes with whatever
        # else the machine is doing (the suite runs test files in parallel),
        # and a short window makes this hardware test flaky rather than
        # informative.
        for line in session.lines(deadline=session._now() + 20.0):
            seen.append(line)
            if line.text.startswith("Controller"):
                break
    texts = [line.text for line in seen]
    assert any(t.startswith("Controller") for t in texts), texts
    # Captured on BlueZ 5.72 with stdin piped: bluetoothctl glues the
    # startup banner, its prompt and the next emission onto one physical
    # line and redraws the prompt with cursor-movement escapes. No line of
    # classified text may carry either through — the device-info modal
    # renders these strings verbatim.
    assert not any("\x1b" in t for t in texts), texts
    assert not any(_PROMPT_TOKEN in t for t in texts), texts
