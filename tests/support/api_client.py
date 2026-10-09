"""A FastAPI test client for the bridge API, without the bridge around it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from sendspin_bridge.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path


def make_client(
    config: dict[str, Any] | None = None,
    *,
    peer: str = "testclient",
    spa_dir: Path | None = None,
    **headers: str,
) -> TestClient:
    """An API client; authentication is off unless *config* turns it on.

    With *spa_dir* the SPA catch-all serves that directory, as in production.

    The lifespan (event hub) is not started — enter the client as a context
    manager for tests that need ``/events``.
    """
    cfg = {"AUTH_ENABLED": False, "SECRET_KEY": "test-secret-key-0123456789abcdef"}
    cfg.update(config or {})
    app = create_app(config=cfg, spa_dir=spa_dir, serve_spa=spa_dir is not None)
    client = TestClient(app, raise_server_exceptions=False, client=(peer, 50000))
    client.headers.update(headers)
    return client


def wait_for_job(client: TestClient, job: dict[str, Any], *, timeout: float = 5.0) -> dict[str, Any]:
    """Poll ``/jobs/{id}`` until the job leaves ``running``."""
    import time

    deadline = time.monotonic() + timeout
    current = job
    while current["status"] == "running":
        if time.monotonic() > deadline:
            raise AssertionError(f"job {job['id']} still running after {timeout}s")
        time.sleep(0.01)
        current = client.get(f"/api/v1/jobs/{job['id']}").json()
    return current


_SESSION_COOKIE = "sendspin_session"


def set_session(client: TestClient, data: dict[str, Any]) -> None:
    """Seed the signed session cookie, as a previous request would have."""
    import base64
    import json

    from itsdangerous import TimestampSigner

    payload = base64.b64encode(json.dumps(data).encode())
    # The jar files cookies from ``testserver`` under ``testserver.local``; use
    # the same domain so the server's own Set-Cookie replaces this one.
    client.cookies.set(
        _SESSION_COOKIE, TimestampSigner(_secret(client)).sign(payload).decode(), domain="testserver.local"
    )


def get_session(client: TestClient) -> dict[str, Any]:
    """The session the server last stored (``{}`` when there is none)."""
    import base64
    import json

    from itsdangerous import TimestampSigner

    raw = client.cookies.get(_SESSION_COOKIE)
    if not raw:
        return {}
    return json.loads(base64.b64decode(TimestampSigner(_secret(client)).unsign(raw.encode())))


def _secret(client: TestClient) -> str:
    for middleware in client.app.user_middleware:
        if middleware.cls.__name__ == "SessionMiddleware":
            return str(middleware.kwargs["secret_key"])
    raise AssertionError("no session middleware")
