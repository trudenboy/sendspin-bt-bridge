"""Shared helpers for route handlers."""

from __future__ import annotations

import re

from flask import jsonify

from state import clients as _clients
from state import clients_lock as _clients_lock

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def validate_mac(mac: str) -> bool:
    """Return True if mac is a valid XX:XX:XX:XX:XX:XX Bluetooth MAC address."""
    return bool(_MAC_RE.fullmatch(mac))


def get_client_or_error(player_name: str | None):
    """Look up a SendspinClient by player_name.

    Returns ``(client, None)`` on success or ``(None, (response, status))``
    on failure.  Single-device shortcut: if *player_name* is ``None`` and
    exactly one client is configured, that client is returned.
    """
    with _clients_lock:
        snapshot = list(_clients)
    if not snapshot:
        return None, (jsonify({"success": False, "error": "No clients configured"}), 503)
    if player_name:
        client = next(
            (c for c in snapshot if getattr(c, "player_name", None) == player_name),
            None,
        )
        if client is None:
            return None, (jsonify({"success": False, "error": f"Unknown player: {player_name}"}), 400)
        return client, None
    if len(snapshot) == 1:
        return snapshot[0], None
    return None, (jsonify({"success": False, "error": "player_name is required"}), 400)
