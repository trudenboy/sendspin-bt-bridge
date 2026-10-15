"""Serve the compiled SPA: hashed assets cached forever, every other path → index.html."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.exceptions import HTTPException

_PACKAGE_SPA = Path(__file__).resolve().parent.parent / "spa"
_REPO_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"

_MISSING_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Sendspin Bluetooth Bridge</title></head>
<body style="font-family:sans-serif;margin:2rem"><h1>Sendspin Bluetooth Bridge</h1>
<p>The web interface is not installed in this build. The API is available at
<a href="api/v1/docs">api/v1/docs</a>.</p></body></html>"""


def find_spa_dir() -> Path | None:
    """Where the built SPA lives: ``SENDSPIN_SPA_DIR``, the package, or ``ui/dist`` in a checkout."""
    candidates = [os.environ.get("SENDSPIN_SPA_DIR", ""), str(_PACKAGE_SPA), str(_REPO_DIST)]
    for candidate in candidates:
        if candidate and (Path(candidate) / "index.html").is_file():
            return Path(candidate)
    return None


_MAX_SEGMENTS = 16


def _file_in(root: Path, resolved_root: Path, relative: str) -> Path | None:
    try:
        candidate = (root / relative).resolve()
        if candidate.is_file() and resolved_root in candidate.parents:
            return candidate
    except (OSError, ValueError):
        pass
    return None


def _find_file(root: Path, path: str) -> Path | None:
    """The SPA file a request names, or ``None`` (then the page itself is served).

    Asset URLs are relative (``./assets/...``, for HA ingress), so a reload of a nested page
    (``/sync/<group>``) asks for ``sync/assets/...``. Besides the path itself, at most two
    more lookups: from its first ``assets`` segment, and its last segment alone (favicon).
    Deeply nested paths are never app files: no lookup at all. The handler runs on the
    bridge loop, unauthenticated, so the work stays constant.
    """
    parts = path.split("/")
    if len(parts) > _MAX_SEGMENTS:
        return None
    resolved_root = root.resolve()
    tries = [path]
    if "assets" in parts[1:]:
        tries.append("/".join(parts[parts.index("assets", 1) :]))
    if len(parts) > 1:
        tries.append(parts[-1])
    for relative in tries:
        found = _file_in(root, resolved_root, relative)
        if found is not None:
            return found
    return None


def mount_spa(app: FastAPI, spa_dir: Path | None = None) -> None:
    root = spa_dir if spa_dir is not None else find_spa_dir()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        if path.startswith("api/") or path == "api":
            raise HTTPException(404)
        if root is None:
            return HTMLResponse(_MISSING_PAGE)
        if path:
            candidate = _find_file(root, path)
            if candidate is not None:
                headers = {}
                if candidate.parent.name == "assets":
                    # Vite puts a content hash in every asset file name.
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return FileResponse(candidate, headers=headers)
        return FileResponse(root / "index.html")
