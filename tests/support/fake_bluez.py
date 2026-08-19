"""FakeBluez — the shared bluetoothctl fake for the test suite.

This is **not** a ``subprocess`` mock: it substitutes the ``Spawner``
protocol that :class:`sendspin_bridge.bluetooth.bluez.BluezControl` takes
at construction (``run()`` / ``popen()`` / ``supports_select``), so tests
assert against structured command records instead of hand-rolled Popen
fakes and wire-protocol string scraping.

Usage::

    def test_something(fake_bluez):
        bluez = fake_bluez.control
        info = bluez.device_info("6C:5C:3D:35:17:99", Adapter.select("hci0"))
        assert info.paired is True
        fake_bluez.assert_never_selected(verb="show")   # the #193 one-liner

Failure injection::

    fake_bluez.fail("list", exc=FileNotFoundError)     # → Outcome.UNAVAILABLE
    fake_bluez.timeout("info")                          # → Outcome.TIMEOUT
    fake_bluez.nonzero("trust", stderr="boom")          # → Outcome.NONZERO
    fake_bluez.hang("devices", seconds=30)              # advances the clock, then times out

Interactive sessions::

    fake_bluez.session_script([
        ("pair ", ["[agent] Confirm passkey 312997 (yes/no):", "Pairing successful"]),
    ])

Version skew: ``FakeBluez(bluez_version="5.82")`` swaps the whole default
transcript corpus.  Corpora beyond 5.72 are frozen from the production
hosts (HAOS / LXC / test VM) — see ``tests/support/transcripts/``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

__all__ = ["Command", "FakeBluez"]

_SELECT_RE = re.compile(r"^select\s+(\S+)", re.IGNORECASE)

# Default transcript corpus for BlueZ 5.72 (captured on the live
# verification stand; the frozen full captures live under
# ``tests/support/transcripts/bluez-5.72/`` and back the parser tests).
_CORPORA: dict[str, dict[str, str]] = {
    "5.72": {
        "version": "bluetoothctl: 5.72\n",
        "list": "Controller C0:FB:F9:62:D7:D6 HP-ProDesk [default]\n",
        "show": (
            "Controller C0:FB:F9:62:D7:D6 (public)\n"
            "\tManufacturer: 0x000a (10)\n"
            "\tVersion: 0x06 (6)\n"
            "\tName: HP-ProDesk\n"
            "\tAlias: HP-ProDesk\n"
            "\tClass: 0x006c0104 (7078148)\n"
            "\tPowered: yes\n"
            "\tDiscoverable: no\n"
            "\tPairable: no\n"
            "\tDiscovering: no\n"
        ),
        "devices": ("Device 6C:5C:3D:35:17:99 ENEBY Portable\nDevice 30:21:0E:0A:AE:5A Lenco LS-500\n"),
        "info:6C:5C:3D:35:17:99": (
            "Device 6C:5C:3D:35:17:99 (public)\n"
            "\tName: ENEBY Portable\n"
            "\tAlias: ENEBY Portable\n"
            "\tClass: 0x00240404 (2360324)\n"
            "\tIcon: audio-headset\n"
            "\tPaired: yes\n"
            "\tBonded: yes\n"
            "\tTrusted: yes\n"
            "\tBlocked: no\n"
            "\tConnected: no\n"
            "\tLegacyPairing: no\n"
            "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
        ),
        "info:30:21:0E:0A:AE:5A": (
            "Device 30:21:0E:0A:AE:5A (public)\n"
            "\tName: Lenco LS-500\n"
            "\tAlias: Lenco LS-500\n"
            "\tClass: 0x00200418 (2098200)\n"
            "\tIcon: audio-headphones\n"
            "\tPaired: yes\n"
            "\tBonded: yes\n"
            "\tTrusted: yes\n"
            "\tBlocked: no\n"
            "\tConnected: no\n"
            "\tLegacyPairing: no\n"
            "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
        ),
    },
}


@dataclass(frozen=True, slots=True)
class Command:
    """One recorded interaction with the fake transport."""

    kind: str  # run | popen | send | reply | drain | close
    argv: tuple[str, ...] = ()
    script: str = ""
    adapter_selected: str = ""
    timeout: float | None = None
    at: float = 0.0

    @property
    def verb(self) -> str:
        """The first verb of the invocation (``list``, ``info``, ``show``…)."""
        if self.argv and len(self.argv) > 1:
            return self.argv[1].lstrip("-")
        for line in self.script.splitlines():
            stripped = line.strip()
            if not stripped or stripped.lower().startswith("select "):
                continue
            return stripped.split(" ", 1)[0].lower()
        return ""


@dataclass
class _Handler:
    match: Any  # str substring | callable(Command-ish text) -> bool
    adapter: str | None
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    exc: Exception | None = None
    timeout_seconds: float | None = None  # hang simulation


@dataclass
class _Completed:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


class _FakeStdin:
    """stdin stand-in; records writes and fires session-script triggers."""

    def __init__(self, fake: FakeBluez, proc: _FakeProcess) -> None:
        self._fake = fake
        self._proc = proc

    def write(self, text: str) -> None:
        self._record("send", text)

    def reply(self, text: str) -> None:
        # Duck-typed hook used by BluezSession.reply() — recorded as a
        # distinct command kind so the SSP handshake is assertable.
        self._record("reply", text + "\n")

    def flush(self) -> None:
        pass

    def _record(self, kind: str, text: str) -> None:
        self._fake._record(kind=kind, script=text.rstrip("\n"))
        for line in text.splitlines():
            self._proc.fire_triggers(line)


class _FakeStdout:
    """readline() over the scripted buffer; "" means *no data yet*."""

    def __init__(self, proc: _FakeProcess) -> None:
        self._proc = proc

    def readline(self) -> str:
        if self._proc.buffer:
            return self._proc.buffer.popleft()
        return ""


class _FakeProcess:
    """The popen() stand-in: scripted stdout, recorded stdin."""

    def __init__(self, fake: FakeBluez, triggers: list[tuple[str, list[str]]]) -> None:
        self._fake = fake
        self._triggers = triggers
        from collections import deque

        self.buffer: deque[str] = deque()
        self.stdin = _FakeStdin(fake, self)
        self.stdout = _FakeStdout(self)
        self.returncode: int | None = None

    def fire_triggers(self, written_line: str) -> None:
        for trigger, emitted in self._triggers:
            if trigger in written_line:
                self.buffer.extend(line + "\n" for line in emitted)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def communicate(self, timeout: float | None = None):
        self._fake._record(kind="drain", timeout=timeout)
        out = "".join(self.buffer)
        self.buffer.clear()
        if self.returncode is None:
            self.returncode = 0
        return out, None


class _AdapterRegistration:
    """``fake.on_adapter(mac).on(...)`` — adapter-scoped registrations."""

    def __init__(self, fake: FakeBluez, mac: str) -> None:
        self._fake = fake
        self._mac = mac.upper()

    def on(self, match, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self._fake.on(match, stdout=stdout, stderr=stderr, returncode=returncode, adapter=self._mac)


class FakeBluez:
    """Spawner substitute with a recorded command log and a fake clock."""

    supports_select = False

    def __init__(self, *, bluez_version: str = "5.72") -> None:
        if bluez_version not in _CORPORA:
            raise ValueError(
                f"No frozen transcript corpus for BlueZ {bluez_version!r}; "
                f"available: {sorted(_CORPORA)}. Capture it from a real host first."
            )
        self.bluez_version = bluez_version
        self.commands: list[Command] = []
        self._handlers: list[_Handler] = []
        self._session_triggers: list[tuple[str, list[str]]] = []
        self._clock = 0.0
        self.hci_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Clock (injected into BluezControl as time_source / sleeper)
    # ------------------------------------------------------------------

    def now(self) -> float:
        return self._clock

    def sleep(self, seconds: float) -> None:
        self._clock += seconds

    @property
    def control(self):
        """A BluezControl wired to this fake (spawner + instant clock)."""
        from sendspin_bridge.bluetooth.bluez import BluezControl

        return BluezControl(
            spawner=self,
            hci_resolver=lambda hci: self.hci_map.get(hci, ""),
            sysfs_dir=self._no_sysfs_dir(),
            time_source=self.now,
            sleeper=self.sleep,
        )

    @staticmethod
    def _no_sysfs_dir():
        # A path that never exists, so resolution falls to the injected
        # hci_map and then to the fake's own ``list`` corpus.
        import tempfile
        from pathlib import Path

        return Path(tempfile.gettempdir()) / "fake-bluez-no-sysfs"

    # ------------------------------------------------------------------
    # Scripting
    # ------------------------------------------------------------------

    def on(self, match, *, stdout: str = "", stderr: str = "", returncode: int = 0, adapter: str | None = None) -> None:
        """Script a response; last registration wins. ``match`` is a
        substring tested against the script + argv, or a callable."""
        self._handlers.append(
            _Handler(match=match, adapter=adapter, stdout=stdout, stderr=stderr, returncode=returncode)
        )

    def on_adapter(self, mac: str) -> _AdapterRegistration:
        return _AdapterRegistration(self, mac)

    def fail(self, match, exc: Exception | None = None) -> None:
        """Make matching invocations raise — FileNotFoundError → UNAVAILABLE."""
        self._handlers.append(_Handler(match=match, adapter=None, exc=exc or FileNotFoundError("bluetoothctl")))

    def timeout(self, match) -> None:
        """Matching invocations raise TimeoutExpired → Outcome.TIMEOUT."""
        self._handlers.append(
            _Handler(match=match, adapter=None, exc=subprocess.TimeoutExpired(cmd="bluetoothctl", timeout=5))
        )

    def nonzero(self, match, *, stdout: str = "", stderr: str = "") -> None:
        """A non-zero exit, optionally with the partial output it left behind."""
        self._handlers.append(_Handler(match=match, adapter=None, returncode=1, stdout=stdout, stderr=stderr))

    def hang(self, match, *, seconds: float = 30.0) -> None:
        """Advance the fake clock past the deadline, then time out."""
        self._handlers.append(_Handler(match=match, adapter=None, timeout_seconds=seconds))

    def session_script(self, pairs: list[tuple[str, list[str]]]) -> None:
        """Script an interactive session: ``(trigger, [emitted lines])``.

        When a line written to stdin contains ``trigger``, the mapped
        lines are emitted on stdout — e.g. the SSP prompt after ``pair``.
        """
        self._session_triggers.extend(pairs)

    # ------------------------------------------------------------------
    # Spawner SPI
    # ------------------------------------------------------------------

    def run(self, argv, input: str | None = None, timeout: float | None = None):
        argv = tuple(argv)
        script = input or ""
        self._record(kind="run", argv=argv, script=script, timeout=timeout)
        handler = self._find_handler(argv, script)
        if handler is not None:
            return self._apply(handler, argv, timeout)
        return self._default_response(argv, script)

    def popen(self, argv):
        self._record(kind="popen", argv=tuple(argv))
        return _FakeProcess(self, list(self._session_triggers))

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def verbs(self) -> list[str]:
        """The verbs of all run/popen invocations, in order."""
        return [c.verb for c in self.commands if c.kind in ("run", "popen")]

    def scoped(self, mac: str) -> list[Command]:
        """All commands that carried ``select <mac>``."""
        return [c for c in self.commands if c.adapter_selected == mac.upper()]

    def assert_never_selected(self, *, verb: str | None = None) -> None:
        """Assert no matching invocation emitted a ``select`` line.

        ``verb="show"`` is the #193 regression test in one line: the
        addressed alias read must never select a controller.
        """
        offenders = [c for c in self.commands if c.adapter_selected and (verb is None or c.verb == verb)]
        assert not offenders, f"expected no select for verb={verb!r}, got: {offenders}"

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record(self, *, kind: str, argv: tuple[str, ...] = (), script: str = "", timeout: float | None = None) -> None:
        adapter_selected = ""
        for line in script.splitlines():
            match = _SELECT_RE.match(line.strip())
            if match:
                adapter_selected = match.group(1)
        self.commands.append(
            Command(
                kind=kind,
                argv=argv,
                script=script,
                adapter_selected=adapter_selected,
                timeout=timeout,
                at=self._clock,
            )
        )

    def _matches(self, handler: _Handler, argv: tuple[str, ...], script: str) -> bool:
        haystack = script + "\n" + " ".join(argv)
        if callable(handler.match):
            return bool(handler.match(haystack))
        return str(handler.match) in haystack

    def _find_handler(self, argv: tuple[str, ...], script: str) -> _Handler | None:
        selected = ""
        for line in script.splitlines():
            match = _SELECT_RE.match(line.strip())
            if match:
                selected = match.group(1).upper()
        for handler in reversed(self._handlers):  # last registration wins
            if handler.adapter is not None and handler.adapter != selected:
                continue
            if self._matches(handler, argv, script):
                return handler
        return None

    def _apply(self, handler: _Handler, argv: tuple[str, ...], timeout: float | None) -> _Completed:
        if handler.timeout_seconds is not None:
            self.sleep(handler.timeout_seconds)
            raise subprocess.TimeoutExpired(cmd=" ".join(argv), timeout=timeout or handler.timeout_seconds)
        if handler.exc is not None:
            raise handler.exc
        return _Completed(stdout=handler.stdout, stderr=handler.stderr, returncode=handler.returncode)

    def _default_response(self, argv: tuple[str, ...], script: str) -> _Completed:
        corpus = _CORPORA[self.bluez_version]
        if "--version" in argv:
            return _Completed(stdout=corpus["version"])
        if "list" in argv:
            return _Completed(stdout=corpus["list"])
        # Script mode: prompt-echo each command line, then its chunk.
        out: list[str] = []
        for line in script.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            out.append(f"[bluetooth]# {stripped}\n")
            out.append(self._chunk_for(stripped, corpus))
        return _Completed(stdout="".join(out))

    def _chunk_for(self, command_line: str, corpus: dict[str, str]) -> str:
        verb, _, arg = command_line.partition(" ")
        verb = verb.lower()
        arg = arg.strip()
        if verb == "select" or not verb:
            return ""
        if verb == "show":
            return corpus["show"]
        if verb == "devices":
            return corpus["devices"]
        if verb == "info":
            return corpus.get(
                f"info:{arg.upper()}", f"Device {arg.upper()} not available\nDeviceSet {arg.upper()} not available\n"
            )
        if verb == "power":
            return f"Changing power {arg} succeeded\n" if arg else ""
        if verb == "trust":
            return "Changing trust succeeded\n"
        if verb == "connect":
            return "Connection successful\n"
        if verb == "disconnect":
            return "Successful disconnected\n"
        if verb == "remove":
            return "Device has been removed\n"
        if verb in ("agent", "default-agent"):
            return "Agent registered\n" if verb == "agent" else "Default agent request successful\n"
        if verb in ("scan", "quit", "exit"):
            return ""
        return ""
