"""HA-integration REST surface (v2.65.0+).

Endpoints used by the HA custom_component (Path A1) and the bridge web
UI's "Home Assistant" panel:

  * ``GET /api/ha/state``           — full ``HAStateProjection`` snapshot
  * ``GET /api/status/events``      — typed-event SSE channel
  * ``GET /api/ha/mqtt/probe``      — auto-detect HAOS MQTT add-on creds
  * ``GET /api/ha/mqtt/status``     — publisher diagnostics

Auth: every endpoint requires either a Flask session (web UI) or a
bearer token (custom_component) — enforcement happens in
``web_interface._check_auth``, so route handlers themselves do nothing
extra.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger(__name__)

ha_bp = Blueprint("ha_integration", __name__)


# ---------------------------------------------------------------------------
# /api/ha/state — dehydrated projection bootstrap
# ---------------------------------------------------------------------------


def _build_projection_for_request():
    """Build a fresh ``HAStateProjection`` from the live bridge snapshot."""
    from config import ensure_bridge_name, get_runtime_version, load_config
    from services.ha_state_projector import project_snapshot
    from services.status_snapshot import build_bridge_snapshot
    from state import get_clients_snapshot

    config = load_config()
    bridge_name = ensure_bridge_name(config)
    bridge_id = hashlib.sha1(bridge_name.encode("utf-8")).hexdigest()[:12]
    snapshot = build_bridge_snapshot(get_clients_snapshot())
    return project_snapshot(
        snapshot,
        bridge_id=bridge_id,
        bridge_name=bridge_name,
        runtime_extras={"version": get_runtime_version()},
    )


@ha_bp.route("/api/ha/state", methods=["GET"])
def api_ha_state():
    """Return the full HA entity-state projection.

    Used by the custom_component coordinator's first refresh — the SSE
    event channel covers updates afterward.  Errors are non-fatal: a
    coordinator that gets a 5xx falls back to its prior cached state.
    """
    try:
        projection = _build_projection_for_request()
        return jsonify(projection.to_json())
    except Exception:
        logger.exception("Failed to build HA state projection")
        return jsonify({"error": "Failed to build projection"}), 500


# ---------------------------------------------------------------------------
# /api/status/events — typed event SSE channel
# ---------------------------------------------------------------------------


_EVENT_SSE_MAX_LIFETIME = 6 * 60 * 60  # 6 hours
_EVENT_QUEUE_MAXSIZE = 256


@ha_bp.route("/api/status/events", methods=["GET"])
def api_status_events():
    """Server-Sent Events stream of typed ``InternalEvent`` records.

    Reuses the SSE concurrency budget already enforced by
    ``routes/api_status.py``.  Each ``InternalEvent`` becomes one SSE
    event with ``event:<event_type>`` and ``data:<json>``.
    """
    # Borrow the existing budget counter — the HA coordinator opens both
    # /api/status/stream (snapshots) and /api/status/events (deltas).
    import routes.api_status as status_module
    from routes.api_status import _MAX_SSE, _sse_lock

    with _sse_lock:
        if status_module._sse_count >= _MAX_SSE:
            return (
                'event: error\ndata: {"error": "too many listeners"}\n\n',
                503,
                {"Content-Type": "text/event-stream"},
            )
        status_module._sse_count += 1

    from state import get_internal_event_publisher

    publisher = get_internal_event_publisher()
    msg_q: queue.Queue = queue.Queue(maxsize=_EVENT_QUEUE_MAXSIZE)

    def _on_event(event: Any) -> None:
        try:
            msg_q.put_nowait(event)
        except queue.Full:
            # Drop oldest to make room — the coordinator will resync on
            # heartbeat republish.
            try:
                msg_q.get_nowait()
                msg_q.put_nowait(event)
            except (queue.Empty, queue.Full):
                pass

    unsubscribe = publisher.subscribe(_on_event)

    def _generate():
        try:
            # Warm-up flush so HA ingress + nginx don't buffer.
            yield ": " + " " * 2048 + "\n\n"
            yield 'event: ready\ndata: {"ready": true}\n\n'

            started = time.monotonic()
            while True:
                if time.monotonic() - started >= _EVENT_SSE_MAX_LIFETIME:
                    yield 'event: expired\ndata: {"expired": true}\n\n'
                    break
                try:
                    event = msg_q.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue

                payload = {
                    "event_type": getattr(event, "event_type", ""),
                    "category": getattr(event, "category", ""),
                    "subject_id": getattr(event, "subject_id", ""),
                    "payload": dict(getattr(event, "payload", {}) or {}),
                    "at": getattr(event, "at", ""),
                }
                yield f"event: {payload['event_type']}\n"
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            try:
                unsubscribe()
            except Exception:  # pragma: no cover
                pass
            with _sse_lock:
                status_module._sse_count -= 1

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Content-Encoding": "identity",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# /api/ha/mqtt/probe — Supervisor MQTT auto-detect
# ---------------------------------------------------------------------------


@ha_bp.route("/api/ha/mqtt/probe", methods=["GET"])
def api_ha_mqtt_probe():
    """Probe Supervisor for the MQTT add-on broker credentials.

    Used by the web UI's "Auto-detect MQTT add-on" button.  Returns the
    credentials WITHOUT the password (the password lives in config.json
    after a save) and a ``found`` flag.
    """
    try:
        from services.ha_addon import get_mqtt_addon_credentials

        creds = get_mqtt_addon_credentials()
    except Exception as exc:  # pragma: no cover
        logger.exception("MQTT probe failed")
        return jsonify({"found": False, "error": str(exc)}), 500

    if creds is None:
        return jsonify(
            {
                "found": False,
                "hint": "Install and start the official Mosquitto add-on, then try again.",
            }
        )
    # Mask the password — operator can paste it manually if they want.
    return jsonify(
        {
            "found": True,
            "host": creds.get("host"),
            "port": creds.get("port"),
            "username": creds.get("username"),
            "password_present": bool(creds.get("password")),
            "ssl": creds.get("ssl"),
        }
    )


# ---------------------------------------------------------------------------
# /api/ha/mosquitto/status — Mosquitto add-on install state
# ---------------------------------------------------------------------------


@ha_bp.route("/api/ha/mosquitto/status", methods=["GET"])
def api_ha_mosquitto_status():
    """Read-only snapshot of the Mosquitto add-on state on HAOS.

    Used by the web UI to decide whether to show the "Install Mosquitto"
    install banner, the "Start Mosquitto" hint, or the auto-configure
    CTA.  Outside HA addon mode the response has ``available=false`` so
    the UI hides the banner.
    """
    try:
        from services.ha_addon import get_mosquitto_addon_state

        return jsonify(get_mosquitto_addon_state())
    except Exception as exc:  # pragma: no cover
        logger.exception("Mosquitto status query failed")
        # Reuse the canonical constants instead of duplicating them.  Set
        # ``available`` from SUPERVISOR_TOKEN even on the error path so
        # the UI keeps showing the banner (with the error context) when
        # we're inside HA addon mode — otherwise the operator loses the
        # actionable hint and just sees a silent missing banner.
        import os as _os

        from services.ha_addon import MOSQUITTO_ADDON_DEEP_LINK, MOSQUITTO_ADDON_SLUG

        return jsonify(
            {
                "available": bool(_os.environ.get("SUPERVISOR_TOKEN", "").strip()),
                "installed": False,
                "started": False,
                "slug": MOSQUITTO_ADDON_SLUG,
                "install_url": MOSQUITTO_ADDON_DEEP_LINK,
                "error": str(exc),
            }
        )


# ---------------------------------------------------------------------------
# /api/ha/mqtt/status — publisher diagnostics
# ---------------------------------------------------------------------------


@ha_bp.route("/api/ha/mqtt/status", methods=["GET"])
def api_ha_mqtt_status():
    """Read-only snapshot of the MQTT publisher state for the UI."""
    try:
        from services.ha_integration_lifecycle import get_default_lifecycle
        from services.ha_mqtt_publisher import publisher_status

        lifecycle = get_default_lifecycle()
        publisher = lifecycle.publisher if lifecycle is not None else None
        return jsonify(publisher_status(publisher))
    except Exception:
        logger.exception("MQTT status query failed")
        return jsonify(
            {
                "running": False,
                "state": "error",
                "broker": None,
                "discovery_payload_count": 0,
                "published_messages": 0,
                "last_error": "status query failed",
                "last_event_at": None,
            }
        )


# ---------------------------------------------------------------------------
# /api/ha/mdns/status — mDNS advertiser diagnostics
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/ha/command — unified command dispatch for the custom_component
# ---------------------------------------------------------------------------


@ha_bp.route("/api/ha/command", methods=["POST"])
def api_ha_command_device():
    """Dispatch a device-scoped command via ``HaCommandDispatcher``."""
    data = request.get_json(silent=True) or {}
    player_id = str(data.get("player_id") or "").strip()
    command = str(data.get("command") or "").strip()
    value = data.get("value")
    if not player_id:
        return jsonify({"success": False, "error": "player_id required"}), 400
    if not command:
        return jsonify({"success": False, "error": "command required"}), 400

    from services.ha_command_dispatcher import get_default_dispatcher

    result = get_default_dispatcher().dispatch_device(player_id, command, value)
    return jsonify(result.to_dict()), result.code if not result.success else 200


@ha_bp.route("/api/ha/command/bridge", methods=["POST"])
def api_ha_command_bridge():
    """Dispatch a bridge-scoped command via ``HaCommandDispatcher``."""
    data = request.get_json(silent=True) or {}
    command = str(data.get("command") or "").strip()
    value = data.get("value")
    if not command:
        return jsonify({"success": False, "error": "command required"}), 400

    from services.ha_command_dispatcher import get_default_dispatcher

    result = get_default_dispatcher().dispatch_bridge(command, value)
    return jsonify(result.to_dict()), result.code if not result.success else 200


@ha_bp.route("/api/ha/mdns/status", methods=["GET"])
def api_ha_mdns_status():
    try:
        from services.bridge_mdns import get_default_advertiser

        adv = get_default_advertiser()
        if adv is None or adv.advertisement is None:
            return jsonify({"advertised": False})
        return jsonify(
            {
                "advertised": True,
                "service_name": adv.advertisement.service_name,
                "host_id": adv.advertisement.host_id,
                "port": adv.advertisement.port,
                "txt_records": dict(adv.advertisement.txt_records),
            }
        )
    except Exception:
        logger.exception("mDNS status query failed")
        return jsonify({"advertised": False, "error": "status query failed"})
