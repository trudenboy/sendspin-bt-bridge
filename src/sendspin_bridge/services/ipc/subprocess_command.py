"""Helpers for writing daemon stdin commands."""

from __future__ import annotations

import json
import logging
from typing import Any

from sendspin_bridge.services.ipc.ipc_protocol import build_command_envelope


class SubprocessCommandService:
    """Own daemon stdin command serialization and transport."""

    def __init__(self, *, logger_: logging.Logger | None = None):
        self._logger = logger_ or logging.getLogger(__name__)

    async def send(self, proc, cmd: dict[str, Any]) -> None:
        """Write one JSON command envelope to daemon stdin if the proc is alive."""
        stdin = proc.stdin if proc else None
        if proc and stdin and proc.returncode is None:
            try:
                command = str(cmd.get("cmd") or "").strip()
                if not command:
                    return
                payload = {key: value for key, value in cmd.items() if key != "cmd"}
                stdin.write((json.dumps(build_command_envelope(command, **payload)) + "\n").encode())
                await stdin.drain()
            except Exception as exc:
                self._logger.debug("Could not send subprocess command: %s", exc)
