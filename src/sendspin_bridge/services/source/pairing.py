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
_known_devices: set[str] = set()
_task: asyncio.Task[None] | None = None
_agent: Any = None
_AGENT_PATH = "/io/sendspin/bridge/SourceAgent"


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


def _devices() -> set[str]:
    """MACs BlueZ already knows (paired or seen)."""
    ok, out = _run_bluetoothctl(["devices"])
    if not ok:
        return set()
    return {m.group(1).upper() for m in re.finditer(r"Device\s+([0-9A-Fa-f:]{17})", out)}


def _trust(macs: set[str]) -> None:
    """Trust a device paired during the window: a trusted device skips agent authorization."""
    for mac in macs:
        good, out = _run_bluetoothctl([f"trust {mac}"])
        if good:
            logger.info("Bluetooth pairing window: trusted %s", mac)
        else:
            logger.warning("Bluetooth pairing window: could not trust %s: %s", mac, out.strip()[:160])


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


async def ensure_agent() -> bool:
    """Register the source's own agent if it is not registered yet, and keep it.

    A phone re-connects long after the pairing window: without an agent BlueZ answers pairing and
    profile authorization with a dead default agent (seen on this host as
    "Agent /org/bluez/agent replied with an error ... ServiceUnknown").
    """
    global _agent
    if _agent is not None:
        return True
    from sendspin_bridge.services.bluetooth.pairing_agent import PairingAgent

    agent = PairingAgent(capability="DisplayYesNo", path=_AGENT_PATH)
    try:
        await asyncio.to_thread(agent.__enter__)
    except Exception:
        logger.warning("Bluetooth source: the pairing agent could not register", exc_info=True)
        return False
    _agent = agent
    return True


async def close_agent() -> None:
    """Unregister the source's agent (the source itself is stopping)."""
    global _agent


async def open_window(seconds: int = PAIRING_WINDOW_S) -> dict[str, Any]:
    """Open discoverable/pairable adapters and host a no-PIN agent until the window closes."""
    global _task, _agent
    if _state["until"] > time.time() + 5:
        return pairing_status()
    adapters = await asyncio.to_thread(_adapters)
    if not adapters:
        _state.update(until=0.0, adapters=[], error="no_adapter")
        return pairing_status()
    global _known_devices
    _known_devices = await asyncio.to_thread(_devices)
    await asyncio.to_thread(_set_discoverable, adapters, True)
    if not await ensure_agent():
        await asyncio.to_thread(_set_discoverable, adapters, False)
        _state.update(until=0.0, adapters=[], error="no_agent")
        return pairing_status()
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
        # A phone paired in the window would lose its agent with the window; trusting it lets
        # A2DP connect afterwards without one.
        paired = await asyncio.to_thread(_devices)
        newly_paired = {mac for mac in paired - _known_devices if mac not in {"FC:58:FA:EB:08:6C"}}
        if newly_paired:
            await asyncio.to_thread(_trust, newly_paired)
    if adapters:
        logger.info("Bluetooth pairing window closed")
    _state.update(until=0.0, adapters=[])
    return pairing_status()
