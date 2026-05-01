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
import secrets
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
    from sendspin_bridge.bridge.state import get_clients_snapshot
    from sendspin_bridge.config import ensure_bridge_name, get_runtime_version, load_config
    from sendspin_bridge.services.ha.ha_state_projector import project_snapshot
    from sendspin_bridge.services.lifecycle.status_snapshot import build_bridge_snapshot

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
    import sendspin_bridge.web.routes.api_status as status_module
    from sendspin_bridge.web.routes.api_status import _MAX_SSE, _sse_lock

    with _sse_lock:
        if status_module._sse_count >= _MAX_SSE:
            return (
                'event: error\ndata: {"error": "too many listeners"}\n\n',
                503,
                {"Content-Type": "text/event-stream"},
            )
        status_module._sse_count += 1

    from sendspin_bridge.bridge.state import get_internal_event_publisher

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
            # ``no-transform`` (RFC 7234 §5.2.2.4) keeps the HA
            # Supervisor ingress proxy from compressing the SSE chunks
            # — see ``/api/status/stream`` for the full rationale.
            "Cache-Control": "no-cache, no-transform",
            "Content-Encoding": "identity",
            "X-Accel-Buffering": "no",
            # NB: ``Connection`` is a hop-by-hop header (PEP 3333 /
            # RFC 2616 §13.5.1) and waitress raises AssertionError if
            # a WSGI app sets it.  Don't add it here.
        },
    )


# ---------------------------------------------------------------------------
# /api/ha/mqtt/probe — Supervisor MQTT auto-detect
# ---------------------------------------------------------------------------


