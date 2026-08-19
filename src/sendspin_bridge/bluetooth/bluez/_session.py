"""Interactive bluetoothctl sessions: one long-lived process, phase-driven I/O.

Substrate for the pairing composites (phase 2) and the scan composite.
The ``selectors.DefaultSelector`` is created in ``__enter__`` and owned by
the :meth:`BluezSession.lines` generator — no ``fileno``, no selector and
no ``proc.stdout`` cross the interface.  When the spawner declares
``supports_select = False`` (the test fake), a clock-driven ``readline()``
fallback is used instead; ``tests/integration/test_bluez_session_selector.py``
keeps the real selector branch honest.

Stdlib only — see ``tests/unit/bluetooth/test_bluez_import_rule.py``.
"""

from __future__ import annotations

import logging
import selectors
import subprocess
from typing import TYPE_CHECKING, Any

from ._lines import BluezLine, classify_line, classify_lines

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

__all__ = ["BluezSession"]


class BluezSession:
    """A running interactive bluetoothctl process.

    Obtained from :meth:`BluezControl.session`; use as a context manager.
    Sessions take no overall timeout — the composites are phase-structured
    and pass per-phase deadlines to :meth:`lines`.
    """

    def __init__(
        self,
        *,
        spawner,
        select_lines: tuple[str, ...] = (),
        cancel: Callable[[], bool] | None = None,
        time_source: Callable[[], float],
        sleeper: Callable[[float], None],
    ):
        self._spawner = spawner
        self._select_lines = select_lines
        self._cancel = cancel
        self._now = time_source
        self._sleep = sleeper
        self._proc: Any = None
        self._sel: selectors.BaseSelector | None = None
        self._transcript: list[BluezLine] = []
        self._aborted = False

    def __enter__(self) -> BluezSession:
        self._proc = self._spawner.popen(("bluetoothctl",))
        # Cancel-after-spawn: evaluate the callback once, up front.  On fire,
        # terminate immediately and yield a session whose every method is a
        # no-op — no leaked process, no partial command stream.
        if self._cancel is not None and self._cancel():
            self._aborted = True
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception as exc:
                logger.debug("session cancel cleanup failed: %s", exc)
            return self
        if getattr(self._spawner, "supports_select", False):
            try:
                self._sel = selectors.DefaultSelector()
                self._sel.register(self._proc.stdout, selectors.EVENT_READ)
            except Exception as exc:
                logger.debug("selector registration failed, using readline fallback: %s", exc)
                self._sel = None
        if self._select_lines:
            self.send(*self._select_lines)
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def send(self, *lines: str) -> None:
        """Write command lines to stdin.  No-op on an aborted session."""
        if self._aborted or self._proc is None:
            return
        stdin = self._proc.stdin
        if stdin is None:
            raise RuntimeError("bluetoothctl subprocess stdin unavailable")
        stdin.write("\n".join(lines) + "\n")
        stdin.flush()

    def reply(self, text: str) -> None:
        """Answer an interactive prompt (SSP passkey, legacy PIN)."""
        if self._aborted or self._proc is None:
            return
        stdin = self._proc.stdin
        if stdin is None:
            raise RuntimeError("bluetoothctl subprocess stdin unavailable")
        # Duck-typed hook so the test fake can record the reply as a
        # distinct command kind (``Command(kind="reply")``).
        if hasattr(stdin, "reply"):
            stdin.reply(text)
            return
        stdin.write(text + "\n")
        stdin.flush()

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def lines(self, deadline: float | None = None) -> Iterator[BluezLine]:
        """Yield classified output lines as they arrive, up to ``deadline``.

        Callers ``break`` out of the loop on the condition they await —
        exactly how the historical pair loops consumed stdout.  Every
        yielded line is also appended to :meth:`transcript`.
        """
        if self._aborted or self._proc is None:
            return
        while True:
            if deadline is not None and self._now() >= deadline:
                return
            if self._proc.poll() is not None:
                return
            if self._sel is not None:
                remaining = 0.5 if deadline is None else max(0.0, min(deadline - self._now(), 0.5))
                events = self._sel.select(timeout=remaining)
                if not events:
                    continue
            line = self._proc.stdout.readline()
            if not line:
                if self._sel is not None:
                    return  # EOF on a real pipe
                # Fallback spawner (tests): no data yet — advance the clock.
                self._sleep(0.05)
                continue
            parsed = classify_line(line)
            self._transcript.append(parsed)
            yield parsed

    def wait(self, seconds: float, *, drain: bool = False) -> None:
        """Wait ``seconds``; with ``drain=True`` consume output meanwhile."""
        if self._aborted:
            return
        end = self._now() + seconds
        if drain:
            for _line in self.lines(deadline=end):
                pass
            return
        remaining = end - self._now()
        if remaining > 0:
            self._sleep(remaining)

    def transcript(self) -> tuple[BluezLine, ...]:
        """Everything consumed so far, in arrival order."""
        return tuple(self._transcript)

    def drain(self, *, timeout: float = 3.0) -> None:
        """Collect remaining output after the final command phase."""
        if self._aborted or self._proc is None:
            return
        try:
            out, _ = self._proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return
        except Exception as exc:
            logger.debug("session drain failed: %s", exc)
            return
        if out:
            self._transcript.extend(classify_lines(out))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def alive(self) -> bool:
        return self._proc is not None and not self._aborted and self._proc.poll() is None

    @property
    def returncode(self) -> int | None:
        return self._proc.poll() if self._proc is not None else None

    def close(self) -> None:
        if self._sel is not None:
            try:
                self._sel.close()
            except Exception as exc:
                logger.debug("selector close failed: %s", exc)
            self._sel = None
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.kill()
                self._proc.wait(timeout=3)
        except Exception as exc:
            logger.debug("session close failed: %s", exc)
