"""A short window in which a phone can be paired to the bridge as an audio source (#563).

Pairing a phone is not the bridge's usual "pair this speaker" flow: the phone is the one that
starts it, on whichever adapter it reaches, and it needs an agent that confirms without a PIN.
The window opens discoverability and pairable mode for a couple of minutes and restores them
afterwards, so the adapter is not left visible on the air.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

PAIRING_WINDOW_S = 120
_CONTROLLER_RE = re.compile(r"Controller\s+([0-9A-Fa-f:]{17})", re.IGNORECASE)

#: The one open window, if any (the API and the status read it from any thread).
_state: dict[str, Any] = {"until": 0.0, "adapters": [], "error": None}
_task: asyncio.Task[None] | None = None
_agent: Any = None


def pairing_status() -> dict[str, Any]:
    """Seconds left in the pairing window (0 when closed) and the adapters it opened."""
    left = max(0, int(_state["until"] - time.time())) if _state["until"] else 0
    return {"pairing_seconds_left": left, "pairing_adapters": list(_state["adapters"]), "error": _state["error"]}


def _run_bluetoothctl(commands: list[str]) -> tuple[bool, str]:
    """Run bluetoothctl with commands on stdin (it reads them non-interactively)."""
    try:
        proc = subprocess.run(
            ["bluetoothctl"],
            input="\n".join(commands) + "\nexit\n",
            capture_output=True,
            text=True,
            timeout=20,
        )
        return proc.returncode == 0, proc.stdout + proc.stderr
    except Exception as exc:  # pragma: no cover - depends on the host
        return False, str(exc)


def _adapters() -> list[str]:
    ok, out = _run_bluetoothctl(["list"])
    return _CONTROLLER_RE.findall(out) if ok else []


def _set_discoverable(adapters: list[str], on: bool) -> bool:
    """Make every given adapter discoverable and pairable (or stop being discoverable)."""
    ok = True
    for mac in adapters:
        commands = [f"select {mac}", f"discoverable {'on' if on else 'off'}"]
        if on:
            commands.append("pairable on")
        good, out = _run_bluetoothctl(commands)
        if not good:
            logger.warning("Bluetooth pairing window: %s refused the setting: %s", mac, out.strip()[:200])
            ok = False
    return ok


async def open_window(seconds: int = PAIRING_WINDOW_S) -> dict[str, Any]:
    """Open discoverable/pairable adapters and host a no-PIN agent until the window closes."""
    global _task, _agent
    if _state["until"] > time.time() + 5:
        return pairing_status()
    adapters = await asyncio.to_thread(_adapters)
    if not adapters:
        _state.update(until=0.0, adapters=[], error="no_adapter")
        return pairing_status()
    await asyncio.to_thread(_set_discoverable, adapters, True)
    if _agent is None:
        from sendspin_bridge.services.bluetooth.pairing_agent import PairingAgent

        agent = PairingAgent(capability="DisplayYesNo")
        try:
            await asyncio.to_thread(agent.__enter__)
        except Exception:
            logger.warning("Bluetooth pairing window: the agent could not register", exc_info=True)
            await asyncio.to_thread(_set_discoverable, adapters, False)
            _state.update(until=0.0, adapters=[], error="no_agent")
            return pairing_status()
        _agent = agent
    _state.update(until=time.time() + max(30, int(seconds)), adapters=adapters, error=None)
    logger.info("Bluetooth pairing window open for %d s on %s", seconds, ", ".join(adapters))
    _task = asyncio.create_task(_close_after(seconds), name="source-pairing-window")
    return pairing_status()


async def _close_after(seconds: int) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(seconds)
        await close_window()


async def close_window() -> dict[str, Any]:
    """Close the window: stop being discoverable and unregister the agent."""
    global _task, _agent
    if _task is not None and _task is not asyncio.current_task():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None
    adapters = list(_state["adapters"])
    if adapters:
        await asyncio.to_thread(_set_discoverable, adapters, False)
    agent, _agent = _agent, None
    if agent is not None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(agent.__exit__, None, None, None)
    if adapters:
        logger.info("Bluetooth pairing window closed")
    _state.update(until=0.0, adapters=[])
    return pairing_status()
