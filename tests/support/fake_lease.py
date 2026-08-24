"""A stand-in for the adapter lease used by route tests.

Routes take the lease on the request thread and hand its release to the
worker thread.  Tests that only care about the routing decision substitute
this for :func:`api_bt._acquire_bt_lease` and assert on ``released``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class FakeLease:
    """Records its release; optionally notifies a callback."""

    def __init__(self, on_release: Callable[[], None] | None = None, reason: str = "test"):
        self._on_release = on_release
        self.reason = reason
        self.released = False

    @property
    def active(self) -> bool:
        return not self.released

    def release(self) -> None:
        self.released = True
        if self._on_release is not None:
            self._on_release()

    def __enter__(self) -> FakeLease:
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
