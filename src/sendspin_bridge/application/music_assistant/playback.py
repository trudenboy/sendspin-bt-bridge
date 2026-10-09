"""Music Assistant playback: now playing, queue commands, the artwork proxy."""

from __future__ import annotations

import logging
import threading
import time
import urllib.error as _ue
import urllib.parse as _up
import urllib.request as _ur
import uuid
from typing import Any, Literal

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.jobs import Job, JobContext, jobs
from sendspin_bridge.application.music_assistant.common import await_loop_result
from sendspin_bridge.application.runtime import bridge_loop
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.infrastructure.url_safety import safe_build_opener
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot_pairs
from sendspin_bridge.services.music_assistant.ma_artwork import has_valid_artwork_signature
from sendspin_bridge.services.music_assistant.ma_monitor import solo_queue_candidates
from sendspin_bridge.services.music_assistant.ma_runtime_state import (
    apply_ma_now_playing_prediction,
    fail_ma_pending_op,
    get_ma_api_credentials,
    get_ma_group_by_id,
    get_ma_group_for_player_id,
    get_ma_groups,
    get_ma_now_playing,
    is_ma_connected,
)

logger = logging.getLogger(__name__)

QueueAction = Literal["next", "previous", "shuffle", "repeat", "seek"]
_ARTWORK_MAX_BYTES = 10 * 1024 * 1024


