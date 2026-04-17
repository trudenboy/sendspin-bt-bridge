"""Adapter-awareness for /api/bt/reset_reconnect.

Bonds that live on a non-default adapter (e.g. hci1) must be reset
against that same adapter, otherwise ``remove`` / ``power off`` / ``pair``
all run on the BlueZ default controller and silently fail.

* The endpoint accepts an ``adapter`` field and forwards it to the
  background job.
* Invalid adapter identifiers are rejected up-front.
* Backwards compatibility: a missing ``adapter`` preserves the historic
  "default controller" behaviour (empty string).
* The background routine threads ``select <adapter>`` through every
  bluetoothctl session (remove, power cycle, pair + trust + connect).
"""

from __future__ import annotations

import re
import threading
from typing import Any

import pytest
from flask import Flask

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def client(tmp_config):
    from routes.api_bt import bt_bp

    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    return app.test_client()


def _extract_select_lines(input_text: str) -> list[str]:
    adapters: list[str] = []
    for line in str(input_text or "").splitlines():
        clean = _ANSI_RE.sub("", line).strip()
        if clean.startswith("select "):
            adapters.append(clean.split(" ", 1)[1].strip().upper())
    return adapters


class _FakeCompletedProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakePopen:
    """Minimal Popen stand-in capturing stdin writes and returning canned output."""

    def __init__(self, stdout_text: str = "Pairing successful\n"):
        self._stdout_lines = list(stdout_text.splitlines(keepends=True))
        self.stdin = _FakeWriter()
        self.stdout = _FakeReader(self._stdout_lines)
        self._closed = False

    def poll(self) -> int | None:
        return 0 if self._closed else None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        remaining = "".join(self._stdout_lines[self.stdout._idx :])
        self._closed = True
        return remaining, ""

    def kill(self) -> None:
        self._closed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer: list[str] = []

    def write(self, data: str) -> int:
        self.buffer.append(data)
        return len(data)

    def flush(self) -> None:
        pass


class _FakeReader:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self._idx = 0

    def fileno(self) -> int:  # selectors.DefaultSelector needs this
        return 0

    def readline(self) -> str:
        if self._idx >= len(self._lines):
            return ""
        line = self._lines[self._idx]
        self._idx += 1
        return line


class _FakeSelector:
    """Replacement for ``selectors.DefaultSelector`` that skips real I/O waits."""

    def __init__(self) -> None:
        self._fd: Any = None

    def register(self, fd: Any, _events: int) -> None:
        self._fd = fd

    def select(self, timeout: float | None = None):
        del timeout
        return [("evt",)] if self._fd is not None else []

    def close(self) -> None:
        self._fd = None


def test_reset_reconnect_accepts_adapter_and_forwards_it(client, monkeypatch):
    """POST body's ``adapter`` must reach the background job verbatim."""

    import routes.api_bt as module

    captured: dict[str, Any] = {}
    done = threading.Event()

    def fake_run(job_id: str, mac: str, adapter: str) -> None:
        captured["mac"] = mac
        captured["adapter"] = adapter
        module.finish_scan_job(job_id, {"success": True})
        done.set()

    monkeypatch.setattr(module, "_run_reset_reconnect", fake_run)

    resp = client.post(
        "/api/bt/reset_reconnect",
        json={"mac": "AA:BB:CC:DD:EE:01", "adapter": "C0:FB:F9:62:D7:D6"},
    )
    assert resp.status_code == 200
    assert resp.get_json().get("job_id")
    assert done.wait(2.0), "background thread never invoked _run_reset_reconnect"
    assert captured == {"mac": "AA:BB:CC:DD:EE:01", "adapter": "C0:FB:F9:62:D7:D6"}


def test_reset_reconnect_preserves_default_adapter_when_omitted(client, monkeypatch):
    """Missing ``adapter`` → empty string (pre-existing behaviour)."""

    import routes.api_bt as module

    captured: dict[str, Any] = {}
    done = threading.Event()

    def fake_run(job_id: str, mac: str, adapter: str) -> None:
        captured["adapter"] = adapter
        module.finish_scan_job(job_id, {"success": True})
        done.set()

    monkeypatch.setattr(module, "_run_reset_reconnect", fake_run)

    resp = client.post("/api/bt/reset_reconnect", json={"mac": "AA:BB:CC:DD:EE:02"})
    assert resp.status_code == 200
    assert done.wait(2.0)
    assert captured["adapter"] == ""


def test_reset_reconnect_rejects_invalid_adapter(client, monkeypatch):
    """Garbage adapter strings must 400 before spawning the job thread."""

    import routes.api_bt as module

    called = threading.Event()

    def fake_run(*_a: Any, **_kw: Any) -> None:
        called.set()

    monkeypatch.setattr(module, "_run_reset_reconnect", fake_run)

    resp = client.post(
        "/api/bt/reset_reconnect",
        json={"mac": "AA:BB:CC:DD:EE:03", "adapter": "not-a-mac"},
    )
    assert resp.status_code == 400
    assert not called.is_set()


