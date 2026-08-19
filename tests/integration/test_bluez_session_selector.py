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

import shutil
import subprocess

import pytest

from sendspin_bridge.bluetooth.bluez import BluezControl, LineKind


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
    control = BluezControl()
    with control.session() as session:
        session.send("show")
        seen = list(session.lines(deadline=session._now() + 5.0))
    texts = [line.text for line in seen]
    assert any(t.startswith("Controller") for t in texts)
    # The prompt echoes must be classified as prompts, not content.
    assert any(line.kind is LineKind.PROMPT for line in seen)
