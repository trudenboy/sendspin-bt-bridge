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


def mount_spa(app: FastAPI, spa_dir: Path | None = None) -> None:
    root = spa_dir if spa_dir is not None else find_spa_dir()

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str, request: Request) -> Response:
        if path.startswith("api/") or path == "api":
            raise HTTPException(404)
        if root is None:
            return HTMLResponse(_MISSING_PAGE)
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and root.resolve() in candidate.parents:
                headers = {}
                if candidate.parent.name == "assets":
                    # Vite puts a content hash in every asset file name.
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return FileResponse(candidate, headers=headers)
        return FileResponse(root / "index.html")