@ha_bp.route("/api/ha/mqtt/probe", methods=["GET"])
def api_ha_mqtt_probe():
    """Probe for an MQTT broker the bridge can talk to.

    Used by the web UI's "Auto-detect MQTT add-on" button.  Two paths:

    1. **HA add-on (Supervisor available)** — query Supervisor for the
       Mosquitto add-on's full credentials (host, port, user, pass).
       Returns ``source: "supervisor"`` and ``password_present`` so the
       UI knows the secret is already on the bridge side.

    2. **Standalone (no Supervisor)** — derive a *suggested* broker
       host from the configured Music Assistant URL.  When MA runs as
       an HA add-on (the common harryfine-style topology — bridge in
       Docker, MA on HAOS), Mosquitto sits on the same host, so the
       MA host is a strong default.  Returns ``source: "ma_url"`` and
       ``password_present: false`` so the UI prompts for credentials.

    Returns the credentials *without* the password (the password lives
    in ``config.json`` after a save) and a ``found`` flag.
    """
    try:
        from sendspin_bridge.services.ha.ha_addon import (
            derive_mqtt_broker_from_ma_url,
            get_mqtt_addon_credentials,
        )

        creds = get_mqtt_addon_credentials()
    except Exception as exc:  # pragma: no cover
        logger.exception("MQTT probe failed")
        return jsonify({"found": False, "error": str(exc)}), 500

    if creds is not None:
        # Supervisor path: full credentials including password.
        return jsonify(
            {
                "found": True,
                "source": "supervisor",
                "host": creds.get("host"),
                "port": creds.get("port"),
                "username": creds.get("username"),
                "password_present": bool(creds.get("password")),
                "ssl": creds.get("ssl"),
            }
        )

    # Fallback: derive from MA URL on standalone deployments.
    try:
        from sendspin_bridge.config import load_config

        ma_api_url = str(load_config().get("MA_API_URL", "")).strip()
    except Exception:  # pragma: no cover
        ma_api_url = ""

    suggested = derive_mqtt_broker_from_ma_url(ma_api_url) if ma_api_url else None
    if suggested is not None:
        return jsonify(
            {
                "found": True,
                "source": "ma_url",
                "host": suggested["host"],
                "port": suggested["port"],
                "username": "",
                "password_present": False,
                "ssl": False,
                "hint": (
                    f"Suggested host {suggested['host']!r} taken from your Music Assistant URL. "
                    "Enter Mosquitto credentials below if your broker requires authentication "
                    "(anonymous-access brokers can be left blank)."
                ),
            }
        )

    # Nothing to suggest — neither Supervisor nor a configured MA URL.
    return jsonify(
        {
            "found": False,
            "source": None,
            "hint": (
                "Auto-detect needs either HA add-on mode (Supervisor) or a "
                "configured Music Assistant URL.  Enter the broker host and "
                "Mosquitto credentials manually."
            ),
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
        from sendspin_bridge.services.ha.ha_addon import get_mosquitto_addon_state

        return jsonify(get_mosquitto_addon_state())
    except Exception as exc:  # pragma: no cover
        logger.exception("Mosquitto status query failed")
        # Reuse the canonical constants instead of duplicating them.  Set
        # ``available`` from SUPERVISOR_TOKEN even on the error path so
        # the UI keeps showing the banner (with the error context) when
        # we're inside HA addon mode — otherwise the operator loses the
        # actionable hint and just sees a silent missing banner.
        import os as _os

        from sendspin_bridge.services.ha.ha_addon import MOSQUITTO_ADDON_DEEP_LINK, MOSQUITTO_ADDON_SLUG

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


@ha_bp.route("/api/ha/custom_component/status", methods=["GET"])
def api_ha_custom_component_status():
    """Heuristic install/active state for the HACS custom_component.

    Mirrors ``/api/ha/mosquitto/status`` so the UI can render the two
    transports' install indicators with the same code path.  See
    ``services.ha.ha_addon.get_custom_component_state`` for the
    detection logic (token-presence + last_used recency).
    """
    try:
        from sendspin_bridge.services.ha.ha_addon import get_custom_component_state

        return jsonify(get_custom_component_state())
    except Exception as exc:
        logger.exception("custom_component status query failed")
        return jsonify(
            {
                "available": True,
                "installed": False,
                "started": False,
                "last_seen": None,
                "install_url": "https://my.home-assistant.io/redirect/hacs_repository/?owner=trudenboy&repository=sendspin-bt-bridge&category=integration",
                "error": str(exc),
            }
        )


@ha_bp.route("/api/ha/mqtt/status", methods=["GET"])
def api_ha_mqtt_status():
    """Read-only snapshot of the MQTT publisher state for the UI."""
    try:
        from sendspin_bridge.services.ha.ha_integration_lifecycle import get_default_lifecycle
        from sendspin_bridge.services.ha.ha_mqtt_publisher import publisher_status

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
# /api/ha/mqtt/test — pre-save broker reachability + auth check
# ---------------------------------------------------------------------------


_MQTT_TEST_TCP_TIMEOUT_S = 5.0
_MQTT_TEST_CONNACK_TIMEOUT_S = 10.0
# Wire-protocol marker for "password unchanged" — see ``routes/api_config.py``
# for the matching round-trip logic on save.
_REDACTED_PASSWORD_MARKER = "***REDACTED***"


async def _probe_mqtt_broker(
    host: str,
    port: int,
    username: str,
    password: str,
    tls: bool,
) -> dict[str, Any]:
    """Run a one-shot reachability + auth probe against an MQTT broker.

    Mirrors the pre-flight + bounded-CONNACK pattern in
    ``ha_mqtt_publisher._serve``: TCP open via ``asyncio.open_connection``
    (asyncio-native, cancellable on timeout), then a full ``aiomqtt`` CONNACK
    round-trip inside ``asyncio.timeout``.  No retained state, no
    publishes — purely "do these credentials reach this broker".
    """
    import asyncio

    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_MQTT_TEST_TCP_TIMEOUT_S,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        del reader  # only needed to satisfy the open_connection return shape
    except (TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "error_class": type(exc).__name__,
            "error": f"broker {host}:{port} unreachable: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    try:
        import aiomqtt
    except ImportError as exc:
        return {
            "ok": False,
            "error_class": "ImportError",
            "error": f"aiomqtt not installed: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    # Use a unique-per-call identifier so two simultaneous tests (rapid
    # double-click, two operator browsers) can't collide on the broker
    # side and trigger spurious disconnects.  ``secrets.token_hex`` gives
    # us 64 random bits — far below the MQTT v3.1.1 23-byte client_id
    # limit, plenty for collision-free fanout.
    client = aiomqtt.Client(
        hostname=host,
        port=port,
        username=username or None,
        password=password or None,
        identifier=f"sendspin-test-{secrets.token_hex(8)}",
        tls_params=aiomqtt.TLSParameters() if tls else None,
        timeout=10,
    )
    try:
        async with asyncio.timeout(_MQTT_TEST_CONNACK_TIMEOUT_S):
            await client.__aenter__()
        try:
            return {"ok": True, "elapsed_ms": int((time.monotonic() - started) * 1000)}
        finally:
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
    except Exception as exc:
        return {
            "ok": False,
            "error_class": type(exc).__name__,
            "error": f"connect failed: {exc}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


def _resolve_redacted_password(submitted: str) -> str:
    """If the form submitted the redacted-password marker, resolve the
    actual password from saved config so testing host/user/TLS edits
    works without forcing the operator to retype the password."""
    if submitted != _REDACTED_PASSWORD_MARKER:
        return submitted
    try:
        from sendspin_bridge.config import load_config

        cfg = load_config()
        block = cfg.get("HA_INTEGRATION") or {}
        mqtt = block.get("mqtt") or {}
        return str(mqtt.get("password") or "")
    except Exception:
        logger.debug("Could not resolve redacted MQTT password from config", exc_info=True)
        return ""


@ha_bp.route("/api/ha/mqtt/test", methods=["POST"])
def api_ha_mqtt_test():
    """Probe an MQTT broker with the values currently in the form.

    Used by the HA tab's "Test connection" button so operators can
    iterate on broker / credentials without saving partial configs and
    watching the publisher fail in the status panel.

    Accepts a JSON body so the password (or the ``***REDACTED***``
    marker that triggers a saved-config lookup) is not exposed in URL
    query params, browser history, or proxy access logs:

      ``{"host": str, "port": int, "username": str,
         "password": str, "tls": bool}``

    Validation errors (missing ``host``, out-of-range ``port``) return
    HTTP 400 with the same error-shaped body the success path uses
    (``{"ok": false, "error_class", "error"}``).  Probe outcomes —
    success and runtime failures (TCP timeout, auth rejection, TLS
    mismatch) — return HTTP 200 with the result in the body so the UI
    can render the error class + message inline next to the form.
    """
    import asyncio

    payload = request.get_json(silent=True) or {}
    host = str(payload.get("host") or "").strip()
    if not host:
        return jsonify({"ok": False, "error_class": "ValueError", "error": "host required"}), 400
    try:
        port = int(payload.get("port") or 1883)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error_class": "ValueError", "error": "port must be an integer"}), 400
    if not (1 <= port <= 65535):
        return jsonify({"ok": False, "error_class": "ValueError", "error": "port out of range"}), 400
    username = str(payload.get("username") or "").strip()
    password = _resolve_redacted_password(str(payload.get("password") or ""))
    tls_raw = payload.get("tls")
    if isinstance(tls_raw, bool):
        tls = tls_raw
    else:
        tls = str(tls_raw or "").strip().lower() in ("1", "true", "yes", "on")

    try:
        result = asyncio.run(_probe_mqtt_broker(host, port, username, password, tls))
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("MQTT test probe crashed unexpectedly")
        result = {
            "ok": False,
            "error_class": type(exc).__name__,
            "error": f"probe crashed: {exc}",
        }
    return jsonify(result)


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

    from sendspin_bridge.services.ha.ha_command_dispatcher import get_default_dispatcher

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

    from sendspin_bridge.services.ha.ha_command_dispatcher import get_default_dispatcher

    result = get_default_dispatcher().dispatch_bridge(command, value)
    return jsonify(result.to_dict()), result.code if not result.success else 200


@ha_bp.route("/api/ha/mdns/status", methods=["GET"])
def api_ha_mdns_status():
    try:
        from sendspin_bridge.services.ipc.bridge_mdns import get_default_advertiser

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
