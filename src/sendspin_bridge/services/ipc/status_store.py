"""The daemon's status, with an owner.

Five writers shared the raw dict — the aiosendspin callbacks, the log
handler, the command reader, the timing-telemetry watcher and the PulseAudio
volume tap — and only three of them took the module lock.

Under the GIL a single assignment and a single ``dict()`` copy are each
atomic, so the race worth preventing is not the copy: it is a *group* of
related keys written one at a time.  The daemon's re-anchor bookkeeping wrote
eight that way, so a status emission landing mid-group published a re-anchor
count that had gone up while ``reanchoring`` was still False.  Measured on
CPython 3.12, a concurrent reader sees such a group torn hundreds of
thousands of times a second.

:meth:`patch` is the answer to that: one call, one update, nothing observable
in between.  The store keeps the dict interface the daemon already passes
around, so the call sites read the same while gaining an owner.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = ["StatusStore"]


class StatusStore:
    """Thread-safe owner of a daemon's status.

    Reads return copies, so a caller can never hold a reference into the
    live state and watch it change underneath a ``json.dumps``.
    """

    def __init__(self, initial: Mapping[str, Any] | None = None):
        self._lock = threading.Lock()
        self._status: dict[str, Any] = dict(initial or {})

    # -- writes --------------------------------------------------------

    def patch(self, updates: Mapping[str, Any]) -> None:
        """Apply several keys as one indivisible update."""
        if not updates:
            return
        with self._lock:
            self._status.update(updates)

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            self._status[key] = value

    def setdefault(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._status.setdefault(key, default)

    def pop(self, key: str, *args: Any) -> Any:
        with self._lock:
            return self._status.pop(key, *args)

    # -- reads ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """A private copy of the whole state."""
        with self._lock:
            return dict(self._status)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._status[key]

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._status.get(key, default)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._status

    def __iter__(self) -> Iterator[str]:
        return iter(self.snapshot())

    def __len__(self) -> int:
        with self._lock:
            return len(self._status)

    def keys(self):
        return self.snapshot().keys()

    def items(self):
        return self.snapshot().items()

    def values(self):
        return self.snapshot().values()

    def copy(self) -> dict[str, Any]:
        return self.snapshot()

    def __repr__(self) -> str:
        return f"StatusStore({self.snapshot()!r})"
