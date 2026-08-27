from __future__ import annotations

import sys
from pathlib import Path

_DIST_PACKAGES = Path("/usr/lib/python3/dist-packages")


def _ensure_gi() -> None:
    try:
        import gi
    except ImportError:
        if not _DIST_PACKAGES.is_dir():
            raise
        path = str(_DIST_PACKAGES)
        if path not in sys.path:
            sys.path.append(path)
        import gi

    gi.require_version("Gst", "1.0")


_ensure_gi()

from gi.repository import Gst  # noqa: E402

if not Gst.is_initialized():
    Gst.init(None)
