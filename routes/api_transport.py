"""
Transport API Blueprint — native Sendspin transport commands.

Provides POST /api/transport/cmd for play/pause/stop/next/previous/shuffle/repeat
via the Sendspin Controller role, bypassing the MA REST API for lower latency.
"""

import asyncio
import logging

from flask import Blueprint, jsonify, request

from services.bridge_runtime_state import get_main_loop
from services.device_registry import get_device_registry_snapshot

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
        device_index: int — index into the device list
        value: any (optional) — for volume (0-100) or mute (bool)
    """
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip()
    if not action or action not in _VALID_ACTIONS:
        return jsonify(success=False, error=f"Invalid action: {action!r}"), 400

    try:
        device_index = int(data.get("device_index", -1))
    except (ValueError, TypeError):
        return jsonify(success=False, error="Invalid device_index"), 400

    registry = get_device_registry_snapshot()
    clients = registry.active_clients
    if device_index < 0 or device_index >= len(clients):
        return jsonify(success=False, error="Device not found"), 404

    client = clients[device_index]
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
