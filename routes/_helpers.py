"""Shared helpers for route handlers."""

from __future__ import annotations

import errno
import logging
import os
import re

from flask import jsonify

from services.device_registry import get_device_registry_snapshot

logger = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_ADAPTER_ID_RE = re.compile(r"^(hci\d+|[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5})$")


def config_write_error_response(exc: OSError, context: str | None = None):
    """Build a structured JSON 500 response when ``$CONFIG_DIR`` write fails.

    Distinguishes ``PermissionError`` / ``EROFS`` so operators get a
    targeted remediation hint instead of the generic "Internal Server
    Error" Flask defaults to.  Always logs the exception at ERROR level
    so the underlying traceback is preserved for triage.

    Returns the standard Flask ``(response, status_code)`` tuple — the
    handler can ``return config_write_error_response(exc)`` directly.
    """
    logger.exception("Config write failed: %s", exc)
    error_no = getattr(exc, "errno", None)
    config_dir = os.environ.get("CONFIG_DIR", "/config")
    runtime_uid = os.getuid()
    prefix = f"{context}: " if context else ""

    if error_no == errno.EACCES or isinstance(exc, PermissionError):
        # Wrong ownership — the canonical chown fix.  Issue #190.
        return jsonify(
            {
                "success": False,
                "error": (f"{prefix}{config_dir} is not writable by UID {runtime_uid} (permission denied)."),
                "remediation": {
                    "summary": "Bind-mount target needs to be owned by the bridge UID",
                    "fix": (f"On the host: chown -R {runtime_uid}:{os.getgid()} <bind-mount target for {config_dir}>"),
                    "details_url": ("https://github.com/trudenboy/sendspin-bt-bridge/issues/190"),
                },
            }
        ), 500

    if error_no == errno.EROFS:
        # No chown will help — operator must remount or move the path.
        return jsonify(
            {
                "success": False,
                "error": f"{prefix}{config_dir} is on a read-only file system.",
                "remediation": {
                    "summary": "Bind-mount must be writable for runtime config persistence",
                    "fix": (f"Remount {config_dir} read-write (rw), or move CONFIG_DIR to a writable path"),
                    "details_url": None,
                },
            }
        ), 500

    # Unknown OSError (ENOSPC, EIO, ...) — surface what we know without
    # promising a fix we don't have.
    return jsonify(
        {
            "success": False,
            "error": f"{prefix}config write failed: {exc}",
            "remediation": {
                "summary": "Inspect the bridge logs for the underlying error",
                "fix": "Check container logs (docker logs / journalctl) for the full traceback",
                "details_url": None,
            },
        }
    ), 500


def validate_mac(mac: str) -> bool:
    """Return True if mac is a valid XX:XX:XX:XX:XX:XX Bluetooth MAC address."""
    return bool(_MAC_RE.fullmatch(mac))


def validate_adapter(adapter: str | None) -> str:
    """Validate and return sanitized adapter identifier.

    Returns empty string if adapter is None/empty.
    Raises ValueError if adapter format is invalid.
    """
    if not adapter:
        return ""
    adapter = adapter.strip()
    if not adapter:
        return ""
    if not _ADAPTER_ID_RE.fullmatch(adapter):
        raise ValueError(f"Invalid adapter identifier: {adapter!r}")
    return adapter


def get_client_or_error(player_name: str | None):
    """Look up a SendspinClient by player_name.

    Returns ``(client, None)`` on success or ``(None, (response, status))``
    on failure.  Single-device shortcut: if *player_name* is ``None`` and
    exactly one client is configured, that client is returned.
    """
    snapshot = get_device_registry_snapshot().active_clients
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
