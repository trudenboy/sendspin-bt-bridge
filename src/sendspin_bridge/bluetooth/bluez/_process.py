"""Subprocess mechanics for bluetoothctl invocation.

Owns the ``Spawner`` protocol (the seam tests substitute), the
``BluezResult`` value type, and the three timeout tiers.  The tier is
chosen by the calling method; callers never see a number.

Stdlib only — see ``tests/unit/bluetooth/test_bluez_import_rule.py``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ._lines import BluezLine, classify_lines

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "BluezResult",
    "Deadline",
    "Outcome",
    "PowerResult",
    "RemoveResult",
    "SubprocessSpawner",
    "VerbResult",
    "run_bluetoothctl",
]


class Outcome(Enum):
    """How an operation ended.

    The first four describe an invocation: it worked, the command exited
    non-zero, it never returned, or there was nothing to invoke.  ``FAILED``
    describes the operation instead — the transport was fine and BlueZ said
    no.  A caller that only wants to know whether to try a different
    transport looks at ``UNAVAILABLE``; the rest are answers.
    """

    OK = "ok"
    NONZERO = "nonzero"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class Deadline(float, Enum):
    """Timeout tiers, in seconds.  Methods pick the tier; callers don't."""

    PROBE = 3.0
    QUERY = 5.0
    MUTATE = 10.0


@dataclass(frozen=True, slots=True)
class BluezResult:
    """Everything one bluetoothctl invocation produced.

    ``script`` holds the stdin lines exactly as sent (including any
    emitted ``select <MAC>`` prefix).  ``text`` merges stdout + stderr
    byte-for-byte the way the legacy ``_run_bluetoothctl`` runner did, so
    log excerpts and stdout-marker checks behave identically.
    """

    argv: tuple[str, ...]
    script: tuple[str, ...]
    outcome: Outcome
    returncode: int | None
    stdout: str
    stderr: str
    error: str
    elapsed_s: float
    scope_unresolved: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK and self.returncode == 0

    @property
    def timed_out(self) -> bool:
        return self.outcome is Outcome.TIMEOUT

    @property
    def unavailable(self) -> bool:
        return self.outcome is Outcome.UNAVAILABLE

    @property
    def text(self) -> str:
        """stdout + stderr merged, byte-for-byte the legacy runner's output."""
        return "\n".join(part.strip("\n") for part in (self.stdout, self.stderr) if part)

    @property
    def lines(self) -> tuple[BluezLine, ...]:
        """Classified lines of the merged (:meth:`text`) output."""
        return classify_lines(self.text)

    @property
    def summary(self) -> str:
        """One diagnostic line for connect-style failures; "" when no signal."""
        from ._parsers import summarize_connect_output

        return summarize_connect_output(self.lines)


@dataclass(frozen=True, slots=True)
class VerbResult:
    """What a controller verb did, in the terms its caller acts on.

    ``detail`` is the one line worth logging or showing an operator — the
    BlueZ error behind a refusal — and is empty when there is nothing to
    say.  Callers used to derive both of these from a subprocess
    transcript, which only one of the two transports has.
    """

    outcome: Outcome
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def unavailable(self) -> bool:
        """No transport answered — the reason to try another one."""
        return self.outcome is Outcome.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class PowerResult:
    """Whether a controller is in the state that was asked for, and which state that is.

    ``applied`` is not "a toggle happened": a controller that was already
    powered on and asked to power on has had the request applied. Callers
    act on it as the success flag, which is what it has always been.
    """

    applied: bool
    powered: bool
    outcome: Outcome = Outcome.OK
    detail: str = ""

    @property
    def unavailable(self) -> bool:
        return self.outcome is Outcome.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class RemoveResult:
    """Whether a speaker was forgotten, and why it was not.

    ``not_available`` is its own answer rather than a failure: BlueZ has no
    such device, so the caller's goal is already met.
    """

    removed: bool
    not_available: bool
    outcome: Outcome = Outcome.OK
    detail: str = ""

    @property
    def unavailable(self) -> bool:
        return self.outcome is Outcome.UNAVAILABLE


class SubprocessSpawner:
    """Production ``Spawner``: real ``subprocess`` behind the protocol."""

    supports_select = True

    def run(self, argv: tuple[str, ...], input: str | None = None, timeout: float = 5.0):
        return subprocess.run(
            list(argv),
            input=input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def popen(self, argv: tuple[str, ...]):
        return subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )


def _coerce_stream(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_bluetoothctl(
    spawner,
    argv: tuple[str, ...],
    script: tuple[str, ...],
    timeout_s: float,
    time_source: Callable[[], float],
    *,
    scope_unresolved: bool = False,
) -> BluezResult:
    """Run one bluetoothctl invocation and package the outcome.

    Never raises for transport-level failures: a missing binary or other
    ``OSError`` becomes ``Outcome.UNAVAILABLE``; a timeout becomes
    ``Outcome.TIMEOUT`` with whatever partial output was captured.  This
    replaces the legacy runner's literal ``"timeout"`` return sentinel.
    """
    start = time_source()
    stdin_text = "\n".join(script) + "\n" if script else None
    try:
        proc = spawner.run(argv, input=stdin_text, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        return BluezResult(
            argv=argv,
            script=script,
            outcome=Outcome.TIMEOUT,
            returncode=None,
            stdout=_coerce_stream(getattr(exc, "stdout", None)),
            stderr=_coerce_stream(getattr(exc, "stderr", None)),
            error=f"timed out after {timeout_s}s",
            elapsed_s=time_source() - start,
            scope_unresolved=scope_unresolved,
        )
    except OSError as exc:
        # FileNotFoundError (bluetoothctl not installed) lands here.
        return BluezResult(
            argv=argv,
            script=script,
            outcome=Outcome.UNAVAILABLE,
            returncode=None,
            stdout="",
            stderr="",
            error=str(exc),
            elapsed_s=time_source() - start,
            scope_unresolved=scope_unresolved,
        )
    return BluezResult(
        argv=argv,
        script=script,
        outcome=Outcome.OK if proc.returncode == 0 else Outcome.NONZERO,
        returncode=proc.returncode,
        stdout=_coerce_stream(proc.stdout),
        stderr=_coerce_stream(proc.stderr),
        error="",
        elapsed_s=time_source() - start,
        scope_unresolved=scope_unresolved,
    )