def test_run_reset_reconnect_threads_select_adapter_through_every_phase(monkeypatch):
    """``select <adapter>`` must appear in remove, power-cycle, *and*
    the pair/trust/connect bluetoothctl session — otherwise the power
    cycle hits the default controller and pairing happens on the wrong
    radio.
    """

    import routes.api_bt as module

    captured_runs: list[str] = []

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        captured_runs.append(kw.get("input", "") or "")
        return _FakeCompletedProcess()

    fake_proc = _FakePopen(stdout_text="Pairing successful\n")

    def fake_popen(*_a: Any, **_kw: Any) -> _FakePopen:
        return fake_proc

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_PAIR_SCAN_DURATION", 0)
    monkeypatch.setattr(module, "_PAIR_WAIT_DURATION", 5)

    import selectors

    monkeypatch.setattr(selectors, "DefaultSelector", _FakeSelector)

    job_id = "job-test-1"
    module.create_scan_job(job_id)
    module._run_reset_reconnect(job_id, "AA:BB:CC:DD:EE:04", "C0:FB:F9:62:D7:D6")

    # Two ``subprocess.run`` calls: remove phase + power-cycle phase.
    assert len(captured_runs) == 2
    assert _extract_select_lines(captured_runs[0]) == ["C0:FB:F9:62:D7:D6"]
    assert _extract_select_lines(captured_runs[1]) == ["C0:FB:F9:62:D7:D6"]

    # Pair/trust/connect session: inspect everything written to the Popen stdin.
    popen_input = "".join(fake_proc.stdin.buffer)
    assert _extract_select_lines(popen_input) == ["C0:FB:F9:62:D7:D6"]
    # Sanity: the adapter-scoped session must still actually pair/trust/connect.
    assert "pair AA:BB:CC:DD:EE:04" in popen_input
    assert "trust AA:BB:CC:DD:EE:04" in popen_input
    assert "connect AA:BB:CC:DD:EE:04" in popen_input


def test_run_reset_reconnect_translates_hci_name_to_controller_mac(monkeypatch):
    """``bluetoothctl select hci1`` fails on HAOS / LXC with ``Controller
    hci1 not available`` — only the controller MAC is accepted. The fleet
    row's ``<select>`` sends ``hci0``/``hci1`` as its value, so the reset
    flow must translate it to a MAC before issuing ``select`` or the
    entire sequence silently runs against the default controller.
    """

    import routes.api_bt as module

    captured_runs: list[str] = []

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        captured_runs.append(kw.get("input", "") or "")
        return _FakeCompletedProcess()

    fake_proc = _FakePopen(stdout_text="Pairing successful\n")

    def fake_popen(*_a: Any, **_kw: Any) -> _FakePopen:
        return fake_proc

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_PAIR_SCAN_DURATION", 0)
    monkeypatch.setattr(module, "_PAIR_WAIT_DURATION", 5)
    # Pretend the host reports two controllers: hci0 + hci1.
    monkeypatch.setattr(
        module,
        "list_bt_adapters",
        lambda: ["C0:FB:F9:62:D6:9D", "C0:FB:F9:62:D7:D6"],
    )

    import selectors

    monkeypatch.setattr(selectors, "DefaultSelector", _FakeSelector)

    job_id = "job-test-hci"
    module.create_scan_job(job_id)
    module._run_reset_reconnect(job_id, "AA:BB:CC:DD:EE:05", "hci1")

    # Every ``select`` across every phase must name the resolved MAC.
    assert _extract_select_lines(captured_runs[0]) == ["C0:FB:F9:62:D7:D6"]
    assert _extract_select_lines(captured_runs[1]) == ["C0:FB:F9:62:D7:D6"]
    popen_input = "".join(fake_proc.stdin.buffer)
    assert _extract_select_lines(popen_input) == ["C0:FB:F9:62:D7:D6"]
    assert "select hci1" not in popen_input.lower()


def test_run_reset_reconnect_keeps_hci_name_when_resolution_fails(monkeypatch):
    """If ``bluetoothctl list`` is empty (e.g. all adapters down mid-flow),
    fall back to the supplied ``hciN`` instead of dropping the ``select``
    prefix entirely.  The command may still fail at bluetoothctl layer,
    but silently running against the default controller is worse — a
    failed ``select`` surfaces as the natural "not paired" outcome.
    """

    import routes.api_bt as module

    captured_runs: list[str] = []

    def fake_run(args: Any, *_a: Any, **kw: Any) -> _FakeCompletedProcess:
        captured_runs.append(kw.get("input", "") or "")
        return _FakeCompletedProcess()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: _FakePopen())
    monkeypatch.setattr(module.time, "sleep", lambda *_a, **_kw: None)
    monkeypatch.setattr(module, "_PAIR_SCAN_DURATION", 0)
    monkeypatch.setattr(module, "_PAIR_WAIT_DURATION", 5)
    monkeypatch.setattr(module, "list_bt_adapters", lambda: [])

    import selectors

    monkeypatch.setattr(selectors, "DefaultSelector", _FakeSelector)

    job_id = "job-test-fallback"
    module.create_scan_job(job_id)
    module._run_reset_reconnect(job_id, "AA:BB:CC:DD:EE:06", "hci0")

    assert _extract_select_lines(captured_runs[0]) == ["HCI0"]
