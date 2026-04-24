"""Native BlueZ authentication agent for pairing flows.

Replaces the bluetoothctl ``agent on`` + stdin-``yes`` race that loses to
BlueZ's internal agent timeout on some speakers (issue #168, Synergy 65 S).
A ``PairingAgent`` exports ``org.bluez.Agent1`` on the system bus for the
duration of a pair attempt, auto-confirms SSP passkey prompts (Numeric
Comparison via ``DisplayYesNo`` capability) and supplies a legacy PIN when
asked.

Usage::

    with PairingAgent(capability="DisplayYesNo", pin="0000") as agent:
        ...run bluetoothctl pair without agent on...
    # agent unregistered cleanly here
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

AGENT_PATH = "/io/sendspin/bridge/PairingAgent"
_BLUEZ_BUS = "org.bluez"
_AGENT_MANAGER_PATH = "/org/bluez"
_AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"

_VALID_CAPABILITIES = {
    "DisplayOnly",
    "DisplayYesNo",
    "KeyboardOnly",
    "NoInputNoOutput",
    "KeyboardDisplay",
}


def _build_agent_iface(pin: str):
    """Build the org.bluez.Agent1 service interface object.

    Constructed lazily so ``import services.pairing_agent`` stays cheap
    (and importable) on systems without ``dbus_fast``.
    """
    from dbus_fast.service import ServiceInterface, method  # type: ignore

    class _Agent(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")
            self.pin: str = pin
            self.pin_attempted: bool = False
            self.cancelled: bool = False
            self.last_passkey: int | None = None

        @method()
        def Release(self):  # type: ignore[no-untyped-def]
            logger.debug("BlueZ released pairing agent")

        @method()
        def RequestPinCode(self, device: "o") -> "s":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.RequestPinCode(%s) → '%s'", device, self.pin)
            self.pin_attempted = True
            return self.pin

        @method()
        def RequestPasskey(self, device: "o") -> "u":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.RequestPasskey(%s) → 0", device)
            return 0

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.DisplayPasskey(%s): %06d (entered=%d)", device, passkey, entered)

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.DisplayPinCode(%s): %s", device, pincode)

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            self.last_passkey = int(passkey)
            logger.info(
                "Agent.RequestConfirmation(%s): auto-confirming passkey %06d",
                device,
                passkey,
            )

        @method()
        def RequestAuthorization(self, device: "o"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.RequestAuthorization(%s): auto-authorize", device)

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            logger.info("Agent.AuthorizeService(%s, %s): auto-authorize", device, uuid)

        @method()
        def Cancel(self):  # type: ignore[no-untyped-def]
            logger.warning("Agent.Cancel — peer cancelled pairing")
            self.cancelled = True

    return _Agent()


class PairingAgent:
    """Context manager that hosts a BlueZ auth agent for a single pair."""

    def __init__(self, *, capability: str = "DisplayYesNo", pin: str = "0000") -> None:
        if capability not in _VALID_CAPABILITIES:
            raise ValueError(f"Invalid agent capability: {capability}")
        self._capability = capability
        self._pin = pin
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Typed as Any so mypy doesn't require an eager dbus_fast import at
        # module level (the import is deliberately lazy inside _register).
        self._bus: Any = None
        self._iface: Any = None
        self._ready = threading.Event()
        self._start_error: BaseException | None = None

    @property
    def capability(self) -> str:
        return self._capability

    @property
    def was_cancelled(self) -> bool:
        return bool(self._iface and self._iface.cancelled)

    @property
    def pin_attempted(self) -> bool:
        return bool(self._iface and self._iface.pin_attempted)

    def __enter__(self) -> PairingAgent:
        self._thread = threading.Thread(target=self._thread_main, name="pair-agent", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            self._force_stop()
            raise RuntimeError("PairingAgent did not register within 5s")
        if self._start_error is not None:
            err = self._start_error
            self._force_stop()
            raise RuntimeError(f"PairingAgent start failed: {err}") from err
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._force_stop()

    def _force_stop(self) -> None:
        loop = self._loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def _thread_main(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            loop.run_until_complete(self._register())
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(self._unregister())
            except Exception as exc:
                logger.debug("PairingAgent unregister failed (non-fatal): %s", exc)
            loop.close()
            self._loop = None

    async def _register(self) -> None:
        from dbus_fast import BusType  # type: ignore
        from dbus_fast.aio import MessageBus  # type: ignore

        self._iface = _build_agent_iface(self._pin)
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._bus.export(AGENT_PATH, self._iface)
        introspect = await self._bus.introspect(_BLUEZ_BUS, _AGENT_MANAGER_PATH)
        proxy = self._bus.get_proxy_object(_BLUEZ_BUS, _AGENT_MANAGER_PATH, introspect)
        mgr = proxy.get_interface(_AGENT_MANAGER_IFACE)
        await mgr.call_register_agent(AGENT_PATH, self._capability)
        await mgr.call_request_default_agent(AGENT_PATH)
        logger.info(
            "PairingAgent registered (path=%s capability=%s)",
            AGENT_PATH,
            self._capability,
        )

    async def _unregister(self) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            introspect = await bus.introspect(_BLUEZ_BUS, _AGENT_MANAGER_PATH)
            mgr = bus.get_proxy_object(_BLUEZ_BUS, _AGENT_MANAGER_PATH, introspect).get_interface(_AGENT_MANAGER_IFACE)
            await mgr.call_unregister_agent(AGENT_PATH)
        finally:
            bus.disconnect()
            try:
                await bus.wait_for_disconnect()
            except Exception:
                pass
        self._bus = None
