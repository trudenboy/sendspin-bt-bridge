"""
Transport API Blueprint — native Sendspin transport commands.

Provides POST /api/transport/cmd for play/pause/stop/next/previous/shuffle/repeat
via the Sendspin Controller role, bypassing the MA REST API for lower latency.
"""

import asyncio
import logging

from flask import Blueprint, jsonify, request

from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

logger = logging.getLogger(__name__)

transport_bp = Blueprint("transport", __name__)

_VALID_ACTIONS = frozenset(
    {
        "play",
        "pause",
        "stop",
        "next",
        "previous",
        "volume",
        "mute",
        "repeat_off",
        "repeat_one",
        "repeat_all",
        "shuffle",
        "unshuffle",
        "switch",
    }
)


@transport_bp.route("/api/transport/cmd", methods=["POST"])
def transport_cmd():
    """Send a native Sendspin transport command to a device.

    Body JSON:
        action: str — one of the valid transport actions
        player_id: str — unique device player_id (UUID5 of MAC,
                          derived via ``_player_id_from_mac``).  Resilient
                          to device reorder / disable / online-add —
                          unlike the deprecated ``device_index`` path
                          which mis-routed Next/Pause to the wrong
                          device after ``active_clients`` order
                          diverged from frontend ``lastDevices`` order.
        device_index: int (deprecated, fallback) — index into the device list.
        value: any (optional) — for volume (0-100) or mute (bool)
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        # JSON top-level must be an object — guard against `null`, lists,
        # scalars, or invalid JSON (silent=True returns None) before
        # calling .get() on the body.
        return jsonify(success=False, error="Request body must be a JSON object"), 400
    action = str(data.get("action", "")).strip()
    if not action or action not in _VALID_ACTIONS:
        return jsonify(success=False, error=f"Invalid action: {action!r}"), 400

    registry = get_device_registry_snapshot()
    clients = registry.active_clients

    # Prefer player_id lookup — unique per device (UUID5 of MAC), stable
    # across reorder / disable / online-add.
    player_id = str(data.get("player_id") or "").strip()
    if player_id:
        client = next((c for c in clients if getattr(c, "player_id", None) == player_id), None)
        if client is None:
            return jsonify(success=False, error=f"Unknown player_id: {player_id}"), 404
    else:
        # Legacy index-based path — kept for backward compat with any
        # caller that hasn't been updated yet.  Logs a warning so we
        # can spot stragglers.
        try:
            device_index = int(data.get("device_index", -1))
        except (ValueError, TypeError):
            return jsonify(success=False, error="Invalid device_index"), 400
        if device_index < 0 or device_index >= len(clients):
            return jsonify(success=False, error="Device not found"), 404
        client = clients[device_index]
        logger.warning(
            "Transport command via deprecated device_index=%d (no player_id) — "
            "client resolved to %r; update the caller to pass player_id",
            device_index,
            getattr(client, "player_name", "?"),
        )
    if client is None:
        return jsonify(success=False, error="Device client unavailable"), 503

    # Check that the device has the command in its supported set
    supported = client.status.get("supported_commands")
    if supported is not None and action not in supported:
        return jsonify(success=False, error=f"Command {action!r} not supported by device"), 400

    loop = get_main_loop()
    if loop is None:
        return jsonify(success=False, error="Event loop not available"), 503

    value = data.get("value")
    try:
        future = asyncio.run_coroutine_threadsafe(client.send_transport_command(action, value=value), loop)
        result = future.result(timeout=5.0)
    except Exception as exc:
        logger.warning("Transport command %s failed: %s", action, exc)
        return jsonify(success=False, error=str(exc)), 500

    if not result:
        return jsonify(success=False, error="Daemon subprocess not running"), 503

    return jsonify(success=True)