def _resolve_target_queue(
    syncgroup_id: str | None,
    player_id: str | None = None,
    group_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve (state_key, target_queue_id) from request context.

    ``state_key`` is the key used in our shared now-playing cache/UI snapshots.
    ``target_queue_id`` is the actual MA queue/player identifier that queue
    commands must target.
    """
    raw_syncgroup_id = str(syncgroup_id or "").strip()
    raw_player_id = str(player_id or "").strip()
    raw_group_id = str(group_id or "").strip()
    solo_queue_ids = solo_queue_candidates(raw_player_id)
    solo_queue_id = solo_queue_ids[0] if solo_queue_ids else ""

    if not raw_player_id:
        for candidate in (raw_syncgroup_id, raw_group_id):
            if candidate.startswith(("up", "media_player.", "ma_")):
                return candidate, candidate

        active_clients = []
        for client, device in build_device_snapshot_pairs(get_device_registry_snapshot().active_clients):
            pid = str(getattr(client, "player_id", "") or "").strip()
            if not pid:
                continue
            is_running = False
            try:
                is_running = bool(client.is_running())
            except Exception:
                is_running = False
            if not (is_running or device.server_connected):
                continue
            active_clients.append((pid, device))

        if len(active_clients) == 1:
            inferred_player_id, _status = active_clients[0]
            inferred_solo_queue_ids = solo_queue_candidates(inferred_player_id)
            inferred_solo_queue_id = inferred_solo_queue_ids[0] if inferred_solo_queue_ids else ""
            for candidate in (raw_syncgroup_id, raw_group_id):
                if candidate.startswith("syncgroup_"):
                    ma_group = get_ma_group_by_id(candidate)
                    members = {str(m.get("id", "")) for m in (ma_group or {}).get("members", [])}
                    if any(queue_id in members for queue_id in inferred_solo_queue_ids):
                        return candidate, candidate
            return inferred_player_id, inferred_solo_queue_id

    if raw_player_id:
        ma_group = get_ma_group_for_player_id(raw_player_id)
        if ma_group and ma_group.get("id"):
            resolved = ma_group["id"]
            return resolved, resolved

        for candidate in (raw_syncgroup_id, raw_group_id):
            if not candidate:
                continue
            if candidate.startswith(("up", "media_player.", "ma_")):
                return raw_player_id, candidate
            if candidate.startswith("syncgroup_"):
                ma_group = get_ma_group_by_id(candidate)
                members = {str(m.get("id", "")) for m in (ma_group or {}).get("members", [])}
                if any(queue_id in members for queue_id in solo_queue_ids):
                    return candidate, candidate

        if solo_queue_id:
            return raw_player_id, solo_queue_id

    for candidate in (raw_syncgroup_id, raw_group_id):
        if not candidate:
            continue
        ma_group = get_ma_group_by_id(candidate)
        if ma_group and ma_group.get("id"):
            resolved = ma_group["id"]
            return resolved, resolved
        if candidate.startswith("syncgroup_"):
            return candidate, candidate
        if candidate.startswith(("up", "media_player.", "ma_")):
            return (raw_player_id or candidate), candidate

    if player_id:
        return raw_player_id, raw_player_id

    groups = get_ma_groups()
    if not groups:
        return None, None
    first_group = groups[0] if isinstance(groups[0], dict) else {}
    first_id = first_group.get("id")
    return first_id, first_id


def _build_ma_prediction_patch(action: str, value) -> dict:
    """Build a small predicted state patch for fast UI feedback."""
    if action == "shuffle":
        return {"shuffle": bool(value)}
    if action == "repeat":
        return {"repeat": str(value or "off")}
    if action == "seek":
        try:
            return {"elapsed": int(value)}
        except (TypeError, ValueError):
            return {}
    return {}


class _ArtworkRedirectHandler(_ur.HTTPRedirectHandler):
    """Follow artwork redirects, but never carry the MA token off-origin.

    ``urllib``'s default handler copies request headers onto the redirected
    request, and the artwork HMAC only covers the *initial* URL — so a
    MA-origin URL that 302s to a provider CDN used to deliver the bridge's
    bearer token to that CDN.  Redirects still work; the credential simply
    stops at the origin it was issued for.
    """

    def __init__(self, ma_url: str):
        self._origin = _origin_of(ma_url)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        if _origin_of(newurl) != self._origin:
            new_req.remove_header("Authorization")
            # ``Request`` lowercases unredirected headers separately.
            new_req.unredirected_hdrs.pop("Authorization", None)
        return new_req


def _origin_of(url: str) -> tuple[str, str]:
    """Return the ``(scheme, netloc)`` origin of *url*, case-folded."""
    parsed = _up.urlparse(url)
    return (parsed.scheme.lower(), parsed.netloc.lower())


def _resolve_ma_artwork_url(raw_url: str) -> tuple[str, bool]:
    """Resolve a raw artwork path/URL and report whether it targets the MA origin."""
    ma_url, _token = get_ma_api_credentials()
    if not ma_url:
        raise ValueError("MA API URL is not configured")

    trimmed = raw_url.strip()
    parsed_raw = _up.urlparse(trimmed)
    base_parsed = _up.urlparse(ma_url)
    if parsed_raw.scheme and parsed_raw.scheme.lower() not in ("http", "https"):
        raise ValueError("Unsupported artwork URL scheme")

    if not parsed_raw.scheme and not parsed_raw.netloc:
        # Pure relative path (e.g. ``/imageproxy/...``) — genuinely resolves
        # against the MA base and is same-origin.
        base = ma_url.rstrip("/") + "/"
        return _up.urljoin(base, trimmed), True

    # Absolute or scheme-relative (``//host/...``).  A scheme-relative URL
    # carries a *different* netloc while lacking a scheme; joining it against
    # the base swaps the host, so it must be classified by the resolved
    # origin — never assumed same-origin, or the artwork proxy would HMAC-sign
    # a foreign host as if it were MA.
    resolved = trimmed if parsed_raw.scheme else _up.urljoin(ma_url, trimmed)
    parsed = _up.urlparse(resolved)
    is_ma_origin = (parsed.scheme.lower(), parsed.netloc.lower()) == (
        base_parsed.scheme.lower(),
        base_parsed.netloc.lower(),
    )
    return resolved, is_ma_origin


# Artwork a host refused, remembered so a refused image is not asked for on
# every status refresh: {url: (status, retry_at)}.
_ARTWORK_FAILURE_TTL_S = 300.0
_artwork_failures: dict[str, tuple[int, float]] = {}
_artwork_failures_lock = threading.Lock()


def _reset_artwork_failures() -> None:
    with _artwork_failures_lock:
        _artwork_failures.clear()


def _artwork_failure(url: str) -> int | None:
    with _artwork_failures_lock:
        entry = _artwork_failures.get(url)
        if entry is None:
            return None
        status, retry_at = entry
        if time.monotonic() >= retry_at:
            del _artwork_failures[url]
            return None
        return status


def _remember_artwork_failure(url: str, status: int) -> bool:
    """Record a refusal; ``True`` when it is new (worth a log line)."""
    with _artwork_failures_lock:
        new = url not in _artwork_failures
        if len(_artwork_failures) > 256:
            _artwork_failures.clear()
        _artwork_failures[url] = (status, time.monotonic() + _ARTWORK_FAILURE_TTL_S)
        return new


def _artwork_user_agent() -> str:
    # Wikimedia, where many radio logos live, refuses urllib's default agent.
    from sendspin_bridge.config import VERSION

    return f"sendspin-bt-bridge/{VERSION} (+https://github.com/trudenboy/sendspin-bt-bridge)"


def _artwork_unavailable(status: int) -> UseCaseError:
    return UseCaseError(
        status,
        "artwork_unavailable",
        "Artwork unavailable",
        headers={"Cache-Control": f"private, max-age={int(_ARTWORK_FAILURE_TTL_S)}"},
    )


def now_playing() -> dict[str, Any]:
    """What Music Assistant is playing; ``{"connected": false}`` without MA."""
    if not is_ma_connected():
        return {"connected": False}
    return get_ma_now_playing()


def fetch_artwork(raw_url: str, signature: str) -> tuple[bytes, str]:
    """Fetch signed artwork for the UI (same-origin image URLs). Returns ``(body, content_type)``.

    The HMAC signature prevents arbitrary-URL SSRF; the MA token is sent only
    to the MA origin and never follows a redirect off it.
    """
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise UseCaseError(400, "url_required", "Missing artwork URL")
    if not has_valid_artwork_signature(raw_url, (signature or "").strip()):
        raise UseCaseError(400, "invalid_signature", "Invalid artwork signature")
    try:
        artwork_url, is_ma_origin = _resolve_ma_artwork_url(raw_url)
    except ValueError as exc:
        raise UseCaseError(400, "invalid_url", str(exc)) from exc
    parsed = _up.urlsplit(artwork_url)
    # ``%`` is safe: an already-encoded path (Wikimedia logos) must not be encoded twice.
    artwork_url = _up.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            _up.quote(parsed.path, safe="/:@!$&'()*+,;=-._~%"),
            parsed.query,
            parsed.fragment,
        )
    )
    refused = _artwork_failure(artwork_url)
    if refused is not None:
        raise _artwork_unavailable(refused)
    ma_url, ma_token = get_ma_api_credentials()
    req = _ur.Request(artwork_url, headers={"Accept": "image/*", "User-Agent": _artwork_user_agent()})
    if is_ma_origin and ma_token:
        req.add_header("Authorization", f"Bearer {ma_token}")
    opener = safe_build_opener(_ArtworkRedirectHandler(ma_url or ""))
    try:
        with opener.open(req, timeout=15) as resp:
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > _ARTWORK_MAX_BYTES:
                raise UseCaseError(413, "too_large", "Artwork too large")
            body = resp.read(_ARTWORK_MAX_BYTES + 1)
            if len(body) > _ARTWORK_MAX_BYTES:
                raise UseCaseError(413, "too_large", "Artwork too large")
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            if not content_type.startswith("image/"):
                content_type = "application/octet-stream"
            return body, content_type
    except _ue.HTTPError as exc:
        if _remember_artwork_failure(artwork_url, exc.code):
            logger.warning("MA artwork proxy HTTP %s for %s", exc.code, artwork_url)
        raise _artwork_unavailable(exc.code) from exc
    except UseCaseError:
        raise
    except Exception as exc:
        logger.exception("MA artwork proxy failed for %s", artwork_url)
        raise UseCaseError(502, "artwork_unavailable", "Artwork unavailable") from exc


def start_queue_command(
    action: QueueAction,
    value: Any = None,
    *,
    device_id: str | None = None,
    syncgroup_id: str | None = None,
    group_id: str | None = None,
) -> tuple[Job, dict[str, Any]]:
    """Send next/previous/shuffle/repeat/seek to the right MA queue.

    The UI gets a predicted now-playing state at once (second element);
    the job confirms or rolls it back.
    """
    if not is_ma_connected():
        raise UseCaseError(503, "ma_unavailable", "MA not connected")
    state_key, target_queue_id = _resolve_target_queue(syncgroup_id, device_id, group_id)
    if not state_key or not target_queue_id:
        raise UseCaseError(503, "queue_unavailable", "No MA queue available")
    raw_player_id = str(device_id or "").strip()
    target_player_id = raw_player_id or (
        target_queue_id if target_queue_id and not str(target_queue_id).startswith("up") else None
    )
    loop = bridge_loop()
    from sendspin_bridge.services.music_assistant.ma_monitor import get_monitor, request_queue_refresh, send_queue_cmd

    monitor = get_monitor()
    if monitor is None or not monitor.is_connected():
        raise UseCaseError(503, "monitor_unavailable", "MA monitor unavailable")
    op_id = uuid.uuid4().hex
    predicted = apply_ma_now_playing_prediction(
        state_key, _build_ma_prediction_patch(action, value), op_id=op_id, action=action, value=value
    )

    def _work(_ctx: JobContext):
        try:
            result = await_loop_result(
                loop,
                send_queue_cmd(action, value, target_queue_id, player_id=target_player_id),
                timeout=5.0,
                description=f"MA queue cmd {action}",
            )
        except Exception as exc:
            fail_ma_pending_op(state_key or target_queue_id, op_id, str(exc))
            raise
        if not result or not result.get("accepted"):
            error = (result or {}).get("error") or "MA command was not accepted"
            fail_ma_pending_op(state_key or target_queue_id, op_id, error)
            raise UseCaseError(409, "command_rejected", error)
        accepted_queue_id = str(result.get("queue_id") or target_queue_id)
        confirmed_state = apply_ma_now_playing_prediction(
            state_key,
            {},
            op_id=op_id,
            action=action,
            value=value,
            accepted_at=result.get("accepted_at"),
            ack_latency_ms=result.get("ack_latency_ms"),
        )
        await_loop_result(loop, request_queue_refresh(accepted_queue_id), timeout=1.0, description="MA queue refresh")
        return {
            "op_id": op_id,
            "syncgroup_id": state_key,
            "queue_id": accepted_queue_id,
            "accepted_at": result.get("accepted_at"),
            "ack_latency_ms": result.get("ack_latency_ms"),
            "ma_now_playing": confirmed_state,
        }

    job = jobs.start("music_assistant.queue_command", _work, subject=str(target_queue_id), progress={"action": action})
    return job, {"op_id": op_id, "syncgroup_id": state_key, "queue_id": target_queue_id, "ma_now_playing": predicted}
