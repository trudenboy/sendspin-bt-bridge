"""``/sync`` — group sync tuning. ``public`` serves the click track to Music Assistant."""

from __future__ import annotations

from fastapi import APIRouter, Response

from sendspin_bridge.api.errors import ApiError
from sendspin_bridge.sync_tuning import click_track_host as host

public = APIRouter(tags=["sync"])


@public.get("/sync/signal/{token}.wav", response_class=Response, summary="Click track for a running session (public)")
def signal(token: str) -> Response:
    if len(token) != 64 or host.tokens.session_for(token) is None:
        raise ApiError(404, "not_found", "No such signal")
    return Response(host.track_wav(), media_type="audio/wav", headers={"Cache-Control": "no-store"})
